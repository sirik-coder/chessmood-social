"""
meta_client.py
==============

Every single conversation with Meta happens in this file. Nothing else in the
project knows what an HTTP request looks like.

The three things this file is careful about:

  1. RETRIES - the internet is unreliable and Meta rate-limits you. Any request
     that fails for a *temporary* reason is retried with growing waits.
  2. TOKEN EXPIRY - if your access token died, you get one clear sentence telling
     you to refresh it, not a wall of red text.
  3. REFUSED METRICS - Meta rejects the whole request if ONE metric name is
     invalid for that post. So when a batch request fails, we automatically retry
     one metric at a time and simply report which ones were refused.
"""

from __future__ import annotations

import time
from typing import Any, Iterator

import requests

import config


# ===========================================================================
# Custom error types - these let collect.py react differently to each problem
# ===========================================================================

class MetaError(RuntimeError):
    """Base class: something went wrong talking to Meta."""


class MetaTokenError(MetaError):
    """Your access token is expired, revoked, or missing permissions.
    Nothing will work until you refresh it, so we stop the whole run."""


class MetaPermanentError(MetaError):
    """Meta says this exact request is invalid and always will be - e.g. a metric
    name that does not exist for this post type. Retrying would be pointless.
    This is the error we catch and turn into 'metric refused'."""


class MetaTemporaryError(MetaError):
    """Server hiccup, timeout, or rate limit - worth retrying, but we ran out of
    attempts."""


# Meta error codes that mean "you are going too fast, slow down".
RATE_LIMIT_CODES = {
    4,      # application-level request limit
    17,     # user-level request limit
    32,     # page-level request limit
    613,    # custom-level throttling
    80001,  # business use case rate limit (pages)
    80002,  # business use case rate limit (instagram)
    80003,
    80004,  # instagram platform rate limit
    80014,
}

# Meta error codes that mean "your token is no good".
TOKEN_ERROR_CODES = {102, 190, 458, 459, 463, 464, 467}

# Sub-codes under error code 190 that specifically mean expiry / revocation.
TOKEN_ERROR_SUBCODES = {458, 459, 460, 463, 464, 467, 492}


