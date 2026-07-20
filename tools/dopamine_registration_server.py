#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def run(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed:\n{result.stdout}")
    return result.stdout.strip()


def private_key(value: str) -> str:
    value = value.strip().replace("\\n", "\n")
    if "BEGIN PRIVATE KEY" in value:
        return value
    decoded = base64.b64decode("".join(value.split())).decode()
    if "BEGIN PRIVATE KEY" not in decoded:
        raise RuntimeError("ASC_PRIVATE_KEY is not a PEM private key")
    return decoded


class AppStoreConnect:
    def __init__(self) -> None:
        import jwt  # type: ignore

        self.jwt = jwt
        self.key_id = os.environ["ASC_KEY_ID"]
        self.issuer_id = os.environ["ASC_ISSUER_ID"]
        self.key = private_key(os.environ["ASC_PRIVATE_KEY"])
        self.base = "https://api.appstoreconnect.apple.com"

    def token(self) -> str:
        now = int(time.time())
        return self.jwt.encode(
            {"iss": self.issuer_id, "iat": now, "exp": now + 900, "aud": "appstoreconnect-v1"},
            self.key,
            algorithm="ES256",
            headers={"kid": self.key_id, "typ": "JWT"},
        )

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Authorization": f"Bearer {self.token()}", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"Apple API {method} {path} failed ({exc.code}): {detail}") from exc

    def all(self, path: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        next_path: str | None = path
        while next_path:
            response = self.request("GET", next_path)
            rows.extend(response.get("data", []))
            next_url = response.get("links", {}).get("next")
            next_path = None
            if next_url:
                parsed = urllib.parse.urlsplit(next_url)
                next_path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        return rows

    def ensure_device(self, *, udid: str, name: str) -> str:
        query = urllib.parse.urlencode({"filter[udid]": udid, "limit": "200"})
        existing = self.all(f"/v1/devices?{query}")
        if existing:
            return existing[0]["id"]
        payload = {
            "data": {
                "type": "devices",
                "attributes": {"name": name[:100], "platform": "IOS", "udid": udid},
            }
        }
        return self.request("POST", "/v1/devices", payload)["data"]["id"]

    def bundle_resource(self, bundle_id: str) -> str:
        query = urllib.parse.urlencode({"filter[identifier]": bundle_id, "limit": "200"})
        rows = self.all(f"/v1/bundleIds?{query}")
        if not rows:
            raise RuntimeError(f"Apple bundle ID not found: {bundle_id}")
        return rows[0]["id"]

    def certificate_resource(self, signing_sha1: str) -> str:
        query = urllib.parse.urlencode({"filter[certificateType]": "IOS_DEVELOPMENT", "limit": "200"})
        expected = signing_sha1.replace(" ", "").upper()
        for row in self.all(f"/v1/certificates?{query}"):
            content = row.get("attributes", {}).get("certificateContent")
            if content and hashlib.sha1(base64.b64decode(content)).hexdigest().upper() == expected:
                return row["id"]
        raise RuntimeError("The imported signing certificate was not found in App Store Connect")

    def enabled_devices(self) -> list[str]:
        query = urllib.parse.urlencode({"filter[platform]": "IOS", "filter[status]": "ENABLED", "limit": "200"})
        return [row["id"] for row in self.all(f"/v1/devices?{query}")]

    def profile(self, *, bundle: str, certificate: str, devices: list[str]) -> bytes:
        payload = {
            "data": {
                "type": "profiles",
                "attributes": {
                    "name": f"Dopamine Auto {int(time.time())}",
                    "profileType": "IOS_APP_DEVELOPMENT",
                },
                "relationships": {
                    "bundleId": {"data": {"type": "bundleIds", "id": bundle}},
                    "certificates": {"data": [{"type": "certificates", "id": certificate}]},
                    "devices": {"data": [{"type": "devices", "id": device} for device in devices]},
                },
            }
        }
        created = self.request("POST", "/v1/profiles", payload)
        return base64.b64decode(created["data"]["attributes"]["profileContent"])


class State:
    def __init__(self, args: argparse.Namespace) -> None:
        self.site = args.site.resolve()
        self.base_ipa = args.base_ipa.resolve()
        self.bundle_id = args.bundle_id
        self.version = args.version
        self.title = args.title
        self.identity = args.identity
        self.keychain = args.keychain
        self.repo_root = args.repo_root.resolve()
        self.base_url_file = args.base_url_file.resolve()
        self.challenge = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.apple = AppStoreConnect()

    def base_url(self) -> str:
        value = self.base_url_file.read_text().strip().rstrip("/")
        if not value.startswith("https://"):
            raise RuntimeError("Cloudflare URL is not ready")
        return value

    def mobileconfig(self) -> bytes:
        profile = {
            "PayloadContent": {
                "URL": f"{self.base_url()}/complete/enroll",
                "DeviceAttributes": ["UDID", "DEVICE_NAME", "PRODUCT", "VERSION", "SERIAL"],
                "Challenge": self.challenge,
            },
            "PayloadOrganization": "NightVibes33",
            "PayloadDisplayName": "Dopamine iPad Registration",
            "PayloadDescription": "Registers this iPad for Dopamine OTA installation.",
            "PayloadVersion": 1,
            "PayloadUUID": str(uuid.uuid4()).upper(),
            "PayloadIdentifier": "com.nightvibes33.dopamine.registration",
            "PayloadType": "Profile Service",
        }
        return plistlib.dumps(profile, fmt=plistlib.FMT_XML, sort_keys=False)

    def register(self, values: dict[str, Any]) -> None:
        udid = str(values.get("UDID", "")).strip()
        challenge = str(values.get("CHALLENGE") or values.get("Challenge") or "").strip()
        if not udid:
            raise RuntimeError("The profile did not return a UDID")
        if challenge and challenge != self.challenge:
            raise RuntimeError("Registration profile challenge mismatch")
        name = str(values.get("DEVICE_NAME") or values.get("PRODUCT") or "iPad")

        with self.lock:
            self.apple.ensure_device(udid=udid, name=f"{name} {udid[-6:]}")
            profile = self.apple.profile(
                bundle=self.apple.bundle_resource(self.bundle_id),
                certificate=self.apple.certificate_resource(self.identity),
                devices=self.apple.enabled_devices(),
            )
            self.resign(profile)
            threading.Thread(target=self.persist_profile, args=(profile,), daemon=True).start()

    def resign(self, profile: bytes) -> None:
        with tempfile.TemporaryDirectory(prefix="dopamine-registration-") as temp_name:
            temp = Path(temp_name)
            profile_path = temp / "profile.mobileprovision"
            profile_path.write_bytes(profile)
            profile_plist = temp / "profile.plist"
            profile_plist.write_text(run(["security", "cms", "-D", "-i", str(profile_path)]))
            profile_data = plistlib.loads(profile_plist.read_bytes())
            entitlements = profile_data["Entitlements"]
            bundle_id = entitlements["application-identifier"].split(".", 1)[1]
            if bundle_id != self.bundle_id:
                raise RuntimeError("Generated profile has the wrong bundle ID")
            entitlements_path = temp / "entitlements.plist"
            entitlements_path.write_bytes(plistlib.dumps(entitlements, fmt=plistlib.FMT_XML, sort_keys=False))

            unpacked = temp / "unpacked"
            with zipfile.ZipFile(self.base_ipa) as archive:
                archive.extractall(unpacked)
            apps = list((unpacked / "Payload").glob("*.app"))
            if len(apps) != 1:
                raise RuntimeError("IPA payload is invalid")
            app = apps[0]
            build = time.strftime("%Y%m%d%H%M%S", time.gmtime())
            run(["/usr/libexec/PlistBuddy", "-c", f"Set :CFBundleIdentifier {bundle_id}", str(app / "Info.plist")])
            run(["/usr/libexec/PlistBuddy", "-c", f"Set :CFBundleVersion {build}", str(app / "Info.plist")])
            shutil.copy2(profile_path, app / "embedded.mobileprovision")
            for signature in app.rglob("_CodeSignature"):
                if signature.is_dir():
                    shutil.rmtree(signature)

            nested = list(app.rglob("*.framework")) + list(app.rglob("*.dylib")) + list(app.rglob("*.appex"))
            nested.sort(key=lambda item: len(item.parts), reverse=True)
            for item in nested:
                run(["codesign", "--force", "--sign", self.identity, "--keychain", self.keychain, "--timestamp=none", str(item)])
            run([
                "codesign", "--force", "--sign", self.identity, "--keychain", self.keychain,
                "--timestamp=none", "--entitlements", str(entitlements_path),
                "--generate-entitlement-der", str(app),
            ])
            run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)])

            latest = self.site / "latest"
            latest.mkdir(parents=True, exist_ok=True)
            output_temp = temp / "Dopamine-iPad5-latest-signed.ipa"
            with zipfile.ZipFile(output_temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in (unpacked / "Payload").rglob("*"):
                    archive.write(path, path.relative_to(unpacked))
            output = latest / "Dopamine-iPad5-latest-signed.ipa"
            shutil.copy2(output_temp, output)
            ipa_url = f"{self.base_url()}/complete/latest/{output.name}"
            manifest = {
                "items": [{
                    "assets": [{"kind": "software-package", "url": ipa_url}],
                    "metadata": {
                        "bundle-identifier": bundle_id,
                        "bundle-version": self.version,
                        "kind": "software",
                        "title": self.title,
                    },
                }]
            }
            manifest_data = plistlib.dumps(manifest, fmt=plistlib.FMT_XML, sort_keys=False)
            (latest / "manifest.plist").write_bytes(manifest_data)
            (self.site / "manifest.plist").write_bytes(manifest_data)

    def persist_profile(self, profile: bytes) -> None:
        try:
            parts = self.repo_root / "dopamine" / "profile.parts"
            parts.mkdir(parents=True, exist_ok=True)
            for old in parts.iterdir():
                if old.is_file():
                    old.unlink()
            encoded = base64.b64encode(profile).decode()
            for number, offset in enumerate(range(0, len(encoded), 45000), 1):
                (parts / f"part-{number:03d}.b64").write_text(encoded[offset:offset + 45000] + "\n")
            run(["git", "config", "user.name", "github-actions[bot]"], cwd=self.repo_root)
            run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], cwd=self.repo_root)
            run(["git", "add", "dopamine/profile.parts"], cwd=self.repo_root)
            if not run(["git", "status", "--porcelain", "dopamine/profile.parts"], cwd=self.repo_root):
                return
            run(["git", "commit", "-m", "Update auto-registration provisioning profile"], cwd=self.repo_root)
            for _ in range(5):
                try:
                    run(["git", "pull", "--rebase", "origin", "main"], cwd=self.repo_root)
                    run(["git", "push", "origin", "HEAD:main"], cwd=self.repo_root)
                    return
                except Exception:
                    time.sleep(3)
        except Exception as exc:
            print(f"Could not persist refreshed profile: {exc}", flush=True)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: State) -> None:
        super().__init__(address, Handler)
        self.state = state


