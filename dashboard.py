"""
dashboard.py
============

The desk. Everything you need to decide what to post, on one page.

Run it locally with:
    streamlit run dashboard.py

Four sections:
  1. My performance   - your own numbers, computed in Python
  2. Competitor ideas - outlier posts from other accounts
  3. What to post     - Gemini's written advice, built from the real numbers
  4. This week        - your Weekly_Plan and Backlog tabs

Every number here comes from stats.py. Gemini only writes sentences; it never
counts. That is on purpose.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import stats

# ---------------------------------------------------------------------------
# Look and feel
# ---------------------------------------------------------------------------

SERIES = "#2a78d6"       # one hue for magnitude bars
INK = "#0b0b0b"
INK_SOFT = "#52514e"
GRID = "#e6e5e1"
GOOD = "#1baf7a"
BAD = "#e34948"

st.set_page_config(
    page_title="ChessMood Social Desk",
    page_icon="♟️",
    layout="wide",
)


def bar_chart(labels, values, value_label: str, suffix: str = "", height: int = 300):
    """
    One horizontal bar chart, single colour.

    Single colour on purpose: the category name is already on the axis, so
    colour would repeat information instead of adding any. No legend needed.
    """
    order = list(range(len(labels)))
    figure = go.Figure(
        go.Bar(
            x=list(values),
            y=list(labels),
            orientation="h",
            marker=dict(color=SERIES, cornerradius=4),
            text=[f"{v:,.2f}{suffix}" if isinstance(v, float) else f"{v:,}{suffix}"
                  for v in values],
            textposition="outside",
            textfont=dict(color=INK_SOFT, size=12),
            hovertemplate="<b>%{y}</b><br>" + value_label + ": %{x}" + suffix
                          + "<extra></extra>",
        )
    )
    figure.update_layout(
        height=height,
        margin=dict(l=0, r=40, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        bargap=0.4,
        showlegend=False,
        font=dict(color=INK, size=13),
        xaxis=dict(
            title=dict(text=value_label, font=dict(color=INK_SOFT, size=12)),
            gridcolor=GRID, zeroline=False, showline=False,
        ),
        yaxis=dict(
            gridcolor="rgba(0,0,0,0)", zeroline=False, showline=False,
            categoryorder="array", categoryarray=list(labels)[::-1],
        ),
    )
    st.plotly_chart(figure, use_container_width=True)


# ---------------------------------------------------------------------------
# Optional password (only used when APP_PASSWORD is set, i.e. on the web)
# ---------------------------------------------------------------------------

def password_ok() -> bool:
    try:
        expected = st.secrets.get("APP_PASSWORD", "")
    except Exception:
        expected = os.getenv("APP_PASSWORD", "")

    if not expected:
        return True  # no password set - running locally

    if st.session_state.get("unlocked"):
        return True

    st.title("ChessMood Social Desk")
    typed = st.text_input("Password", type="password")
    if typed and typed == expected:
        st.session_state["unlocked"] = True
        st.rerun()
    elif typed:
        st.error("Wrong password.")
    return False


# ---------------------------------------------------------------------------
# Loading data (cached, so clicking around does not re-read the sheet)
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner=False)
def sheet_client():
    return stats.get_client()


@st.cache_data(ttl=900, show_spinner="Reading your Google Sheet...")
def load_everything():
    client = sheet_client()
    posts = stats.load_posts(client)
    inspiration = stats.load_inspiration(client)
    plan = stats.read_tab(client, "Weekly_Plan")
    backlog = stats.read_tab(client, "Backlog")
    ideas = stats.read_tab(client, "AI_Insights")
    return posts, inspiration, plan, backlog, ideas


def ideas_as_text(ideas_frame: pd.DataFrame) -> tuple[str, str]:
    """Rebuild the AI_Insights tab back into readable text."""
    if ideas_frame.empty or "line" not in ideas_frame.columns:
        return "", ""
    when = ""
    if "generated_at" in ideas_frame.columns and len(ideas_frame):
        when = str(ideas_frame["generated_at"].iloc[0])
    return "\n\n".join(str(x) for x in ideas_frame["line"]), when


# ---------------------------------------------------------------------------
# Making fresh ideas - from Python numbers, not from raw rows
# ---------------------------------------------------------------------------

def generate_ideas(format_summary, tag_summary, account_summary,
                   competitor_format_summary, hooks, frequency) -> str:
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        try:
            api_key = st.secrets.get("GEMINI_API_KEY", "")
        except Exception:
            api_key = ""
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")

    hook_lines = "\n".join(
        f"- {row.get('score')}x above normal | {row.get('format')} | "
        f"@{row.get('username')} | {row.get('hook')}"
        for _, row in hooks.iterrows()
    )

    prompt = f"""You are helping ChessMood, a company that teaches chess online,
