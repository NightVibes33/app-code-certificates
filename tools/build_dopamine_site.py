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
    return plistlib.dumps(
        {
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
        },
        fmt=plistlib.FMT_XML,
        sort_keys=False,
    )


def page_html(*, title: str, manifest_url: str, profile_url: str) -> str:
    install_url = f"itms-services://?action=download-manifest&url={quote(manifest_url, safe='')}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#090b10">
<title>{html.escape(title)} Installer</title>
<style>
:root {{ color-scheme: dark; font-family: -apple-system,BlinkMacSystemFont,"SF Pro Display",sans-serif; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 22px; background: radial-gradient(circle at 50% -10%,#4b285f 0,#111522 38%,#07090e 72%); color: #f7f8fb; }}
main {{ width: min(100%,500px); border: 1px solid rgba(255,255,255,.12); border-radius: 28px; padding: 28px; background: rgba(12,15,22,.9); box-shadow: 0 30px 90px rgba(0,0,0,.5); }}
h1 {{ margin: 0 0 10px; font-size: clamp(32px,8vw,44px); letter-spacing: -.045em; }}
p {{ margin: 0 0 24px; color: #aeb6c8; line-height: 1.5; }}
.status {{ display: inline-block; margin-bottom: 18px; padding: 7px 10px; border-radius: 999px; color: #bfffd2; background: rgba(100,255,155,.1); border: 1px solid rgba(100,255,155,.25); font-size: 13px; font-weight: 800; }}
.actions {{ display: grid; gap: 13px; }}
a {{ display: block; padding: 17px 19px; border-radius: 15px; text-align: center; text-decoration: none; font-size: 17px; font-weight: 900; }}
.profile {{ color: #f3f6ff; border: 1px solid rgba(255,255,255,.2); background: rgba(255,255,255,.07); }}
.install {{ color: #071009; background: linear-gradient(135deg,#92ffb9,#6ee7ff); }}
</style>
</head>
<body>
<main>
<h1>{html.escape(title)}</h1>
<div class="status">iPad 5 DarkSword stability build</div>
<p>This build includes the CPU-watchdog wait fixes, bounded retries, and IOSurface cleanup. Install the registration profile first, then return here and install the latest signed IPA.</p>
<div class="actions">
<a class="profile" href="{html.escape(profile_url, quote=True)}">Install Registration Profile</a>
<a class="install" href="{html.escape(install_url, quote=True)}">Install Latest Stability IPA</a>
</div>
</main>
</body>
</html>
"""


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
    latest_dir = site / "latest"
    latest_dir.mkdir(parents=True, exist_ok=True)
    latest_ipa = latest_dir / "Dopamine-iPad5-latest-signed.ipa"
    shutil.copy2(ipa, latest_ipa)

    ipa_url = f"{base_url}/latest/{latest_ipa.name}"
    manifest_url = f"{base_url}/latest/manifest.plist"
    profile_url = f"{base_url}/register.mobileconfig"
    manifest = manifest_bytes(
        ipa_url=ipa_url,
        bundle_id=metadata["bundle_id"],
        version=metadata["version"],
        title=metadata["title"],
    )
    (latest_dir / "manifest.plist").write_bytes(manifest)
    (site / "manifest.plist").write_bytes(manifest)
    page = page_html(title=metadata["title"], manifest_url=manifest_url, profile_url=profile_url)
    (site / "index.html").write_text(page, encoding="utf-8")
    (latest_dir / "index.html").write_text(page, encoding="utf-8")

    published = {
        **metadata,
        "stable_url": base_url + "/",
        "registration_url": profile_url,
        "ipa_url": ipa_url,
        "manifest_url": manifest_url,
    }
    (site / "latest.json").write_text(json.dumps(published, indent=2) + "\n", encoding="utf-8")
    (latest_dir / "metadata.json").write_text(json.dumps(published, indent=2) + "\n", encoding="utf-8")
    (latest_dir / "source-sha.txt").write_text(metadata["source_sha"] + "\n", encoding="utf-8")
    print(json.dumps(published))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
