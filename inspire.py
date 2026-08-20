"""
inspire.py  -  find the best posts from other accounts
=====================================================

WHAT THIS DOES
--------------
It asks Instagram for the recent posts of the accounts in
competitors_config.py. Then it finds the OUTLIERS: posts that got far more
likes than that account normally gets.

Those outliers are worth reading. Something in them worked.

WHY WE COMPARE AN ACCOUNT TO ITSELF
-----------------------------------
We cannot see other accounts' reach or saves. Instagram keeps those private.
So we cannot compare chesscom to ChessMood directly.

But we do not need to. If an account normally gets 500 likes and one post got
4,000, something in that post worked - whatever their size. We compare every
post to that same account's normal level.

HOW TO RUN
----------
    python inspire.py                  (all accounts)
    python inspire.py --group teaching (only the teaching accounts)
    python inspire.py --top 10         (show 10 best instead of 15)
    python inspire.py --no-write       (print only, do not touch the sheet)
"""

import os
import re
import sys
import json
import time
import argparse
import statistics
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

from competitors_config import (
    WATCH_LIST,
    POSTS_PER_ACCOUNT,
    OUTLIER_THRESHOLD,
)


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

INSPIRATION_TAB = "Inspiration"

# Wait this long between accounts, to be polite to Instagram.
PAUSE_SECONDS = 1.0


# ---------------------------------------------------------------------------
# SETUP
# ---------------------------------------------------------------------------

def load_settings():
    load_dotenv()
    token = os.getenv("META_ACCESS_TOKEN", "").strip()
    ig_id = os.getenv("IG_BUSINESS_ACCOUNT_ID", "").strip()
    version = os.getenv("META_API_VERSION", "v26.0").strip()

    if not token:
        sys.exit("ERROR: META_ACCESS_TOKEN is missing from .env")
    if not ig_id:
        sys.exit("ERROR: IG_BUSINESS_ACCOUNT_ID is missing from .env")

    return token, ig_id, version


def connect_to_sheet():
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    key_value = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

    if not sheet_id or not key_value:
        return None  # we can still print results without the sheet

    if key_value.startswith("{"):
        info = json.loads(key_value)
    else:
        if not os.path.exists(key_value):
            print(f"  WARNING: key file not found at {key_value} - will not save to the sheet.")
            return None
        with open(key_value, "r", encoding="utf-8") as f:
            info = json.load(f)

    try:
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        return gspread.authorize(creds).open_by_key(sheet_id)
    except Exception as e:
        print(f"  WARNING: could not open the sheet ({e}) - will not save.")
        return None


# ---------------------------------------------------------------------------
# ASKING INSTAGRAM
# ---------------------------------------------------------------------------

