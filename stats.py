"""
stats.py
========

All the counting happens here, in Python. Never in an AI model.

Why this file exists: a language model is good at explaining patterns and bad
at counting rows. So Python counts, and Gemini only writes sentences about
numbers we hand it. Every number on the dashboard comes from this file.

Nothing here draws anything. dashboard.py does the drawing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

import config
from sheets_client import SheetsClient
from tags_config import tag_caption


# ---------------------------------------------------------------------------
# Connecting
# ---------------------------------------------------------------------------

def get_client() -> SheetsClient:
    """Open the Google Sheet. verbose=False so it does not print to the web page."""
    settings = config.get_settings()
    return SheetsClient(
        service_account_info=settings.google_service_account_info,
        sheet_id=settings.google_sheet_id,
        verbose=False,
    )


def read_tab(client: SheetsClient, tab_name: str) -> pd.DataFrame:
    """
    Read one tab into a table. Returns an EMPTY table if the tab is missing,
    so a missing tab never crashes the dashboard.
    """
    try:
        worksheet = client._retry(client.spreadsheet.worksheet, tab_name)
        values = client._retry(worksheet.get_all_values)
    except Exception:
        return pd.DataFrame()

    if len(values) < 2:
        return pd.DataFrame()

    header = [h.strip() for h in values[0]]
    width = len(header)
    rows = [row[:width] + [""] * (width - len(row)) for row in values[1:]]
    frame = pd.DataFrame(rows, columns=header)

    frame = frame.loc[:, [c for c in frame.columns if c]]
    frame = frame[~(frame == "").all(axis=1)]
    return frame.reset_index(drop=True)


def to_number(series: pd.Series) -> pd.Series:
    """Turn spreadsheet text into numbers. '1,234' -> 1234. '' -> 0."""
    cleaned = series.astype(str).str.replace(",", "", regex=False).str.strip()
    return pd.to_numeric(cleaned, errors="coerce").fillna(0)


def get_column(frame: pd.DataFrame, name: str, default: str = "") -> pd.Series:
    """
    Always return a real column, even if the tab does not have it.

    Why: DataFrame.get("x", "") hands back a plain string when the column is
    missing, and a string has no .astype or .str - which crashes later. This
    returns an empty column of the right length instead.
    """
    if name in frame.columns:
        return frame[name]
    return pd.Series([default] * len(frame), index=frame.index, dtype="object")


# ---------------------------------------------------------------------------
# Your own posts
# ---------------------------------------------------------------------------

NUMERIC_POST_COLUMNS = [
    "like_count", "comments_count", "reach", "views", "impressions",
    "saved", "shares", "total_interactions", "profile_visits", "follows",
]


def nice_format(media_type: str, product_type: str) -> str:
    """One clear word for the post format."""
    product = str(product_type or "").upper()
    media = str(media_type or "").upper()

    if product == "REELS":
        return "Reel"
    if product == "STORY":
        return "Story"
    if media == "CAROUSEL_ALBUM":
        return "Carousel"
    if media == "VIDEO":
        return "Video"
    if media == "IMAGE":
        return "Image"
    return "Other"


def load_posts(client: SheetsClient) -> pd.DataFrame:
    """Read IG_Posts and add the calculated columns we need."""
    frame = read_tab(client, config.IG_POSTS_TAB)
    if frame.empty:
        return frame

    for column in NUMERIC_POST_COLUMNS:
        if column in frame.columns:
            frame[column] = to_number(frame[column])
        else:
            frame[column] = 0

    frame["post_date"] = pd.to_datetime(
        get_column(frame, "timestamp"), errors="coerce", utc=True
    )

    frame["format"] = [
        nice_format(m, p)
        for m, p in zip(
            get_column(frame, "media_type"), get_column(frame, "media_product_type")
        )
    ]

    frame["tag"] = [tag_caption(c) for c in get_column(frame, "caption")]

    reach = frame["reach"].replace(0, pd.NA)
    frame["save_rate"] = (frame["saved"] / reach * 100).fillna(0)
    frame["share_rate"] = (frame["shares"] / reach * 100).fillna(0)
    frame["follow_rate"] = (frame["follows"] / reach * 100).fillna(0)
    frame["engagement_rate"] = (
        (frame["like_count"] + frame["comments_count"]) / reach * 100
    ).fillna(0)

    return frame


def group_summary(frame: pd.DataFrame, by_column: str) -> pd.DataFrame:
    """
    Summarise posts by format or by tag.

    Two save-rate columns on purpose:
      combined = all saves divided by all reach  (big posts count more)
      per post = the average of each post's own rate  (every post counts once)
    They answer slightly different questions, so you see both.
    """
    if frame.empty or by_column not in frame.columns:
        return pd.DataFrame()

    grouped = frame.groupby(by_column)
    summary = pd.DataFrame({
        "posts": grouped.size(),
        "avg reach": grouped["reach"].mean().round(0),
        "total reach": grouped["reach"].sum(),
        "total saves": grouped["saved"].sum(),
        "save % combined": (
            grouped["saved"].sum() / grouped["reach"].sum().replace(0, pd.NA) * 100
        ).round(2),
        "save % per post": grouped["save_rate"].mean().round(2),
        "share % combined": (
            grouped["shares"].sum() / grouped["reach"].sum().replace(0, pd.NA) * 100
        ).round(2),
        "avg likes": grouped["like_count"].mean().round(0),
    })
    summary = summary.fillna(0).sort_values("save % combined", ascending=False)
    return summary.reset_index()


def posting_frequency(frame: pd.DataFrame, days: int = 90) -> dict:
    """How often do you post? Returns a small dictionary of plain facts."""
    if frame.empty or frame["post_date"].isna().all():
        return {"days": days, "posts": 0, "per_week": 0.0, "gap_days": 0.0}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    recent = frame[frame["post_date"] >= cutoff]
    count = len(recent)
    per_week = round(count / (days / 7), 2)

    dates = recent["post_date"].dropna().sort_values()
    gap = 0.0
    if len(dates) > 1:
        gaps = dates.diff().dropna().dt.total_seconds() / 86400
        gap = round(float(gaps.mean()), 1)

    return {"days": days, "posts": count, "per_week": per_week, "gap_days": gap}


def top_posts(frame: pd.DataFrame, by: str = "save_rate", count: int = 10,
              ascending: bool = False) -> pd.DataFrame:
    """The best (or worst) posts, as a small readable table."""
    if frame.empty:
        return pd.DataFrame()

    columns = ["date", "format", "tag", "reach", "saved", "save_rate",
               "like_count", "shares", "permalink"]
    available = [c for c in columns if c in frame.columns]

    solid = frame[frame["reach"] >= 200] if "reach" in frame.columns else frame
    if solid.empty:
        solid = frame

    result = solid.sort_values(by, ascending=ascending).head(count)[available].copy()
    if "save_rate" in result.columns:
        result["save_rate"] = result["save_rate"].round(2)
    return result.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Competitor data
# ---------------------------------------------------------------------------

def load_inspiration(client: SheetsClient) -> pd.DataFrame:
    """
    Read the Inspiration tab and remove duplicates.

    inspire.py APPENDS rows every run, so the same post appears once per run.
    We keep only the newest copy of each post (newest run_date wins).
    """
    frame = read_tab(client, "Inspiration")
    if frame.empty:
        return frame

    for column in ["followers", "likes", "comments", "normal_likes", "score"]:
        if column in frame.columns:
            frame[column] = to_number(frame[column])

    frame["outlier"] = (
        get_column(frame, "is_outlier").astype(str).str.upper().str.strip() == "YES"
    )

    if "permalink" in frame.columns and "run_date" in frame.columns:
        before = len(frame)
        frame = frame.sort_values("run_date").drop_duplicates(
            subset=["permalink"], keep="last"
        )
        frame.attrs["duplicates_removed"] = before - len(frame)

    return frame.reset_index(drop=True)


def outliers_by_account(frame: pd.DataFrame) -> pd.DataFrame:
    """How many REAL outliers each account has. This is the number Gemini got wrong."""
    if frame.empty or "username" not in frame.columns:
        return pd.DataFrame()

    grouped = frame.groupby("username")
    summary = pd.DataFrame({
        "posts read": grouped.size(),
        "outliers": grouped["outlier"].sum().astype(int),
        "normal likes": grouped["normal_likes"].median().round(0),
        "followers": grouped["followers"].max(),
    })
    summary["outlier %"] = (
        summary["outliers"] / summary["posts read"] * 100
    ).round(0)
    return summary.sort_values("outliers", ascending=False).reset_index()


def outliers_by_format(frame: pd.DataFrame) -> pd.DataFrame:
    """Which formats become outliers most often, across all accounts."""
    if frame.empty or "format" not in frame.columns:
        return pd.DataFrame()

    grouped = frame.groupby("format")
    summary = pd.DataFrame({
        "posts read": grouped.size(),
        "outliers": grouped["outlier"].sum().astype(int),
    })
    summary["outlier %"] = (
        summary["outliers"] / summary["posts read"] * 100
    ).round(0)
    return summary.sort_values("outlier %", ascending=False).reset_index()


def best_outliers(frame: pd.DataFrame, count: int = 25) -> pd.DataFrame:
    """The strongest outlier posts, for reading the hooks."""
    if frame.empty:
        return pd.DataFrame()

    columns = ["username", "group", "post_date", "format", "score",
               "likes", "normal_likes", "hook", "permalink"]
    available = [c for c in columns if c in frame.columns]

    winners = frame[frame["outlier"]] if "outlier" in frame.columns else frame
    if winners.empty:
        return pd.DataFrame()

    return (
        winners.sort_values("score", ascending=False)
        .head(count)[available]
        .reset_index(drop=True)
    )
