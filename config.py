"""
config.py
=========

This is the "settings" file. Nothing here talks to the internet. Its two jobs:

  1. Read the secrets out of the environment (.env locally, GitHub Secrets in CI)
     and complain in plain English if something is missing or malformed.
  2. Hold every list, name and knob in ONE place, so when Meta renames a metric
     (they do this often) you edit this file and nothing else.

Everything is heavily commented on purpose - treat this file as the control panel.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Read the .env file sitting next to this script (if there is one) and copy its
# contents into the process environment. On GitHub Actions there is no .env file,
# and that is fine - the values come from GitHub Secrets instead.
load_dotenv()


class ConfigError(RuntimeError):
    """Raised when a setting is missing or unusable. collect.py catches this and
    prints a friendly message instead of a stack trace."""


# ===========================================================================
# SECTION 1 - Graph API version
# ===========================================================================
#
# Default: v26.0 - checked against Meta's changelog on 2026-08-11.
#
# Meta ships a new version every ~3 months and switches OFF old ones after
# roughly 2 years, so this default WILL go stale eventually. When it does, prefer
# setting META_API_VERSION in .env over editing this line - that way upgrading
# never touches code, and GitHub Actions can be bumped by changing one secret.
#     https://developers.facebook.com/docs/graph-api/changelog
# Calling a retired version gives you a clear error, not silently wrong data.
#
GRAPH_API_VERSION = (os.getenv("META_API_VERSION") or "v26.0").strip()

# Both Instagram and Facebook business data come from this same host.
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


# ===========================================================================
# SECTION 2 - How much history to collect
# ===========================================================================

def _env_int(name: str, default: int) -> int:
    """Read a whole number from the environment, falling back to a default."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(
            f"{name} must be a whole number (you set it to {raw!r})."
        ) from None


# 90 days of posts, as requested. Override with LOOKBACK_DAYS in .env.
LOOKBACK_DAYS = _env_int("LOOKBACK_DAYS", 90)

# How many items to ask Meta for per page. 50 is a good compromise: big enough
# to keep the number of requests low, small enough that Meta rarely times out.
PAGE_SIZE = _env_int("PAGE_SIZE", 50)

# Safety brake so a bug in Meta's paging cursors can never loop forever.
MAX_PAGES = _env_int("MAX_PAGES", 100)

# Which Facebook edge to read the page's own posts from.
#   published_posts -> posts the Page itself published (what you almost certainly want)
#   posts           -> older name for roughly the same thing
#   feed            -> ALSO includes posts other people made on your page
# If published_posts is rejected, the code automatically retries with "posts".
FB_POSTS_EDGE = (os.getenv("FB_POSTS_EDGE") or "published_posts").strip()


# ===========================================================================
# SECTION 3 - Instagram: which fields and metrics to ask for
# ===========================================================================

# Plain fields on a media object. These are stable and rarely change.
IG_MEDIA_FIELDS = [
    "id",
    "media_type",           # IMAGE | VIDEO | CAROUSEL_ALBUM
    "media_product_type",   # FEED | REELS | STORY | AD
    "caption",
    "permalink",
    "timestamp",
    "like_count",
    "comments_count",
]

# Insight metrics to ask for per post.
#
# IMPORTANT - these are the volatile ones. Meta allows different metrics for
# different media types (a Reel supports different metrics than a carousel), and
# they retire metric names periodically. The code NEVER crashes on a rejected
# metric: it retries the request one metric at a time, records which ones were
# refused, and leaves those cells blank. See METRIC_NOTES below for what I know
# about each one.
IG_MEDIA_METRICS = [
    "reach",
    "views",               # the modern replacement for impressions / plays
    "impressions",         # probably retired - see METRIC_NOTES
    "saved",
    "shares",
    "total_interactions",
    "profile_visits",
    "follows",
    "plays",               # probably retired - see METRIC_NOTES
]

# Account-level daily metrics (the "DailyStats" tab).
IG_ACCOUNT_METRICS = [
    "follower_count",
    "reach",
    "profile_views",
]


# ===========================================================================
# SECTION 4 - Facebook: which fields and metrics to ask for
# ===========================================================================

# Plain fields on a Page post. The summary(true).limit(0) bits mean
# "just give me the total count, not the actual comments/reactions".
FB_POST_FIELDS = [
    "id",
    "created_time",
    "message",
    "permalink_url",
    "status_type",
    "shares",                              # -> {"count": 12}
    "comments.summary(true).limit(0)",     # -> summary.total_count
    "reactions.summary(true).limit(0)",    # -> summary.total_count
]

