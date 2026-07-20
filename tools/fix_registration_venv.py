#!/usr/bin/env python3
from pathlib import Path

path = Path('.github/workflows/dopamine-main-stable-installer.yml')
text = path.read_text()

replacements = [
    (
        '          python3 -m pip install --user PyJWT cryptography\n',
        '          python3 -m venv registration-venv\n'
        '          registration-venv/bin/python -m pip install --upgrade pip\n'
        '          registration-venv/bin/python -m pip install PyJWT cryptography\n'
        '          echo "REGISTRATION_PYTHON=$GITHUB_WORKSPACE/registration-venv/bin/python" >> "$GITHUB_ENV"\n',
    ),
    (
        '          RUNNER_TRACKING_ID="" nohup python3 tools/dopamine_registration_server.py \\\n',
        '          RUNNER_TRACKING_ID="" nohup "$REGISTRATION_PYTHON" tools/dopamine_registration_server.py \\\n',
    ),
    (
        '          if [[ "$EVENT_NAME" == "workflow_dispatch" || "$EVENT_NAME" == "push" ]]; then\n'
        '            BUILD_NEEDED=true\n'
        '          elif [[ -z "$PUBLISHED_SHA" || "$SOURCE_SHA" != "$PUBLISHED_SHA" ]]; then\n',
        '          if [[ "$EVENT_NAME" == "workflow_dispatch" ]]; then\n'
        '            BUILD_NEEDED=true\n'
        '          elif [[ "$EVENT_NAME" == "push" ]] && git diff-tree --no-commit-id --name-only -r "$GITHUB_SHA" | grep -q \'^dopamine/\'; then\n'
        '            BUILD_NEEDED=true\n'
        '          elif [[ -z "$PUBLISHED_SHA" || "$SOURCE_SHA" != "$PUBLISHED_SHA" ]]; then\n',
    ),
    (
        '          elif [[ "$EVENT_SCHEDULE" == "7 */4 * * *" ]]; then\n'
        '            SERVE_NEEDED=true\n',
        '          elif [[ "$EVENT_NAME" == "push" || "$EVENT_SCHEDULE" == "7 */4 * * *" ]]; then\n'
        '            SERVE_NEEDED=true\n',
    ),
]

for old, new in replacements:
    if old not in text:
        raise SystemExit(f'expected workflow block not found: {old[:80]!r}')
    text = text.replace(old, new, 1)

path.write_text(text)