def fetch_account(token, ig_id, version, username):
    """
    Ask Instagram for one account's public info and recent posts.
    Returns (data, error_message). One of them is always None.
    """
    fields = (
        f"business_discovery.username({username})"
        "{followers_count,media_count,media.limit("
        f"{POSTS_PER_ACCOUNT}"
        "){caption,like_count,comments_count,media_type,timestamp,permalink}}"
    )
    params = urllib.parse.urlencode({"fields": fields, "access_token": token})
    url = f"https://graph.facebook.com/{version}/{ig_id}?{params}"

    try:
        with urllib.request.urlopen(url, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
            message = body.get("error", {}).get("message", str(e))
        except Exception:
            message = f"HTTP {e.code}"
        return None, message
    except Exception as e:
        return None, str(e)

    discovery = payload.get("business_discovery")
    if not discovery:
        return None, "no business_discovery in the answer (is it a Business account?)"

    return discovery, None


# ---------------------------------------------------------------------------
# FINDING THE OUTLIERS
# ---------------------------------------------------------------------------

def clean_caption(text, length=150):
    """Make a caption safe and short for a spreadsheet cell."""
    if not text:
        return ""
    one_line = " ".join(str(text).split())
    if len(one_line) <= length:
        return one_line
    return one_line[:length - 1] + "…"


def first_line(text, length=90):
    """The hook - the first line is what makes people stop scrolling."""
    if not text:
        return ""
    for piece in str(text).split("\n"):
        piece = piece.strip()
        if piece:
            return piece[:length]
    return ""


def nice_format(media_type):
    mapping = {
        "CAROUSEL_ALBUM": "CAROUSEL",
        "IMAGE": "IMAGE",
        "VIDEO": "REEL/VIDEO",
    }
    return mapping.get((media_type or "").upper(), media_type or "UNKNOWN")


def analyse_account(username, group, discovery):
    """Work out which of this account's posts beat its own normal level."""
    followers = discovery.get("followers_count") or 0
    posts = (discovery.get("media") or {}).get("data") or []

    likes = []
    for p in posts:
        value = p.get("like_count")
        if isinstance(value, int):
            likes.append(value)

    if len(likes) < 5:
        return [], None, f"only {len(likes)} posts with like counts - not enough to compare"

    normal = statistics.median(likes)
    if normal <= 0:
        return [], None, "median likes is zero - cannot compare"

    rows = []
    for p in posts:
        like_count = p.get("like_count")
        if not isinstance(like_count, int):
            continue

        score = like_count / normal
        caption = p.get("caption", "")

        rows.append({
            "username": username,
            "group": group,
            "followers": followers,
            "date": (p.get("timestamp") or "")[:10],
            "format": nice_format(p.get("media_type")),
            "likes": like_count,
            "comments": p.get("comments_count") or 0,
            "normal_likes": round(normal),
            "score": round(score, 2),
            "is_outlier": score >= OUTLIER_THRESHOLD,
            "hook": first_line(caption),
            "caption": clean_caption(caption),
            "permalink": p.get("permalink", ""),
        })

    return rows, normal, None


# ---------------------------------------------------------------------------
# PRINTING
# ---------------------------------------------------------------------------

def line(char="-", width=76):
    print(char * width)


def print_results(all_rows, failures, top_n):
    outliers = [r for r in all_rows if r["is_outlier"]]
    outliers.sort(key=lambda r: r["score"], reverse=True)

    print()
    line("=")
    print(f" TOP {min(top_n, len(outliers))} OUTLIER POSTS")
    print(" these beat their own account's normal level by the most")
    line("=")

    if not outliers:
        print("\n No outliers found. Every account posted at its usual level.")
    for r in outliers[:top_n]:
        print()
        print(f" {r['score']}x   @{r['username']}  ({r['group']})  {r['date']}")
        print(f"   {r['format']}   {r['likes']:,} likes   normal is {r['normal_likes']:,}")
        print(f"   HOOK: {r['hook']}")
        if r["permalink"]:
            print(f"   {r['permalink']}")

    # --- which formats become outliers most often? ---
    print()
    line("=")
    print(" WHICH FORMATS BECOME OUTLIERS")
    line("=")
    counts = {}
    totals = {}
    for r in all_rows:
        totals[r["format"]] = totals.get(r["format"], 0) + 1
        if r["is_outlier"]:
            counts[r["format"]] = counts.get(r["format"], 0) + 1

    for fmt in sorted(totals, key=lambda f: -(counts.get(f, 0) / totals[f])):
        hits = counts.get(fmt, 0)
        share = hits / totals[fmt] * 100
        print(f"   {fmt:<12} {hits:>3} of {totals[fmt]:>3} posts  ({share:.0f}% became outliers)")

    # --- accounts we could not read ---
    if failures:
        print()
        line("=")
        print(" ACCOUNTS WE COULD NOT READ")
        line("=")
        for name, why in failures:
            print(f"   @{name}: {why}")
        print("\n   Remove these from competitors_config.py, or check they are")
        print("   Business or Creator accounts and the username is spelled right.")


# ---------------------------------------------------------------------------
# SAVING
# ---------------------------------------------------------------------------

def write_sheet(sheet, rows):
    headers = ["run_date", "username", "group", "followers", "post_date",
               "format", "likes", "comments", "normal_likes", "score",
               "is_outlier", "hook", "caption", "permalink"]

    try:
        tab = sheet.worksheet(INSPIRATION_TAB)
    except gspread.WorksheetNotFound:
        tab = sheet.add_worksheet(title=INSPIRATION_TAB, rows=2000, cols=len(headers))
        tab.append_row(headers)

    today = datetime.now(timezone.utc).date().isoformat()
    new_rows = [[
        today, r["username"], r["group"], r["followers"], r["date"],
        r["format"], r["likes"], r["comments"], r["normal_likes"], r["score"],
        "YES" if r["is_outlier"] else "", r["hook"], r["caption"], r["permalink"],
    ] for r in rows]

    tab.append_rows(new_rows, value_input_option="RAW")
    print(f"\n Saved {len(new_rows)} rows to the '{INSPIRATION_TAB}' tab.")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Find the best posts from other accounts.")
    parser.add_argument("--group", default=None,
                        help="Only one group: chess or teaching.")
    parser.add_argument("--top", type=int, default=15,
                        help="How many outliers to print. Default 15.")
    parser.add_argument("--no-write", action="store_true",
                        help="Print results but do not save to the sheet.")
    args = parser.parse_args()

    print()
    line("=")
    print(" ChessMood inspiration finder")
    line("=")

    token, ig_id, version = load_settings()

    watching = WATCH_LIST
    if args.group:
        watching = [(u, g) for u, g in WATCH_LIST if g == args.group.lower()]
        if not watching:
            sys.exit(f"ERROR: no accounts in group '{args.group}'. "
                     f"Groups available: chess, teaching")

    print(f" Accounts to check : {len(watching)}")
    print(f" Posts per account : {POSTS_PER_ACCOUNT}")
    print(f" Outlier means     : {OUTLIER_THRESHOLD}x that account's normal likes")
    print()

    all_rows = []
    failures = []

    for i, (username, group) in enumerate(watching, start=1):
        print(f" [{i}/{len(watching)}] @{username} ...", end=" ", flush=True)

        discovery, error = fetch_account(token, ig_id, version, username)
        if error:
            print(f"FAILED")
            failures.append((username, error))
            time.sleep(PAUSE_SECONDS)
            continue

        rows, normal, problem = analyse_account(username, group, discovery)
        if problem:
            print(f"skipped ({problem})")
            failures.append((username, problem))
            time.sleep(PAUSE_SECONDS)
            continue

        found = len([r for r in rows if r["is_outlier"]])
        print(f"{len(rows)} posts, normal {round(normal):,} likes, {found} outlier(s)")
        all_rows.extend(rows)
        time.sleep(PAUSE_SECONDS)

    if not all_rows:
        print("\n No data collected. Check the failures above.")
        sys.exit(1)

    print_results(all_rows, failures, args.top)

    if not args.no_write:
        sheet = connect_to_sheet()
        if sheet:
            write_sheet(sheet, all_rows)

    print()
    line("=")
    print(" Done.")
    line("=")
    print()
    print(" HOW TO USE THIS")
    print(" Read the HOOK lines above. That is the first line of each post,")
    print(" the line that made people stop scrolling.")
    print()
    print(" Do not copy the topic. Copy the STRUCTURE:")
    print("   how the hook is built, how the post is organised,")
    print("   what it promises, how it ends.")
    print()
    print(" Then make it about chess.")
    print()


if __name__ == "__main__":
    main()
