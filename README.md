# ChessMood Social Analytics Collector

Pulls Instagram and Facebook post performance out of the Meta Graph API every day
and writes it into a Google Sheet. No manual CSV exports.

---

## READ THIS FIRST

### 1. The Graph API version — settled

`config.py` defaults to **`v26.0`**, confirmed against Meta's changelog on
**2026-08-11**. Nothing to do right now.

It will go stale eventually: Meta releases a new version roughly every three
months and switches old ones **off** after about two years. When that happens,
override it in `.env` rather than editing `config.py`:

```
META_API_VERSION=v27.0
```

Doing it that way means an upgrade never touches code, and the GitHub Actions run
can be bumped by changing one secret. The changelog lives at
<https://developers.facebook.com/docs/graph-api/changelog>.

A retired version gives you a loud, clear error — it will not silently return
bad numbers.

### 2. Two of the metrics you asked for are probably dead

Meta retired several Instagram metrics around **v22.0 (2025)** and folded them
into a single new metric called `views`:

| Metric you asked for | Status | What to use instead |
|---|---|---|
| `impressions` (IG posts) | Very likely refused | `views` |
| `plays` (Reels) | Very likely refused | `views` |
| `reach`, `saved`, `shares`, `total_interactions` | Fine | — |
| `profile_visits`, `follows` | Only valid on **some** media types | expect blanks |

I kept `impressions` and `plays` in the request list because you asked for them,
**and** added a `views` column next to them. If Meta refuses them, the script
does not crash — it reports them in the summary and leaves those cells blank.
Once you have confirmed they are dead, delete them from `IG_MEDIA_METRICS` in
[config.py](config.py) and the script stops asking.

The Facebook metrics (`post_impressions`, `post_impressions_unique`,
`post_clicks`, `post_reactions_by_type_total`) are the ones I am *reasonably*
confident about, but Meta has also been trimming Page Insights. Same safety net
applies: refusals are reported, never fatal.

**Everything volatile lives in `config.py`.** When Meta changes something, that
is the only file you need to touch.

---

## What each file does

| File | In plain English |
|---|---|
| [.env.example](.env.example) | A template for your secrets. Copy it to `.env` and fill it in. |
| [.gitignore](.gitignore) | Tells git to never commit your secrets or key files. |
| [requirements.txt](requirements.txt) | The four Python packages this needs. |
| [config.py](config.py) | The control panel. Reads your secrets, holds every metric name, column name and tab name. **No internet code in here.** |
| [meta_client.py](meta_client.py) | Every conversation with Meta. Handles retries, rate limits, expired tokens, and metrics Meta refuses. |
| [sheets_client.py](sheets_client.py) | Every conversation with Google Sheets. Creates tabs, writes headers, and does the upsert (update-or-insert) logic. |
| [collect.py](collect.py) | The script you run. Fetch → transform → write → print a summary. |
| README.md | This file. |

---

## First-time setup

### Step 1 — Install Python packages

Open PowerShell in this folder (`chessmood-social`) and run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

The first line creates a private sandbox for this project's packages. The second
switches into it — you will see `(.venv)` at the start of your prompt. You need
to run that second line **every time you open a new terminal** for this project.

> If PowerShell blocks the activate script, run this once:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

### Step 2 — Get your Meta access token

1. Go to <https://developers.facebook.com/tools/explorer/>.
2. Select your app (create one if you have none — type "Business").
3. Click **Generate Access Token** and grant these permissions:
   - `instagram_basic`
   - `instagram_manage_insights`
   - `pages_show_list`
   - `pages_read_engagement`
   - `read_insights`
4. Copy the token, then paste it into
   <https://developers.facebook.com/tools/debug/accesstoken/> and click
   **Extend Access Token**. That turns a 1-hour token into a ~60-day one.

⚠️ Even the long one expires. When it does, `collect.py` stops with a plain
message telling you to come back here — not a stack trace.

### Step 3 — Find your two IDs

With your token in hand, paste these into a browser (swap in your own values):

```
https://graph.facebook.com/v26.0/me/accounts?access_token=YOUR_TOKEN
```
→ gives you the Facebook Page **id** → that is `FB_PAGE_ID`.