decide what to post on Instagram.

Every number below was already calculated for you. Use ONLY these numbers.
Do not invent any count, average or percentage that is not written here.
If you want to say how many posts something is based on, copy the number from
the tables. If a number is not in the tables, do not state it.

MY OWN POSTS, BY FORMAT:
{format_summary.to_string(index=False)}

MY OWN POSTS, BY CONTENT TYPE:
{tag_summary.to_string(index=False)}

HOW OFTEN I POST:
{frequency['posts']} posts in the last {frequency['days']} days
= {frequency['per_week']} posts per week, about one every {frequency['gap_days']} days

COMPETITOR ACCOUNTS - HOW MANY OF THEIR POSTS BECAME OUTLIERS:
(an outlier got at least 2x that account's own normal likes)
{account_summary.to_string(index=False)}

WHICH FORMATS BECOME OUTLIERS, ACROSS ALL COMPETITOR ACCOUNTS:
{competitor_format_summary.to_string(index=False)}

THE STRONGEST OUTLIER POSTS AND THEIR OPENING LINES:
{hook_lines}

Write in VERY SIMPLE ENGLISH. Short sentences. The reader is not a native
English speaker. No marketing jargon.

Give exactly these four sections with these headings:

## What is working
Four sentences. Each one must quote a number from the tables above.

## What to stop doing
Two sentences, each with a number from the tables.

## Five posts for next week
Five ideas. For each: the opening line, the format
(carousel / reel / image), and one short sentence saying which number above
supports it.

## Where the data is too thin
Two sentences naming groups with too few posts to trust, using their counts.

Nothing else. Do not repeat the tables back to me.
"""

    client = genai.Client(api_key=api_key)

    # Google sometimes answers 503 UNAVAILABLE when its servers are busy.
    # That is temporary, so we wait and try again instead of giving up.
    last_error = None
    for attempt, wait_seconds in enumerate([0, 4, 10, 20], start=1):
        if wait_seconds:
            time.sleep(wait_seconds)
        try:
            response = client.models.generate_content(
                model="gemini-3.6-flash", contents=prompt
            )
            return response.text or "(Gemini returned nothing.)"
        except Exception as error:
            last_error = error
            if "503" not in str(error) and "UNAVAILABLE" not in str(error):
                raise
    raise RuntimeError(
        "Google's servers were busy on 4 tries. This is temporary - "
        f"wait a minute and click the button again.\n\nLast message: {last_error}"
    )


def save_ideas(text: str) -> str:
    """Write fresh ideas into the AI_Insights tab and return the timestamp."""
    client = sheet_client()
    worksheet, _, _ = client.ensure_worksheet("AI_Insights", ["generated_at", "line"])
    client._retry(worksheet.batch_clear, ["A2:B10000"])
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = [[stamp, line] for line in text.splitlines() if line.strip()]
    if rows:
        client._retry(
            worksheet.append_rows, rows,
            value_input_option="RAW", insert_data_option="INSERT_ROWS",
            table_range="A1",
        )
    return stamp


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

def main() -> None:
    if not password_ok():
        return

    st.title("♟️ ChessMood Social Desk")

    with st.sidebar:
        st.header("Controls")
        if st.button("Reload data from the sheet", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.caption("Data is cached for 15 minutes. Click above to force a reload.")

    posts, inspiration, plan, backlog, ideas = load_everything()

    if posts.empty:
        st.error("The IG_Posts tab is empty. Run  python collect.py  first.")
        return

    format_summary = stats.group_summary(posts, "format")
    tag_summary = stats.group_summary(posts, "tag")
    frequency = stats.posting_frequency(posts)
    account_summary = stats.outliers_by_account(inspiration)
    competitor_formats = stats.outliers_by_format(inspiration)
    hooks = stats.best_outliers(inspiration, 25)

    tab1, tab2, tab3, tab4 = st.tabs([
        "My performance", "Competitor ideas", "What to post", "This week",
    ])

    # ---------------- 1. My performance ----------------
    with tab1:
        total_reach = int(posts["reach"].sum())
        total_saves = int(posts["saved"].sum())
        overall_save = (total_saves / total_reach * 100) if total_reach else 0

        a, b, c, d = st.columns(4)
        a.metric("Posts collected", f"{len(posts):,}")
        b.metric("Posts per week", f"{frequency['per_week']}",
                 help=f"Last {frequency['days']} days. One post every "
                      f"{frequency['gap_days']} days on average.")
        c.metric("Save rate overall", f"{overall_save:.2f}%",
                 help="All saves divided by all reach.")
        d.metric("Total reach", f"{total_reach:,}")

        st.divider()

        left, right = st.columns(2)
        with left:
            st.subheader("Save rate by format")
            st.caption("Saves divided by reach. Higher means people keep the post.")
            if not format_summary.empty:
                bar_chart(format_summary["format"],
                          format_summary["save % combined"],
                          "Save rate", "%")
        with right:
            st.subheader("Average reach by format")
            st.caption("How many people one post of this format reaches.")
            if not format_summary.empty:
                ordered = format_summary.sort_values("avg reach", ascending=False)
                bar_chart(ordered["format"], ordered["avg reach"], "Average reach")

        st.markdown("**Full table by format**")
        st.dataframe(format_summary, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Save rate by content type")
        st.caption("Content types come from the keyword rules in tags_config.py. "
                   "Groups with very few posts are not yet trustworthy - check the "
                   "posts column.")
        if not tag_summary.empty:
            bar_chart(tag_summary["tag"], tag_summary["save % combined"],
                      "Save rate", "%", height=380)
        st.dataframe(tag_summary, use_container_width=True, hide_index=True)

        st.divider()
        best, worst = st.columns(2)
        with best:
            st.subheader("Your 10 best posts")
            st.caption("By save rate. Posts under 200 reach are left out.")
            st.dataframe(stats.top_posts(posts, "save_rate", 10),
                         use_container_width=True, hide_index=True,
                         column_config={"permalink": st.column_config.LinkColumn(
                             "link", display_text="open")})
        with worst:
            st.subheader("Your 10 weakest posts")
            st.caption("Same measure, other end. Look for a pattern.")
            st.dataframe(stats.top_posts(posts, "save_rate", 10, ascending=True),
                         use_container_width=True, hide_index=True,
                         column_config={"permalink": st.column_config.LinkColumn(
                             "link", display_text="open")})

    # ---------------- 2. Competitor ideas ----------------
    with tab2:
        if inspiration.empty:
            st.info("The Inspiration tab is empty. Run  python inspire.py  first.")
        else:
            removed = inspiration.attrs.get("duplicates_removed", 0)
            total_outliers = int(inspiration["outlier"].sum())

            a, b, c = st.columns(3)
            a.metric("Competitor posts read", f"{len(inspiration):,}")
            b.metric("Real outliers found", f"{total_outliers:,}")
            c.metric("Duplicate rows ignored", f"{removed:,}",
                     help="inspire.py adds rows every run. Only the newest copy "
                          "of each post is counted here.")

            st.divider()
            st.subheader("Which formats become outliers")
            st.caption("Out of all competitor posts read, how often each format "
                       "beat its own account's normal likes by 2x or more.")
            if not competitor_formats.empty:
                bar_chart(competitor_formats["format"],
                          competitor_formats["outlier %"],
                          "Share that became outliers", "%")
            st.dataframe(competitor_formats, use_container_width=True,
                         hide_index=True)

            st.divider()
            st.subheader("Outliers per account")
            st.caption("This is the honest count. An account can only have a few - "
                       "an outlier is measured against that same account's median.")
            st.dataframe(account_summary, use_container_width=True, hide_index=True)

            st.divider()
            st.subheader("The strongest hooks")
            st.caption("The first line of each outlier post. Copy the structure, "
                       "not the topic.")
            st.dataframe(hooks, use_container_width=True, hide_index=True,
                         column_config={"permalink": st.column_config.LinkColumn(
                             "link", display_text="open")})

    # ---------------- 3. What to post ----------------
    with tab3:
        st.subheader("What to post next")

        # The button comes FIRST, then the report right under it. So after you
        # click, the new text appears exactly where you are looking.
        left, right = st.columns([1, 2])
        with left:
            generate = st.button("Generate fresh ideas with Gemini",
                                 type="primary", use_container_width=True)
        with right:
            keep_copy = st.checkbox(
                "Also keep a copy in the Google Sheet",
                value=False,
                help="Off means the report lives on this page only. It is then "
                     "lost when the app restarts. On means it is also written "
                     "to the AI_Insights tab and comes back next time.",
            )

        if generate:
            if inspiration.empty:
                st.error("No competitor data yet. Run  python inspire.py  first.")
            else:
                with st.spinner("Asking Gemini... this takes 20-60 seconds."):
                    try:
                        fresh = generate_ideas(
                            format_summary, tag_summary, account_summary,
                            competitor_formats, hooks, frequency,
                        )
                        stamp = datetime.now(timezone.utc).strftime(
                            "%Y-%m-%d %H:%M UTC")
                        if keep_copy:
                            stamp = save_ideas(fresh)
                            st.cache_data.clear()
                        # Remember it for this browser session so it survives
                        # switching tabs and clicking around.
                        st.session_state["ideas_text"] = fresh
                        st.session_state["ideas_when"] = stamp
                        st.session_state["ideas_saved"] = keep_copy
                    except Exception as error:
                        st.error(f"Could not generate ideas:\n\n{error}")

        # What to show: this session's fresh report wins. Otherwise fall back
        # to whatever was saved in the sheet last time.
        text = st.session_state.get("ideas_text", "")
        when = st.session_state.get("ideas_when", "")
        is_fresh = bool(text)
        if not text:
            text, when = ideas_as_text(ideas)

        st.divider()

        if text:
            if is_fresh:
                where = ("saved to the sheet" if st.session_state.get("ideas_saved")
                         else "shown here only, not saved")
                st.success(f"Fresh report - generated {when} - {where}.")
            else:
                st.caption(f"Saved report from the Google Sheet - {when}. "
                           "Click the button above for a new one.")
            st.markdown(text)
        else:
            st.info("Nothing yet. Click the button above.")

        st.divider()
        st.caption("Gemini receives only the calculated tables from stats.py, "
                   "never raw rows. So it cannot invent a count.")

    # ---------------- 4. This week ----------------
    with tab4:
        st.subheader("This week's plan")
        if plan.empty:
            st.info("The Weekly_Plan tab is empty. Run  python plan.py  to fill it.")
        else:
            st.dataframe(plan, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Backlog")
        st.caption("The topics you wrote yourself. plan.py picks from here.")
        if backlog.empty:
            st.info("The Backlog tab is empty.")
        else:
            st.dataframe(backlog, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