class MetaClient:
    """
    A thin, patient wrapper around the Meta Graph API.

    Create it once:
        meta = MetaClient(token)          # uses config.GRAPH_API_VERSION
    then call the get_* methods.
    """

    def __init__(
        self,
        access_token: str,
        base_url: str | None = None,
        timeout: int = 30,
        max_retries: int = 5,
        verbose: bool = True,
    ) -> None:
        self.access_token = access_token
        self.base_url = (base_url or config.GRAPH_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.verbose = verbose

        # A Session reuses the TCP connection, which makes hundreds of small
        # requests noticeably faster.
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})

        # Bookkeeping so collect.py can print a useful summary at the end.
        self.request_count = 0
        self.retry_count = 0

    # -----------------------------------------------------------------------
    # Small helpers
    # -----------------------------------------------------------------------

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    @staticmethod
    def _backoff_seconds(attempt: int, rate_limited: bool) -> float:
        """
        How long to wait before retry number `attempt` (0-based).

        Normal errors:  2s, 4s, 8s, 16s, 32s  (capped at 60s)
        Rate limits:   30s, 60s, 120s        (capped at 120s) - Meta's counters
                                              reset on a rolling hourly window,
                                              so waiting a bit longer really helps.
        """
        if rate_limited:
            return min(30.0 * (2 ** attempt), 120.0)
        return min(2.0 * (2 ** attempt), 60.0)

    def _check_usage_headers(self, response: requests.Response) -> None:
        """
        Meta tells you how close you are to your limits in response headers.
        If we are above 90% of any quota, pause voluntarily. This is much nicer
        than getting blocked and having to back off after the fact.
        """
        import json as _json

        for header in ("X-App-Usage", "X-Ad-Account-Usage", "X-Business-Use-Case-Usage"):
            raw = response.headers.get(header)
            if not raw:
                continue
            try:
                parsed = _json.loads(raw)
            except ValueError:
                continue

            # The header is either {"call_count": 12, ...} or
            # {"<id>": [{"call_count": 12, ...}]} depending on the header.
            buckets: list[dict] = []
            if isinstance(parsed, dict):
                for value in parsed.values():
                    if isinstance(value, list):
                        buckets.extend(v for v in value if isinstance(v, dict))
                if all(not isinstance(v, list) for v in parsed.values()):
                    buckets.append(parsed)

            for bucket in buckets:
                percentages = [
                    bucket.get("call_count", 0),
                    bucket.get("total_cputime", 0),
                    bucket.get("total_time", 0),
                ]
                worst = max(p for p in percentages if isinstance(p, (int, float)))
                if worst >= 90:
                    self._log(
                        f"   [rate limit] Meta reports {worst}% of quota used "
                        f"({header}). Pausing 60s to stay safe."
                    )
                    time.sleep(60)
                    return

    # -----------------------------------------------------------------------
    # THE core request method - everything else goes through here
    # -----------------------------------------------------------------------

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        token: str | None = None,
        full_url: str | None = None,
    ) -> dict:
        """
        Perform one GET request against the Graph API, with retries.

        path      : e.g. "17841400000000000/media" (no leading slash needed)
        params    : query string values; the access token is added automatically
        token     : use a different token than the default (Facebook Page tokens)
        full_url  : used for pagination - Meta hands back a complete "next" URL

        Returns the parsed JSON as a dictionary, or raises one of the Meta*Error
        types above.
        """
        url = full_url or f"{self.base_url}/{path.lstrip('/')}"

        query = dict(params or {})
        if full_url:
            # Meta's "next" URLs already contain every parameter including the
            # token, so we must not add anything or we risk duplicating it.
            query = {}
        else:
            query["access_token"] = token or self.access_token

        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            # --- send it ---------------------------------------------------
            try:
                self.request_count += 1
                response = self.session.get(url, params=query, timeout=self.timeout)
            except requests.RequestException as exc:
                # No response at all: DNS failure, timeout, dropped connection.
                last_error = exc
                if attempt < self.max_retries:
                    wait = self._backoff_seconds(attempt, rate_limited=False)
                    self.retry_count += 1
                    self._log(f"   [network] {type(exc).__name__}; retrying in {wait:.0f}s")
                    time.sleep(wait)
                    continue
                raise MetaTemporaryError(
                    f"Could not reach Meta after {self.max_retries + 1} attempts: {exc}"
                ) from exc

            # --- happy path ------------------------------------------------
            if response.status_code == 200:
                self._check_usage_headers(response)
                try:
                    return response.json()
                except ValueError as exc:
                    raise MetaTemporaryError(
                        f"Meta returned 200 OK but the body was not JSON: "
                        f"{response.text[:200]}"
                    ) from exc

            # --- something went wrong: dig the details out of the body -----
            try:
                error = (response.json() or {}).get("error", {}) or {}
            except ValueError:
                error = {}

            code = error.get("code")
            subcode = error.get("error_subcode")
            message = error.get("message") or response.text[:300] or "(no message)"
            is_transient = bool(error.get("is_transient"))

            # 1) Token problems - stop everything, this cannot be retried away.
            if code in TOKEN_ERROR_CODES or subcode in TOKEN_ERROR_SUBCODES:
                raise MetaTokenError(self._token_help(code, subcode, message))

            # 2) Rate limits - wait longer and try again.
            if response.status_code == 429 or code in RATE_LIMIT_CODES:
                if attempt < self.max_retries:
                    wait = self._backoff_seconds(attempt, rate_limited=True)
                    self.retry_count += 1
                    self._log(
                        f"   [rate limit] Meta code {code}: {message[:120]} "
                        f"- waiting {wait:.0f}s"
                    )
                    time.sleep(wait)
                    continue
                raise MetaTemporaryError(
                    f"Still rate limited after {self.max_retries + 1} attempts. "
                    f"Wait an hour and run again. Meta said: {message}"
                )

            # 3) Meta's own servers broke, or Meta flagged it as temporary.
            if response.status_code >= 500 or is_transient or code in (1, 2):
                if attempt < self.max_retries:
                    wait = self._backoff_seconds(attempt, rate_limited=False)
                    self.retry_count += 1
                    self._log(
                        f"   [server] HTTP {response.status_code} code {code}; "
                        f"retrying in {wait:.0f}s"
                    )
                    time.sleep(wait)
                    continue
                raise MetaTemporaryError(
                    f"Meta kept failing with HTTP {response.status_code}: {message}"
                )

            # 4) Anything else is a permanent "your request is wrong" - most
            #    often an unsupported metric name (code 100). We raise a
            #    PermanentError so the metric-fallback logic can catch it.
            raise MetaPermanentError(
                f"HTTP {response.status_code} code={code} subcode={subcode}: {message}"
            )

        # Should be unreachable, but never leave a loop without a definite end.
        raise MetaTemporaryError(f"Request to {url} failed: {last_error}")

    @staticmethod
    def _token_help(code: Any, subcode: Any, message: str) -> str:
        """Build the friendly 'go refresh your token' message."""
        return (
            "\n"
            "================== META ACCESS TOKEN PROBLEM ==================\n"
            f"Meta rejected the token (error code {code}, subcode {subcode}).\n"
            f"Meta's own words: {message}\n"
            "\n"
            "This is almost always an EXPIRED TOKEN. To fix it:\n"
            "  1. Go to https://developers.facebook.com/tools/explorer/\n"
            "  2. Pick your app, then 'Generate Access Token'.\n"
            "  3. Tick these permissions:\n"
            "       instagram_basic, instagram_manage_insights,\n"
            "       pages_show_list, pages_read_engagement, read_insights\n"
            "  4. Exchange it for a long-lived token at\n"
            "     https://developers.facebook.com/tools/debug/accesstoken/\n"
            "     (click 'Extend Access Token' - gives you ~60 days)\n"
            "  5. Put the new value in META_ACCESS_TOKEN in your .env file\n"
            "     (and in your GitHub repository secret, if you use Actions).\n"
            "\n"
            "Nothing was written to the Google Sheet. Re-run after updating.\n"
            "==============================================================="
        )

    # -----------------------------------------------------------------------
    # Pagination
    # -----------------------------------------------------------------------

    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        token: str | None = None,
        max_pages: int | None = None,
    ) -> Iterator[dict]:
        """
        Walk through a paged list endpoint and yield items one at a time.

        Meta returns results in chunks with a paging.next URL pointing at the
        following chunk. We keep following it until it disappears.
        """
        limit = max_pages if max_pages is not None else config.MAX_PAGES
        next_url: str | None = None
        pages = 0

        while pages < limit:
            if next_url:
                payload = self.get("", full_url=next_url)
            else:
                payload = self.get(path, params, token=token)

            pages += 1

            for item in payload.get("data", []) or []:
                yield item

            next_url = ((payload.get("paging") or {}).get("next")) or None
            if not next_url:
                return

        self._log(
            f"   [warning] Stopped paging after {limit} pages (MAX_PAGES). "
            f"Raise MAX_PAGES in .env if you expect more data than this."
        )

    # -----------------------------------------------------------------------
    # Insights, with per-metric graceful degradation
    # -----------------------------------------------------------------------

    def fetch_insights(
        self,
        object_id: str,
        metrics: list[str],
        extra_params: dict[str, Any] | None = None,
        token: str | None = None,
    ) -> tuple[list[dict], dict[str, str]]:
        """
        Ask for a list of insight metrics on one object (a post, or an account).

        THE IMPORTANT BEHAVIOUR: Meta rejects the ENTIRE request if even one
        metric name is not valid for that object. So:

          Step 1: ask for all metrics in a single fast request.
          Step 2: if that is refused, ask for each metric on its own. Now we know
                  exactly which ones Meta dislikes, and we keep the good ones.
          Step 3: for a metric that failed alone, try once more with
                  metric_type=total_value, because some newer Instagram metrics
                  are only available in that shape.

        Returns:
          (raw_metric_objects, refusals)
          refusals is {metric_name: reason} - never raises for a bad metric.
        """
        path = f"{object_id}/insights"
        base = dict(extra_params or {})

        # ---- Step 1: one request for everything ----------------------------
        try:
            params = dict(base)
            params["metric"] = ",".join(metrics)
            payload = self.get(path, params, token=token)
            return (payload.get("data", []) or []), {}
        except MetaPermanentError:
            pass  # fall through to the slow, careful path

        # ---- Step 2 + 3: one request per metric ----------------------------
        collected: list[dict] = []
        refusals: dict[str, str] = {}

        for metric in metrics:
            params = dict(base)
            params["metric"] = metric
            try:
                payload = self.get(path, params, token=token)
                collected.extend(payload.get("data", []) or [])
                continue
            except MetaPermanentError as first_error:
                reason = str(first_error)

            # Second chance with metric_type=total_value.
            params_tv = dict(base)
            params_tv["metric"] = metric
            params_tv["metric_type"] = "total_value"
            try:
                payload = self.get(path, params_tv, token=token)
                collected.extend(payload.get("data", []) or [])
            except MetaPermanentError:
                refusals[metric] = reason

        return collected, refusals

    @staticmethod
    def flatten_insights(items: list[dict]) -> dict[str, Any]:
        """
        Turn Meta's verbose insights response into a simple {name: number} dict.

        Meta gives you one of these shapes per metric:
            {"name": "reach", "values": [{"value": 1234}]}
            {"name": "views", "total_value": {"value": 1234}}
            {"name": "post_reactions_by_type_total",
             "values": [{"value": {"like": 10, "love": 3}}]}   <- a breakdown

        The last one is a dictionary of sub-counts, so we add them up.
        """
        result: dict[str, Any] = {}

        for item in items or []:
            name = item.get("name")
            if not name:
                continue

            value: Any = None

            total_value = item.get("total_value")
            if isinstance(total_value, dict):
                value = total_value.get("value")
            else:
                values = item.get("values") or []
                if values and isinstance(values[-1], dict):
                    value = values[-1].get("value")

            # Sum a breakdown dict (Facebook reactions by type).
            if isinstance(value, dict):
                numbers = [v for v in value.values() if isinstance(v, (int, float))]
                value = sum(numbers) if numbers else None

            # Never overwrite a real number with a blank from a duplicate entry.
            if name not in result or result[name] in (None, ""):
                result[name] = value

        return result

    # -----------------------------------------------------------------------
    # INSTAGRAM
    # -----------------------------------------------------------------------

    def get_ig_media(
        self,
        ig_user_id: str,
        since_timestamp: int,
        limit: int | None = None,
    ) -> list[dict]:
        """
        Fetch Instagram posts newer than `since_timestamp` (a unix timestamp).

        Meta returns media newest-first, so we page forward and stop as soon as
        we hit a post older than our cutoff. We deliberately do NOT rely on the
        API's since/until parameters here, because support for them on this
        particular edge has been inconsistent - checking the timestamp ourselves
        always works.
        """
        from datetime import datetime, timezone

        cutoff = datetime.fromtimestamp(since_timestamp, tz=timezone.utc)
        posts: list[dict] = []

        params = {
            "fields": ",".join(config.IG_MEDIA_FIELDS),
            "limit": config.PAGE_SIZE,
        }

        for item in self.paginate(f"{ig_user_id}/media", params):
            posted_at = _parse_meta_time(item.get("timestamp"))

            # Once we are past the cutoff, everything after is older too.
            if posted_at is not None and posted_at < cutoff:
                break

            posts.append(item)

            if limit and len(posts) >= limit:
                break

        return posts

    def get_ig_account_daily(
        self,
        ig_user_id: str,
        metrics: list[str],
        since_timestamp: int,
        until_timestamp: int,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        """
        Fetch account-level daily insights (followers, reach, profile views).

        Instagram only allows a 30-day window per request, so we slice the whole
        period into ~29-day chunks and stitch the results together.

        Returns ({"2026-08-11": {"reach": 500, ...}, ...}, refusals)
        """
        results: dict[str, dict[str, Any]] = {}
        refusals: dict[str, str] = {}

        window = 29 * 86400  # seconds; stay safely under Meta's 30-day cap
        start = since_timestamp

        while start < until_timestamp:
            end = min(start + window, until_timestamp)

            items, refused = self.fetch_insights(
                ig_user_id,
                metrics,
                extra_params={"period": "day", "since": start, "until": end},
            )
            refusals.update(refused)

            # Unlike a post, an account metric is a TIME SERIES: one value per
            # day, each tagged with the end of that day.
            for item in items:
                name = item.get("name")
                if not name:
                    continue
                for point in item.get("values") or []:
                    end_time = point.get("end_time")
                    day = (end_time or "")[:10]  # "2026-08-11T07:00:00+0000" -> date
                    if not day:
                        continue
                    results.setdefault(day, {})[name] = point.get("value")

            start = end + 1  # +1 second so windows never overlap

        return results, refusals

    # -----------------------------------------------------------------------
    # FACEBOOK
    # -----------------------------------------------------------------------

    def get_page_access_token(self, page_id: str) -> str | None:
        """
        Facebook Page *insights* usually require a PAGE access token, not a user
        token. Handily, you can swap a user token for a page token in one call.

        Returns the page token, or None if we could not get one (in which case we
        just keep using the token you supplied - which is correct if you already
        pasted a page token into .env).
        """
        try:
            data = self.get(page_id, {"fields": "access_token"})
            page_token = data.get("access_token")
            if page_token:
                self._log("   Got a Facebook Page access token for insight calls.")
                return page_token
        except MetaTokenError:
            raise  # a dead token must still stop the run
        except MetaError as exc:
            self._log(
                f"   [note] Could not fetch a Page token ({exc}). "
                f"Continuing with the token from .env."
            )
        return None

    def get_fb_posts(
        self,
        page_id: str,
        since_timestamp: int,
        token: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """
        Fetch Facebook Page posts newer than `since_timestamp`.

        Facebook's feed edges DO reliably support `since`, so we pass it - and
        still double-check each post's date, belt and braces.

        If the configured edge (published_posts) is rejected, we retry once with
        the older "posts" edge instead of giving up.
        """
        from datetime import datetime, timezone

        cutoff = datetime.fromtimestamp(since_timestamp, tz=timezone.utc)
        params = {
            "fields": ",".join(config.FB_POST_FIELDS),
            "limit": config.PAGE_SIZE,
            "since": since_timestamp,
        }

        edges = [config.FB_POSTS_EDGE]
        if config.FB_POSTS_EDGE != "posts":
            edges.append("posts")  # fallback

        last_error: Exception | None = None

        for edge in edges:
            posts: list[dict] = []
            try:
                for item in self.paginate(f"{page_id}/{edge}", params, token=token):
                    posted_at = _parse_meta_time(item.get("created_time"))
                    if posted_at is not None and posted_at < cutoff:
                        break
                    posts.append(item)
                    if limit and len(posts) >= limit:
                        break
                return posts
            except MetaPermanentError as exc:
                last_error = exc
                self._log(
                    f"   [note] Facebook rejected the '{edge}' edge ({exc}). "
                    f"Trying the next option."
                )

        raise MetaPermanentError(
            f"Could not read Facebook posts from any of {edges}. Last error: {last_error}"
        )


# ===========================================================================
# One shared date helper - used by this file and by collect.py
# ===========================================================================

def _parse_meta_time(raw: str | None):
    """
    Turn a Meta timestamp into a Python datetime, or None if unparseable.

    Meta usually sends "2026-08-11T14:03:22+0000". Occasionally you see a "Z"
    suffix or fractional seconds, so we try a few shapes rather than crashing.
    """
    from datetime import datetime

    if not raw:
        return None

    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+0000"

    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


# Public alias so collect.py can import it with a normal-looking name.
parse_meta_time = _parse_meta_time
