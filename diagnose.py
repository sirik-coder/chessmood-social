"""
diagnose.py – Meta API diagnostic script (round 2).
Run:  python diagnose.py
Reads META_ACCESS_TOKEN and META_API_VERSION from .env.
No extra dependencies — uses only the stdlib.
"""

import json
import urllib.request
import urllib.parse
from pathlib import Path

# ── load .env manually ───────────────────────────────────────────────────────
env_path = Path(__file__).parent / ".env"
env_vars: dict[str, str] = {}
for line in env_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, _, val = line.partition("=")
    env_vars[key.strip()] = val.strip()

TOKEN   = env_vars.get("META_ACCESS_TOKEN", "")
VERSION = env_vars.get("META_API_VERSION", "v26.0")
BASE    = f"https://graph.facebook.com/{VERSION}"
BIZ_ID  = "158230784873352"

if not TOKEN:
    raise SystemExit("META_ACCESS_TOKEN is empty in .env – aborting.")

# ── helpers ──────────────────────────────────────────────────────────────────
def call(path: str, **params) -> dict:
    params["access_token"] = TOKEN
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())

def section(title: str) -> None:
    print(f"\n{'='*64}")
    print(f"  {title}")
    print('='*64)

def show(label: str, path: str, **params) -> dict:
    section(label)
    result = call(path, **params)
    print(json.dumps(result, indent=2))
    return result

# ── diagnostics ──────────────────────────────────────────────────────────────
print("\nMeta API Diagnostic (round 2) — ChessMood")
print(f"API version  : {VERSION}")
print(f"Business ID  : {BIZ_ID}")
print(f"Token prefix : {TOKEN[:20]}...")

r_perm = show(
    "1 . me/permissions",
    "me/permissions")

r_acct = show(
    "2 . me/accounts  (with instagram_business_account)",
    "me/accounts",
    fields="id,name,access_token,instagram_business_account")

r_biz = show(
    "3 . me/businesses  (with id,name)",
    "me/businesses",
    fields="id,name")

r_owned = show(
    f"4 . {BIZ_ID}?fields=id,name,owned_pages{{id,name,instagram_business_account}}",
    BIZ_ID,
    fields="id,name,owned_pages{id,name,instagram_business_account}")

r_client = show(
    f"5 . {BIZ_ID}?fields=id,name,client_pages{{id,name,instagram_business_account}}",
    BIZ_ID,
    fields="id,name,client_pages{id,name,instagram_business_account}")

r_assigned = show(
    "6 . me/assigned_pages  (with instagram_business_account)",
    "me/assigned_pages",
    fields="id,name,instagram_business_account")

# ── summary ───────────────────────────────────────────────────────────────────
section("SUMMARY (auto-generated)")

# permissions
granted = set()
if "data" in r_perm:
    granted = {p["permission"] for p in r_perm["data"] if p.get("status") == "granted"}
print(f"Granted permissions : {', '.join(sorted(granted)) or '(none found)'}")
print(f"business_management : {'GRANTED' if 'business_management' in granted else 'NOT in token'}")

# collect all pages from all sources
found_pages: list[dict] = []

for label, result in [
    ("me/accounts",       r_acct),
    ("owned_pages",       r_owned.get("owned_pages", {})),
    ("client_pages",      r_client.get("client_pages", {})),
    ("me/assigned_pages", r_assigned),
]:
    pages = result.get("data", [])
    for p in pages:
        p["_source"] = label
        found_pages.append(p)

print(f"\nPages found across all endpoints: {len(found_pages)}")

fb_page_id = None
ig_id      = None

for p in found_pages:
    name = p.get("name", "?")
    pid  = p.get("id", "?")
    src  = p.get("_source", "?")
    ig   = p.get("instagram_business_account") or {}
    ig_account_id = ig.get("id") if ig else None

    print(f"  Page : {name}  (id={pid})  [from {src}]")
    if ig_account_id:
        print(f"    IG business account id : {ig_account_id}")

    # pick the first page that looks like ChessMood
    if fb_page_id is None and ("chess" in name.lower() or True):
        fb_page_id = pid
        ig_id      = ig_account_id

if fb_page_id:
    print(f"\nFB_PAGE_ID            : {fb_page_id}")
    print(f"IG_BUSINESS_ACCOUNT_ID: {ig_id or '(not found – IG not linked or not returned)'}")
else:
    print("\nNo pages found. Cannot populate FB_PAGE_ID or IG_BUSINESS_ACCOUNT_ID.")

print()