```
https://graph.facebook.com/v26.0/YOUR_PAGE_ID?fields=instagram_business_account&access_token=YOUR_TOKEN
```
→ gives you `instagram_business_account.id` → that is `IG_BUSINESS_ACCOUNT_ID`.

> If the second one comes back empty, your Instagram account is not yet linked to
> the Facebook Page, or it is not a Business/Creator account. Fix that in the
> Instagram app under Settings → Account type, and in Meta Business Suite.

### Step 4 — Set up Google Sheets access

1. Go to <https://console.cloud.google.com/> and create a project (or reuse one).
2. **APIs & Services → Library** → search "Google Sheets API" → **Enable**.
3. **APIs & Services → Credentials → Create credentials → Service account**.
   Give it any name; no roles needed.
4. Open the new service account → **Keys → Add key → Create new key → JSON**.
   A `.json` file downloads. This is your `GOOGLE_SERVICE_ACCOUNT_JSON`.
5. Open that JSON and find `"client_email"` — something like
   `my-bot@my-project.iam.gserviceaccount.com`.
6. Create your Google Sheet, click **Share**, and invite that email address as an
   **Editor**. **This step is the one people forget** — without it the script
   cannot see your sheet, and it will tell you so.
7. Copy the sheet ID from the URL:
   `docs.google.com/spreadsheets/d/`**`THIS_LONG_PART`**`/edit`

> You may already have a service account from your Progress Tracker project — if
> so you can reuse the same key file here. Just remember to share the **new**
> sheet with it too.

### Step 5 — Create your `.env`

```powershell
copy .env.example .env
notepad .env
```

Fill in all five values. For `GOOGLE_SERVICE_ACCOUNT_JSON` you have three
options, easiest first:

- **Local testing:** just paste the file path, e.g. `C:\keys\chessmood-key.json`
- **One line of JSON:** paste the whole file with the line breaks removed
- **Base64** (no quoting headaches at all):
  ```powershell
  [Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\keys\chessmood-key.json"))
  ```
  and paste that string.

The script accepts all three. GitHub Actions has no files on disk, so use option
2 or 3 there.

### Step 6 — Do a tiny test run

Never point a brand-new script at 90 days of data on the first try:

```powershell
python collect.py --limit 3 --days 7 --dry-run
```

`--dry-run` means "talk to Meta but write nothing". You should see it connect,
find a few posts, and print a summary. If a metric is refused, that is normal and
the summary explains which one.

Then let it actually write, still small:

```powershell
python collect.py --limit 3 --days 7
```

Check your Google Sheet — three tabs should now exist with headers and a few
rows.

### Step 7 — The real run

```powershell
python collect.py
```

90 days of posts, all insights, all three tabs. Expect a few minutes: it makes
one insights request per post, and pauses politely if Meta says slow down.

---

## Running it every day

Just run `python collect.py` again. Because it **upserts**, re-running is always
safe:

- A post already in the sheet → its row is **updated** in place
- A post that is new → **appended** at the bottom
- A row whose numbers have not changed → **skipped entirely** (saves quota)

This matters because engagement keeps climbing for days after a post goes live.
Running daily means the sheet always holds current numbers, and the summary tells
you exactly what moved.

### Later: GitHub Actions

You said this is where it is heading. When you get there, add these five
repository secrets under **Settings → Secrets and variables → Actions**, with the
same names as in `.env`:

`META_ACCESS_TOKEN`, `IG_BUSINESS_ACCOUNT_ID`, `FB_PAGE_ID`,
`GOOGLE_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`

Then create `.github/workflows/collect.yml`:

```yaml
name: Collect social stats

on:
  schedule:
    - cron: "0 5 * * *"     # 05:00 UTC every day
  workflow_dispatch:         # also lets you click "Run workflow" by hand

jobs:
  collect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - run: pip install -r requirements.txt

      - run: python collect.py
        env:
          META_ACCESS_TOKEN: ${{ secrets.META_ACCESS_TOKEN }}
          IG_BUSINESS_ACCOUNT_ID: ${{ secrets.IG_BUSINESS_ACCOUNT_ID }}
          FB_PAGE_ID: ${{ secrets.FB_PAGE_ID }}
          GOOGLE_SHEET_ID: ${{ secrets.GOOGLE_SHEET_ID }}
          GOOGLE_SERVICE_ACCOUNT_JSON: ${{ secrets.GOOGLE_SERVICE_ACCOUNT_JSON }}
```

