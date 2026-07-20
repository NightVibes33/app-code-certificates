#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import plistlib
import shutil
from pathlib import Path
from urllib.parse import quote


def manifest_bytes(*, ipa_url: str, bundle_id: str, version: str, title: str) -> bytes:
    manifest = {
        "items": [
            {
                "assets": [{"kind": "software-package", "url": ipa_url}],
                "metadata": {
                    "bundle-identifier": bundle_id,
                    "bundle-version": version,
                    "kind": "software",
                    "title": title,
                },
            }
        ]
    }
    return plistlib.dumps(manifest, fmt=plistlib.FMT_XML, sort_keys=False)


def page_html(*, title: str, version: str, build: str, bundle_id: str, source_sha: str, manifest_url: str) -> str:
    install_url = f"itms-services://?action=download-manifest&url={quote(manifest_url, safe='')}"
    return f"""<!doctype html>
<html lang=\"en\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">
<meta name=\"theme-color\" content=\"#090b10\">
<title>{html.escape(title)} Installer</title>
<style>
:root {{ color-scheme: dark; font-family: -apple-system, BlinkMacSystemFont, \"SF Pro Display\", sans-serif; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; background: radial-gradient(circle at 50% -10%, #4b285f 0, #111522 38%, #07090e 72%); color: #f7f8fb; }}
main {{ width: min(100%, 520px); border: 1px solid rgba(255,255,255,.12); border-radius: 28px; padding: 26px; background: rgba(12,15,22,.88); box-shadow: 0 30px 90px rgba(0,0,0,.5); backdrop-filter: blur(24px); }}
.badge {{ display: inline-flex; padding: 7px 11px; border-radius: 999px; background: rgba(115,255,169,.12); color: #9dffc1; font-size: 12px; font-weight: 700; letter-spacing: .04em; }}
h1 {{ margin: 18px 0 8px; font-size: clamp(30px, 8vw, 46px); letter-spacing: -.045em; line-height: 1; }}
p {{ color: #aeb6c8; line-height: 1.5; }}
a.primary {{ display: block; margin-top: 24px; padding: 17px 20px; border-radius: 15px; text-align: center; text-decoration: none; color: #071009; background: linear-gradient(135deg, #92ffb9, #6ee7ff); font-weight: 900; font-size: 18px; }}
dl {{ margin: 24px 0 0; display: grid; grid-template-columns: 1fr auto; gap: 10px 18px; padding-top: 20px; border-top: 1px solid rgba(255,255,255,.09); }}
dt {{ color: #788297; }} dd {{ margin: 0; text-align: right; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap: anywhere; }}
small {{ display: block; margin-top: 20px; color: #717b8f; line-height: 1.45; }}
</style>
</head>
<body>
<main>
<span class=\"badge\">SIGNED FOR YOUR REGISTERED iPAD</span>
<h1>{html.escape(title)}</h1>
<p>This permanent installer always points to the newest signed StockBaseline IPA. Tap install in Safari, approve the iOS prompt, then wait for the icon to finish loading.</p>
<a class=\"primary\" href=\"{html.escape(install_url, quote=True)}\">Install Dopamine Build {html.escape(build)}</a>
<dl>
<dt>Version</dt><dd>{html.escape(version)}</dd>
<dt>Build</dt><dd>{html.escape(build)}</dd>
<dt>Bundle ID</dt><dd>{html.escape(bundle_id)}</dd>
<dt>Source</dt><dd>{html.escape(source_sha[:12])}</dd>
</dl>
<small>The embedded provisioning profile limits installation to the registered iPad. The public URL does not authorize another device.</small>
</main>
</body>
</html>
"""


def redirect_html(target: str) -> str:
    safe = html.escape(target, quote=True)
    return f"""<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><meta http-equiv=\"refresh\" content=\"0;url={safe}\"><title>Opening latest installer</title></head><body><p><a href=\"{safe}\">Open the latest installer</a></p><script>location.replace({json.dumps(target)});</script></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--ipa", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()

    site = args.site.resolve()
    ipa = args.ipa.resolve()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    required = ["bundle_id", "version", "build", "title", "source_sha"]
    missing = [key for key in required if not str(metadata.get(key, "")).strip()]
    if missing:
        raise SystemExit(f"metadata missing: {', '.join(missing)}")

    base_url = args.base_url.rstrip("/")
    build_id = f"{metadata['build']}-{metadata['source_sha'][:10]}"
    latest_dir = site / "latest"
    build_dir = site / "builds" / build_id
    latest_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)

    latest_ipa = latest_dir / "Dopamine-iPad5-StockBaseline.ipa"
    shutil.copy2(ipa, latest_ipa)
    ipa_url = f"{base_url}/latest/{latest_ipa.name}"
    latest_manifest_url = f"{base_url}/latest/manifest.plist"

    manifest = manifest_bytes(
        ipa_url=ipa_url,
        bundle_id=metadata["bundle_id"],
        version=metadata["version"],
        title=metadata["title"],
    )
    (latest_dir / "manifest.plist").write_bytes(manifest)
    (site / "manifest.plist").write_bytes(manifest)

    page = page_html(
        title=metadata["title"],
        version=metadata["version"],
        build=metadata["build"],
        bundle_id=metadata["bundle_id"],
        source_sha=metadata["source_sha"],
        manifest_url=latest_manifest_url,
    )
    (site / "index.html").write_text(page, encoding="utf-8")
    (latest_dir / "index.html").write_text(page, encoding="utf-8")

    unique_url = f"{base_url}/builds/{build_id}/"
    (build_dir / "index.html").write_text(redirect_html(base_url + "/"), encoding="utf-8")
    (build_dir / "manifest.plist").write_bytes(manifest)

    published = {
        **metadata,
        "build_id": build_id,
        "stable_url": base_url + "/",
        "unique_url": unique_url,
        "ipa_url": ipa_url,
        "manifest_url": latest_manifest_url,
    }
    (site / "latest.json").write_text(json.dumps(published, indent=2) + "\n", encoding="utf-8")
    (latest_dir / "metadata.json").write_text(json.dumps(published, indent=2) + "\n", encoding="utf-8")
    (latest_dir / "source-sha.txt").write_text(metadata["source_sha"] + "\n", encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")
    (site / "installer-url.txt").write_text(unique_url + "\n", encoding="utf-8")
    (site / "stable-installer-url.txt").write_text(base_url + "/\n", encoding="utf-8")
    print(json.dumps(published))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