# Facebook post insight metrics. Facebook uses different (longer) metric names
# than Instagram, so this maps the name YOU want in the sheet to the metric name
# Meta expects.
#
#   reach       <- post_impressions_unique       (unique people, i.e. reach)
#   impressions <- post_impressions              (total times shown)
#   reactions   <- post_reactions_by_type_total  (a breakdown; we sum it)
#   clicks      <- post_clicks                   (all clicks anywhere on the post)
#
# "comments" and "shares" are NOT insight metrics on Facebook - they come from
# the plain post fields above. That is why they are not in this list.
FB_POST_INSIGHT_METRICS = {
    "reach": "post_impressions_unique",
    "impressions": "post_impressions",
    "reactions": "post_reactions_by_type_total",
    "clicks": "post_clicks",
}


# ===========================================================================
# SECTION 5 - Notes about metrics I am NOT fully certain about
# ===========================================================================
#
# You asked me to tell you instead of guessing silently. These are the specific
# things to double-check against Meta's docs. The script prints the relevant note
# automatically whenever Meta refuses one of these metrics, so you will not have
# to remember any of it.
METRIC_NOTES = {
    "impressions": (
        "Meta deprecated 'impressions' for Instagram media (replaced by 'views') "
        "around API v22 in 2025. Expect this to be refused. The 'views' column "
        "is the modern equivalent - use that instead and delete 'impressions' "
        "from IG_MEDIA_METRICS in config.py."
    ),
    "plays": (
        "Same story as 'impressions': 'plays' for Reels was folded into 'views'. "
        "Expect a refusal; use the 'views' column."
    ),
    "views": (
        "'views' is the newer unified metric. If it is refused, your "
        "META_API_VERSION may be too old - try a newer version."
    ),
    "profile_visits": (
        "Only valid on some media product types (mainly REELS / FEED, not "
        "carousel children or stories). Refusals on some posts are normal."
    ),
    "follows": (
        "Same as profile_visits - valid only for some media types. Blank cells "
        "here are expected, not a bug."
    ),
    "shares": (
        "Not available for every Instagram media type, and not available at all "
        "for very old posts."
    ),
    "profile_views": (
        "Account-level metric. Meta has been reshuffling account insights; if "
        "this is refused, check the Instagram Insights docs for the current name."
    ),
    "follower_count": (
        "Instagram only returns follower_count for roughly the last 30 days, so "
        "the older rows in DailyStats will legitimately be blank for this column."
    ),
    "post_impressions": (
        "Facebook Page post metric. Meta has been retiring parts of Page "
        "Insights; if refused, check the Page/Post Insights docs for the "
        "current name (there is a newer 'post_views' style metric)."
    ),
    "post_impressions_unique": (
        "This is how you get Facebook post reach. Same caveat as "
        "post_impressions - verify the name if it is refused."
    ),
    "post_clicks": (
        "Facebook post metric. Verify against current Page Insights docs if refused."
    ),
    "post_reactions_by_type_total": (
        "Returns a breakdown like {'like': 10, 'love': 3}; the code adds those "
        "numbers together. If refused, the script falls back to the plain "
        "reactions.summary count, which is just as good."
    ),
}


# ===========================================================================
# SECTION 6 - Google Sheet layout (tab names and column headers)
# ===========================================================================
#
# The FIRST name in each header list is the unique key used for upserting.
# Order here = column order in the sheet. If you add a column later, the script
# adds it to the end of the existing header row instead of scrambling old data.

IG_POSTS_TAB = "IG_Posts"
IG_POSTS_HEADERS = [
    "id",                    # <- unique key
    "timestamp",
    "date",
    "media_type",
    "media_product_type",
    "permalink",
    "caption",
    "like_count",
    "comments_count",
    "reach",
    "views",
    "impressions",
    "saved",
    "shares",
    "total_interactions",
    "profile_visits",
    "follows",
    "plays",
    "last_updated",
]

FB_POSTS_TAB = "FB_Posts"
FB_POSTS_HEADERS = [
    "id",                    # <- unique key
    "created_time",
    "date",
    "status_type",
    "permalink_url",
    "message",
    "reach",
    "impressions",
    "reactions",
    "comments",
    "shares",
    "clicks",
    "last_updated",
]

DAILY_STATS_TAB = "DailyStats"
DAILY_STATS_HEADERS = [
    "date",                  # <- unique key
    "follower_count",
    "reach",
    "profile_views",
    "last_updated",
]

