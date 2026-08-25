"""
make_secrets.py
===============

Builds the text you paste into Streamlit Cloud's "Secrets" box.

Why this script exists: Streamlit Cloud has no .env file and no key file on
disk. It only has a small text box. So the Google key has to travel as ONE
line of text. We turn the .json key file into base64 - a way of writing any
file as a single line of safe characters. config.py already knows how to read
base64, so nothing else changes.

Run it once:
    python make_secrets.py

It writes .streamlit/secrets.toml, which is git-ignored and never uploaded.
"""

from __future__ import annotations

import base64
from pathlib import Path

ENV_FILE = Path(".env")
OUT_FILE = Path(".streamlit/secrets.toml")

NEEDED = [
    "META_ACCESS_TOKEN",
    "META_API_VERSION",
    "IG_BUSINESS_ACCOUNT_ID",
    "FB_PAGE_ID",
    "GOOGLE_SHEET_ID",
    "GEMINI_API_KEY",
]


def read_env(path: Path) -> dict[str, str]:
    """Read .env into a dictionary, ignoring comments and blank lines."""
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def main() -> None:
    if not ENV_FILE.exists():
        print("ERROR: no .env file found in this folder.")
        return

    env = read_env(ENV_FILE)

    key_path_text = env.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not key_path_text:
        print("ERROR: GOOGLE_SERVICE_ACCOUNT_JSON is missing from .env")
        return

    key_path = Path(key_path_text)
    if not key_path.is_file():
        print(f"ERROR: the key file was not found at: {key_path}")
        print("Open .env and check the GOOGLE_SERVICE_ACCOUNT_JSON line.")
        return

    key_base64 = base64.b64encode(key_path.read_bytes()).decode("ascii")

    print()
    print("Choose a password for the web dashboard.")
    print("Anyone with the link will need it. Letters and numbers, no spaces.")
    password = input("Password: ").strip()
    if not password:
        print("ERROR: the password cannot be empty.")
        return

    lines = ['# Paste everything below into the Streamlit Cloud "Secrets" box.', ""]
    missing = []
    for name in NEEDED:
        value = env.get(name, "")
        if not value:
            missing.append(name)
            continue
        lines.append(f'{name} = "{value}"')

    lines.append(f'APP_PASSWORD = "{password}"')
    lines.append(f'GOOGLE_SERVICE_ACCOUNT_JSON = "{key_base64}"')
    lines.append("")

    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text("\n".join(lines), encoding="utf-8")

    print()
    print(f"Written to {OUT_FILE}")
    print(f"  {len(NEEDED) - len(missing) + 2} settings included")
    if missing:
        print(f"  Empty in .env, so skipped: {', '.join(missing)}")
    print()
    print("Next: open that file, select all, copy.")
    print("This file is git-ignored, so it will never be uploaded.")


if __name__ == "__main__":
    main()