class Handler(SimpleHTTPRequestHandler):
    server: Server

    def translate_path(self, path: str) -> str:
        clean = urllib.parse.unquote(urllib.parse.urlsplit(path).path)
        if clean == "/complete":
            clean = "/complete/"
        if clean.startswith("/complete/"):
            clean = clean[len("/complete"):]
        target = (self.server.state.site / clean.lstrip("/")).resolve()
        root = self.server.state.site.resolve()
        if target != root and root not in target.parents:
            return str(root / "__blocked__")
        if target.is_dir():
            target = target / "index.html"
        return str(target)

    def do_GET(self) -> None:
        if urllib.parse.urlsplit(self.path).path == "/complete/register.mobileconfig":
            try:
                content = self.server.state.mobileconfig()
            except Exception as exc:
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/x-apple-aspen-config")
            self.send_header("Content-Disposition", "attachment; filename=Dopamine-iPad-Registration.mobileconfig")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urllib.parse.urlsplit(self.path).path != "/complete/enroll":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            values = plistlib.loads(body)
            if not isinstance(values, dict):
                raise RuntimeError("Invalid registration callback")
            self.server.state.register(values)
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", f"{self.server.state.base_url()}/complete/?registered=1")
            self.send_header("Content-Length", "0")
            self.end_headers()
        except Exception as exc:
            content = f"<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'><h1>Registration failed</h1><p>{html.escape(str(exc))}</p>".encode()
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--base-ipa", type=Path, required=True)
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--keychain", required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--base-url-file", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    missing = [name for name in ("ASC_KEY_ID", "ASC_ISSUER_ID", "ASC_PRIVATE_KEY") if not os.environ.get(name)]
    if missing:
        raise SystemExit("Missing required GitHub secrets: " + ", ".join(missing))
    server = Server((args.host, args.port), State(args))
    print(f"Dopamine registration server listening on {args.host}:{args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