# This column is refreshed by the script, so it must be ignored when deciding
# "did this row actually change?" - otherwise every row would look changed on
# every single run.
TIMESTAMP_COLUMN = "last_updated"


# ===========================================================================
# SECTION 7 - Reading the secrets
# ===========================================================================

@dataclass(frozen=True)
class Settings:
    """A tidy little box holding the five secrets, already validated."""

    meta_access_token: str
    ig_business_account_id: str
    fb_page_id: str
    google_sheet_id: str
    google_service_account_info: dict

    @property
    def service_account_email(self) -> str:
        """The robot email address that must be invited to your Google Sheet."""
        return self.google_service_account_info.get("client_email", "(unknown)")


def _require(name: str) -> str:
    """Fetch a required environment variable or explain exactly what to do."""
    value = (os.getenv(name) or "").strip()
    if not value:
        raise ConfigError(
            f"Missing required setting: {name}\n"
            f"  -> Add a line like  {name}=your_value  to your .env file\n"
            f"     (copy .env.example to .env if you have not done that yet).\n"
            f"     On GitHub Actions, add it under Settings > Secrets and variables."
        )
    return value


def _optional(name: str) -> str:
    """Fetch a setting that may legitimately be absent (returns '')."""
    return (os.getenv(name) or "").strip()


def _parse_service_account(raw: str) -> dict:
    """
    Turn the GOOGLE_SERVICE_ACCOUNT_JSON value into a Python dictionary.

    We accept three shapes, because each is convenient somewhere:
      1. The raw JSON text          (best for GitHub Actions secrets)
      2. Base64-encoded JSON        (avoids all quoting/newline pain in .env)
      3. A path to a .json file     (easiest for local testing)
    """
    text = raw.strip()

    # People often paste the value wrapped in quotes. Strip one matching pair.
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()

    # Shape 3: a file path on disk.
    if not text.startswith("{"):
        candidate = Path(text)
        try:
            is_file = candidate.is_file()
        except OSError:
            is_file = False  # the string was not a legal path at all
        if is_file:
            text = candidate.read_text(encoding="utf-8")

    # Shape 2: base64. If it still is not JSON, try decoding it.
    if not text.strip().startswith("{"):
        try:
            decoded = base64.b64decode(text, validate=True).decode("utf-8")
            if decoded.strip().startswith("{"):
                text = decoded
        except Exception:
            pass  # not base64 either - the JSON error below will explain

    # Now it really must be JSON.
    try:
        info = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            "GOOGLE_SERVICE_ACCOUNT_JSON could not be read as JSON.\n"
            f"  JSON error: {exc}\n"
            "  Fixes, easiest first:\n"
            "   1. Put the whole JSON on ONE line in .env (no line breaks), or\n"
            "   2. Paste the path to the .json file instead (local runs only), or\n"
            "   3. Base64-encode the file and paste that. PowerShell:\n"
            '      [Convert]::ToBase64String([IO.File]::ReadAllBytes("key.json"))'
        ) from None

    if not isinstance(info, dict):
        raise ConfigError("GOOGLE_SERVICE_ACCOUNT_JSON must be a JSON object ({...}).")

    # Sanity-check the two keys google-auth absolutely needs.
    for key in ("client_email", "private_key"):
        if not info.get(key):
            raise ConfigError(
                f"The service account JSON is missing '{key}'. That usually means "
                "you pasted the wrong file - you want the KEY file you downloaded "
                "from Google Cloud > IAM > Service Accounts > Keys."
            )

    # A very common copy/paste accident: the newlines inside private_key got
    # turned into literal backslash-n instead of real line breaks.
    if "\\n" in info["private_key"] and "\n" not in info["private_key"]:
        info["private_key"] = info["private_key"].replace("\\n", "\n")

    return info


def get_settings() -> Settings:
    """
    Read and validate everything. Call this once at the start of collect.py.

    Note: IG_BUSINESS_ACCOUNT_ID and FB_PAGE_ID are read but NOT required here,
    because you might legitimately run with --skip-ig or --skip-fb. collect.py
    checks for them only when it actually needs them.
    """
    return Settings(
        meta_access_token=_require("META_ACCESS_TOKEN"),
        ig_business_account_id=_optional("IG_BUSINESS_ACCOUNT_ID"),
        fb_page_id=_optional("FB_PAGE_ID"),
        google_sheet_id=_require("GOOGLE_SHEET_ID"),
        google_service_account_info=_parse_service_account(
            _require("GOOGLE_SERVICE_ACCOUNT_JSON")
        ),
    )