No code changes needed — `config.py` reads plain environment variables and only
falls back to `.env` when one exists.

---

## All the command line options

```powershell
python collect.py                  # normal daily run (90 days)
python collect.py --days 7         # last week only
python collect.py --limit 5        # first 5 posts per platform (testing)
python collect.py --dry-run        # fetch everything, write nothing
python collect.py --skip-fb        # Instagram only
python collect.py --skip-ig        # Facebook only
python collect.py --skip-daily     # no DailyStats tab
python collect.py --quiet          # hide retry/rate-limit chatter
python collect.py --help           # show this list
```

---

## The sheet layout

**IG_Posts** — one row per Instagram post, keyed on `id`

`id`, `timestamp`, `date`, `media_type`, `media_product_type`, `permalink`,
`caption`, `like_count`, `comments_count`, `reach`, `views`, `impressions`,
`saved`, `shares`, `total_interactions`, `profile_visits`, `follows`, `plays`,
`last_updated`

**FB_Posts** — one row per Facebook post, keyed on `id`

`id`, `created_time`, `date`, `status_type`, `permalink_url`, `message`, `reach`,
`impressions`, `reactions`, `comments`, `shares`, `clicks`, `last_updated`

Where the Facebook numbers come from:

| Column | Source |
|---|---|
| `reach` | insight `post_impressions_unique` |
| `impressions` | insight `post_impressions` |
| `clicks` | insight `post_clicks` |
| `reactions` | insight `post_reactions_by_type_total`, summed; falls back to the post's own reaction count |
| `comments` | the post's `comments.summary.total_count` (not an insight) |
| `shares` | the post's `shares.count` (not an insight) |

**DailyStats** — one row per day, keyed on `date`

`date`, `follower_count`, `reach`, `profile_views`, `last_updated`

Notes on DailyStats:
- Instagram only allows a 30-day window per request, so the script slices your
  90 days into 29-day chunks automatically.
- Instagram only returns `follower_count` for roughly the **last 30 days**, so
  older rows will legitimately be blank in that column.
- The date comes from Meta's `end_time`, which is the end of the reporting day.

**Adding your own columns is safe.** If you add a column by hand, the script
keeps it, keeps your column order, and appends any new columns of its own to the
end rather than shuffling your data.

---

## When something goes wrong

| What you see | What it means |
|---|---|
| `META ACCESS TOKEN PROBLEM` | Token expired. Redo Step 2, update `.env`. |
| `not allowed to open the sheet` | You skipped Step 4.6 — share the sheet with the service account email. |
| `Metrics Meta refused: impressions` | Expected. See the top of this README. |
| `rate limit` messages, then it continues | Working as designed. It waits and retries. |
| `Still rate limited after 6 attempts` | You have hit Meta's hourly cap. Wait an hour. Use `--limit` while testing. |
| `GOOGLE_SERVICE_ACCOUNT_JSON could not be read as JSON` | Line breaks in `.env`. Use the base64 or file-path option. |
| `Facebook rejected the 'published_posts' edge` | Harmless — it automatically retried with `posts`. |
| `No spreadsheet found with ID` | Wrong `GOOGLE_SHEET_ID`; copy it from the URL again. |

Anything genuinely unexpected prints a full Python traceback — that is a bug in
the script, not a configuration problem.

---

## How the safety nets actually work

**Retries.** Every Meta request goes through one function. Network errors and
Meta 5xx errors are retried after 2s, 4s, 8s, 16s, 32s. Rate limits are retried
after 30s, 60s, 120s. The script also reads Meta's usage headers and pauses for
60s on its own if it is above 90% of quota — better to slow down than get blocked.

**Refused metrics.** Meta rejects the *entire* request if one metric name is
invalid for that post type. So: ask for everything in one fast request; if that
is refused, ask for each metric individually; if a single metric still fails, try
once more with `metric_type=total_value` (some newer metrics only exist in that
shape). Whatever is left over is recorded and reported at the end. **A bad metric
name can never stop the run.**

**Upsert.** The script reads the tab once, builds a `post id → row number` map,
then sends targeted writes for changed rows and a single bulk append for new
ones. Rows whose numbers have not moved are not written at all, so the
"updated" count in the summary is a real signal, not noise.
