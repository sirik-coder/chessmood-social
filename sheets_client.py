"""
sheets_client.py
================

Every conversation with Google Sheets happens in this file.

The interesting part is `upsert`. "Upsert" = UPdate if the row exists, inSERT if
it does not. That matters here because Instagram and Facebook numbers keep
climbing for days after a post goes out, so the same post must be re-written with
fresh numbers every day - never duplicated.

How the upsert works, in plain English:
  1. Read the whole tab once (one API call).
  2. Build a lookup: {post id -> which spreadsheet row it lives on}.
  3. For each post we just fetched:
       - not in the lookup      -> collect it to be appended at the bottom
       - in the lookup, changed -> collect a targeted "write row 57" instruction
       - in the lookup, same    -> do nothing at all (saves API quota)
  4. Send all the updates in a few batches, then append all the new rows.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError, SpreadsheetNotFound, WorksheetNotFound

import config

# The only Google permission this script needs: read + write spreadsheets.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# HTTP statuses from Google that are worth retrying.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Google rejects enormous requests, so we send updates in bite-sized batches.
UPDATE_CHUNK_SIZE = 200   # how many individual row-writes per API call
APPEND_CHUNK_SIZE = 500   # how many new rows per API call


class SheetsError(RuntimeError):
    """Something went wrong with Google Sheets, explained in plain English."""


@dataclass
class UpsertResult:
    """The scoreboard returned by every upsert, used for the final summary."""

    tab: str
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_no_key: int = 0
    columns_added: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.new + self.updated + self.unchanged


# ===========================================================================
# Small pure helpers (no network) - easy to reason about and to test
# ===========================================================================

def column_letter(index_1_based: int) -> str:
    """
    Turn a column number into a spreadsheet letter. 1 -> A, 26 -> Z, 27 -> AA.
    We need this to build ranges like "A57:S57".
    """
    letters = ""
    n = index_1_based
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def normalize_for_compare(value: Any) -> str:
    """
    Make two values comparable as text.

    Why this exists: we write the number 1234, but when we read the sheet back
    Google may hand us the string "1234" or even "1,234". Without normalizing,
    every row would look "changed" on every run and we would burn through write
    quota for nothing.
    """
    if value is None:
        return ""

    text = str(value).strip()
    if text == "":
        return ""

    # Try to read it as a number so 1,234 == 1234 == 1234.0
    candidate = text.replace(",", "")
    try:
        number = float(candidate)
    except ValueError:
        return text  # it is real text (a caption, a URL) - compare as-is

    if number.is_integer():
        return str(int(number))
    return str(number)


def rows_differ(
    existing: Sequence[str],
    incoming: Sequence[Any],
    ignore_indexes: set[int],
) -> bool:
    """Compare two rows cell by cell, skipping the columns we were told to ignore."""
    for i, new_value in enumerate(incoming):
        if i in ignore_indexes:
            continue
        old_value = existing[i] if i < len(existing) else ""
        if normalize_for_compare(old_value) != normalize_for_compare(new_value):
            return True
    return False


# ===========================================================================
# The client
# ===========================================================================

class SheetsClient:
    """Opens one Google Sheet and reads/writes tabs inside it."""

    def __init__(
        self,
        service_account_info: dict,
        sheet_id: str,
        max_retries: int = 5,
        verbose: bool = True,
    ) -> None:
        self.sheet_id = sheet_id
        self.max_retries = max_retries
        self.verbose = verbose
        self.service_account_email = service_account_info.get("client_email", "(unknown)")

        # --- sign in ------------------------------------------------------
        try:
            credentials = Credentials.from_service_account_info(
                service_account_info, scopes=SCOPES
            )
        except Exception as exc:
            raise SheetsError(
                "Google rejected the service account key.\n"
                f"  Details: {exc}\n"
                "  Check that GOOGLE_SERVICE_ACCOUNT_JSON is the complete key file "
                "and that the private_key value was not truncated."
            ) from exc

        self.client = gspread.authorize(credentials)

        # --- open the spreadsheet ----------------------------------------
        try:
            self.spreadsheet = self._retry(self.client.open_by_key, sheet_id)
        except SpreadsheetNotFound as exc:
            raise SheetsError(
                f"No spreadsheet found with ID {sheet_id!r}.\n"
                "  Copy the ID from the middle of the sheet URL:\n"
                "  https://docs.google.com/spreadsheets/d/THIS_PART/edit"
            ) from exc
        except APIError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (403, 404):
                raise SheetsError(
                    "Google says this service account is not allowed to open the sheet.\n"
                    f"  Fix: open the Google Sheet, click Share, and invite\n"
                    f"      {self.service_account_email}\n"
                    f"  as an EDITOR. Then run this again.\n"
                    f"  (Google's raw message: {exc})"
                ) from exc
            raise SheetsError(f"Google Sheets error while opening the sheet: {exc}") from exc

        self._log(f"   Connected to Google Sheet: {self.spreadsheet.title!r}")

    # -----------------------------------------------------------------------
    # Plumbing
    # -----------------------------------------------------------------------

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    def _retry(self, func: Callable, *args, **kwargs):
        """
        Run a Google API call, retrying the temporary failures.

        Google's free quota is 60 read + 60 write requests per minute per user.
        Hitting it returns HTTP 429, which simply means "wait a moment".
        """
        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except APIError as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status in RETRYABLE_STATUS and attempt < self.max_retries:
                    wait = min(5 * (2 ** attempt), 90)
                    self._log(
                        f"   [sheets] HTTP {status} from Google; retrying in {wait}s"
                    )
                    time.sleep(wait)
                    continue
                raise
        raise SheetsError("Unreachable retry state")  # pragma: no cover

    # -----------------------------------------------------------------------
    # Making sure a tab exists with the right header row
    # -----------------------------------------------------------------------

    def ensure_worksheet(
        self, title: str, headers: list[str]
    ) -> tuple[gspread.Worksheet, list[str], list[str]]:
        """
        Guarantee that a tab called `title` exists and has our columns.

        Returns (worksheet, effective_header, newly_added_columns).

        "effective_header" is important: if the tab already existed with columns
        in a different order, we KEEP that order and simply append any of our
        columns that were missing. That way an existing sheet - including any
        extra columns you added by hand - never gets scrambled.
        """
        added: list[str] = []

        try:
            worksheet = self._retry(self.spreadsheet.worksheet, title)
        except WorksheetNotFound:
            self._log(f"   Tab {title!r} does not exist yet - creating it.")
            worksheet = self._retry(
                self.spreadsheet.add_worksheet,
                title=title,
                rows=1000,
                cols=max(len(headers) + 5, 26),
            )
            self._write_header(worksheet, headers)
            # "added" stays empty on a brand-new tab: we only want to report
            # columns that were added to a tab you already had.
            return worksheet, list(headers), []

        existing_header = [h for h in self._retry(worksheet.row_values, 1)]

        # Empty tab (or someone deleted the header row) -> write ours.
        if not any(h.strip() for h in existing_header):
            self._log(f"   Tab {title!r} has no header row - writing it.")
            self._write_header(worksheet, headers)
            return worksheet, list(headers), []

        # Tab exists with a header. Append anything of ours that is missing.
        effective = list(existing_header)
        for column in headers:
            if column not in effective:
                effective.append(column)
                added.append(column)

        if added:
            self._log(f"   Adding new column(s) to {title!r}: {', '.join(added)}")
            if len(effective) > worksheet.col_count:
                self._retry(worksheet.resize, rows=worksheet.row_count, cols=len(effective))
            self._write_header(worksheet, effective)

        return worksheet, effective, added

    def _write_header(self, worksheet: gspread.Worksheet, headers: list[str]) -> None:
        """Write the header row. batch_update is used everywhere because its
        signature is stable across gspread versions."""
        last = column_letter(len(headers))
        self._retry(
            worksheet.batch_update,
            [{"range": f"A1:{last}1", "values": [headers]}],
            value_input_option="RAW",
        )

    # -----------------------------------------------------------------------
    # The main event: upsert
    # -----------------------------------------------------------------------

    def upsert(
        self,
        tab: str,
        headers: list[str],
        rows: list[dict[str, Any]],
        key_column: str,
        ignore_columns: Sequence[str] = (config.TIMESTAMP_COLUMN,),
        dry_run: bool = False,
    ) -> UpsertResult:
        """
        Write `rows` into `tab`, updating existing rows instead of duplicating.

        tab            : e.g. "IG_Posts"
        headers        : the full list of column names we want
        rows           : list of dictionaries keyed by column name
        key_column     : the column holding the unique id (e.g. "id" or "date")
        ignore_columns : columns excluded from the "did it change?" comparison
        dry_run        : work out what WOULD happen but write nothing
        """
        worksheet, header, added = self.ensure_worksheet(tab, headers)
        result = UpsertResult(tab=tab, columns_added=added)

        if key_column not in header:
            raise SheetsError(
                f"Tab {tab!r} has no {key_column!r} column, so rows cannot be "
                f"matched up. Delete the tab and let the script rebuild it."
            )

        key_index = header.index(key_column)
        ignore_indexes = {header.index(c) for c in ignore_columns if c in header}
        width = len(header)
        last_column = column_letter(width)

        # Columns the tab has but WE do not manage - i.e. columns you added by
        # hand, like a "notes" or "campaign" column. We must never overwrite
        # those: when updating a row we copy the existing cell value straight
        # back, and we ignore them when deciding whether a row changed.
        managed = set(headers)
        unmanaged_indexes = {i for i, name in enumerate(header) if name not in managed}
        ignore_indexes |= unmanaged_indexes

        # --- 1. read the tab once ----------------------------------------
        all_values: list[list[str]] = self._retry(worksheet.get_all_values)

        # --- 2. map each existing key to its row number ------------------
        # Row 1 is the header, so real data starts at row 2.
        existing_rows: dict[str, tuple[int, list[str]]] = {}
        for offset, row in enumerate(all_values[1:], start=2):
            if key_index >= len(row):
                continue
            key = str(row[key_index]).strip()
            if not key:
                continue
            # If the sheet somehow already holds duplicates, the first wins and
            # later copies are simply left alone.
            existing_rows.setdefault(key, (offset, row))

        # --- 3. de-duplicate the incoming data ---------------------------
        # Meta's paging can occasionally hand back the same post twice. Keep the
        # last copy so we always write the freshest numbers.
        deduped: dict[str, dict[str, Any]] = {}
        for row_dict in rows:
            key = str(row_dict.get(key_column, "")).strip()
            if not key:
                result.skipped_no_key += 1
                continue
            deduped[key] = row_dict

        # --- 4. decide update vs append ----------------------------------
        pending_updates: list[dict[str, Any]] = []
        pending_appends: list[list[Any]] = []

        for key, row_dict in deduped.items():
            values = [row_dict.get(column, "") for column in header]

            if key in existing_rows:
                row_number, current = existing_rows[key]

                # Put back anything in a column we do not manage, so hand-added
                # columns survive the update untouched.
                for i in unmanaged_indexes:
                    values[i] = current[i] if i < len(current) else ""

                if rows_differ(current, values, ignore_indexes):
                    result.updated += 1
                    pending_updates.append(
                        {
                            "range": f"A{row_number}:{last_column}{row_number}",
                            "values": [values],
                        }
                    )
                else:
                    result.unchanged += 1
            else:
                result.new += 1
                pending_appends.append(values)

        if dry_run:
            self._log(f"   [dry run] {tab}: would write {len(pending_updates)} "
                      f"update(s) and {len(pending_appends)} new row(s).")
            return result

        # --- 5. send the updates in chunks --------------------------------
        for start in range(0, len(pending_updates), UPDATE_CHUNK_SIZE):
            chunk = pending_updates[start:start + UPDATE_CHUNK_SIZE]
            self._retry(worksheet.batch_update, chunk, value_input_option="RAW")

        # --- 6. append the new rows in chunks -----------------------------
        for start in range(0, len(pending_appends), APPEND_CHUNK_SIZE):
            chunk = pending_appends[start:start + APPEND_CHUNK_SIZE]
            self._retry(
                worksheet.append_rows,
                chunk,
                value_input_option="RAW",
                insert_data_option="INSERT_ROWS",
                table_range="A1",
            )

        return result
