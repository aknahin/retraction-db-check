#!/usr/bin/env python3
"""Check article titles against the Retraction Watch Database search page.

The input file should contain one title per line. A title is marked OK when the
search result page contains the site's "No Retractions found..." message.
Anything else is flagged and its HTML response is saved for review.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple

import requests


URL = "https://retractiondatabase.org/RetractionSearch.aspx?AspxAutoDetectCookieSupport=1"
DEFAULT_TITLES_PATH = Path("retraction_search_titles.txt")
DEFAULT_TITLE = "An intelligent system for false alarm reduction in infrared forest-fire detection"
NOT_FOUND_TEXT = "No Retractions found matching selected criteria"
DEFAULT_REPORT_PATH = Path("retraction_check_report.md")
DEFAULT_PROGRESS_PATH = Path("retraction_check_progress.jsonl")
DEFAULT_RESPONSE_DIR = Path("retraction_responses")
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 2
DEFAULT_DELAY = 0.5


class SearchResult(NamedTuple):
    index: int
    title: str
    ok: bool
    status_code: int
    html_path: Path | None
    snippet: str
    error: str | None = None
    elapsed_seconds: float = 0.0


class ProgressLogger:
    def __init__(self, path: Path | None, total: int) -> None:
        self.path = path
        self.total = total
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")
            self.write_event("run_started", index=0)

    def write_event(self, event: str, **data: object) -> None:
        if not self.path:
            return

        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            "total": self.total,
            **data,
        }
        with self.path.open("a", encoding="utf-8") as progress_file:
            progress_file.write(json.dumps(payload, ensure_ascii=False) + "\n")


class RetractionSearchClient:
    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        delay: float = DEFAULT_DELAY,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.delay = delay
        self.session = create_session()
        self._last_html: str | None = None

    def search_title(self, title: str) -> requests.Response:
        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            try:
                response = self._search_once(title)
                if self.delay:
                    time.sleep(self.delay)
                return response
            except (requests.RequestException, RuntimeError) as exc:
                last_error = exc
                self.session = create_session()
                self._last_html = None
                if attempt >= self.retries:
                    break
                time.sleep(min(2**attempt, 5))

        raise RuntimeError(str(last_error) if last_error else "search failed")

    def _search_once(self, title: str) -> requests.Response:
        html = self._last_html
        if html is None:
            page_response = fetch_page(self.session, self.timeout)
            html = page_response.text

        form_data = collect_inputs(html)
        form_data.update(
            {
                "__EVENTTARGET": "btnSearch",
                "__EVENTARGUMENT": "",
                "txtSrchTitle": title,
                "hidClearSearch": "N",
            }
        )
        response = self.session.post(URL, data=form_data, timeout=self.timeout)
        response.raise_for_status()
        self._last_html = response.text
        return response


class InputParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inputs: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "input":
            return

        attr_map = {name: value or "" for name, value in attrs}
        name = attr_map.get("name")
        if name:
            self.inputs[name] = attr_map.get("value", "")


def collect_inputs(html: str) -> dict[str, str]:
    parser = InputParser()
    parser.feed(html)
    return parser.inputs


def fetch_page(session: requests.Session, timeout: float = DEFAULT_TIMEOUT) -> requests.Response:
    response = session.get(URL, timeout=timeout, allow_redirects=False)
    for _ in range(10):
        if not response.is_redirect:
            response.raise_for_status()
            return response
        response = session.get(
            response.headers["Location"],
            timeout=timeout,
            allow_redirects=False,
        )

    raise RuntimeError("exceeded manual redirect limit")


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    session.cookies.set("AspxAutoDetectCookieSupport", "1", domain="retractiondatabase.org")
    return session


def read_titles(path: Path) -> list[str]:
    titles = []
    for line in path.read_text(encoding="utf-8").splitlines():
        title = line.strip()
        if title:
            titles.append(title)
    return titles


def safe_filename(index: int, title: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_").lower()
    return f"{index:04d}_{normalized[:80] or 'untitled'}.html"


def html_to_text(html: str) -> str:
    text = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", html, flags=re.I)
    text = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def make_snippet(html: str, limit: int = 1200) -> str:
    text = html_to_text(html)
    return text[:limit] + ("..." if len(text) > limit else "")


def escape_md(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`")


def write_report(results: list[SearchResult], report_path: Path) -> None:
    ok_count = sum(result.ok for result in results)
    bad_results = [result for result in results if not result.ok]

    lines = [
        "# Retraction Database Check Report",
        "",
        f"- Total titles checked: {len(results)}",
        f"- OK / no matching retractions: {ok_count}",
        f"- Flagged or failed: {len(bad_results)}",
        f"- Match rule: `{NOT_FOUND_TEXT}` means OK",
        "",
        "## Summary",
        "",
        "| # | Status | Title | Response | Duration |",
        "|---:|---|---|---|---:|",
    ]

    for result in results:
        status = "OK" if result.ok else "BAD" if result.error is None else "ERROR"
        response = str(result.html_path) if result.html_path else ""
        lines.append(
            f"| {result.index} | {status} | {escape_md(result.title)} | "
            f"{escape_md(response)} | {result.elapsed_seconds:.1f}s |"
        )

    if bad_results:
        lines.extend(["", "## Flagged Responses", ""])
        for result in bad_results:
            lines.extend(
                [
                    f"### {result.index}. {result.title}",
                    "",
                    f"- Status code: {result.status_code}",
                    f"- Saved response: {result.html_path or 'not saved'}",
                ]
            )
            if result.error:
                lines.extend(["", f"Error: `{escape_md(result.error)}`"])
            else:
                lines.extend(["", "Response snippet:", "", "```text", result.snippet, "```"])
            lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


def run_batch(
    titles: list[str],
    response_dir: Path,
    save_all: bool,
    timeout: float,
    retries: int,
    delay: float,
    progress_path: Path | None,
) -> list[SearchResult]:
    client = RetractionSearchClient(timeout=timeout, retries=retries, delay=delay)
    progress = ProgressLogger(progress_path, len(titles))
    results = []

    for index, title in enumerate(titles, start=1):
        started_at = time.monotonic()
        print(f"[{index}/{len(titles)}] Searching: {title}", flush=True)
        progress.write_event("title_started", index=index, title=title)

        try:
            response = client.search_title(title)
        except (requests.RequestException, RuntimeError) as exc:
            elapsed = time.monotonic() - started_at
            results.append(
                SearchResult(
                    index=index,
                    title=title,
                    ok=False,
                    status_code=0,
                    html_path=None,
                    snippet="",
                    error=str(exc),
                    elapsed_seconds=elapsed,
                )
            )
            progress.write_event(
                "title_finished",
                index=index,
                title=title,
                status="ERROR",
                elapsed_seconds=round(elapsed, 3),
                error=str(exc),
            )
            print(f"  ERROR after {elapsed:.1f}s: {exc}", flush=True)
            continue

        ok = NOT_FOUND_TEXT in response.text
        elapsed = time.monotonic() - started_at
        html_path = None
        if save_all or not ok:
            response_dir.mkdir(parents=True, exist_ok=True)
            html_path = response_dir / safe_filename(index, title)
            html_path.write_bytes(response.content)

        result = SearchResult(
            index=index,
            title=title,
            ok=ok,
            status_code=response.status_code,
            html_path=html_path,
            snippet="" if ok else make_snippet(response.text),
            elapsed_seconds=elapsed,
        )
        results.append(result)

        status = "OK" if ok else "BAD"
        progress.write_event(
            "title_finished",
            index=index,
            title=title,
            status=status,
            status_code=response.status_code,
            html_path=str(html_path) if html_path else None,
            elapsed_seconds=round(elapsed, 3),
        )

        completed = len(results)
        ok_count = sum(item.ok for item in results)
        bad_count = completed - ok_count
        print(
            f"  {status} after {elapsed:.1f}s "
            f"(done {completed}/{len(titles)}, ok {ok_count}, flagged/failed {bad_count})",
            flush=True,
        )

    progress.write_event(
        "run_finished",
        index=len(results),
        ok=sum(result.ok for result in results),
        flagged_or_failed=sum(not result.ok for result in results),
    )
    return results


def main() -> int:
    arg_parser = argparse.ArgumentParser(
        description="Check article titles against the Retraction Watch Database."
    )
    arg_parser.add_argument(
        "titles_file",
        nargs="?",
        type=Path,
        default=DEFAULT_TITLES_PATH,
        help=f"Text file containing one title per line. Default: {DEFAULT_TITLES_PATH}",
    )
    arg_parser.add_argument(
        "--title",
        default=None,
        help="Check one title directly instead of reading a text file.",
    )
    arg_parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help=f"Markdown report path. Default: {DEFAULT_REPORT_PATH}",
    )
    arg_parser.add_argument(
        "--response-dir",
        type=Path,
        default=DEFAULT_RESPONSE_DIR,
        help=f"Directory for saved HTML responses. Default: {DEFAULT_RESPONSE_DIR}",
    )
    arg_parser.add_argument(
        "--progress-log",
        type=Path,
        default=DEFAULT_PROGRESS_PATH,
        help=f"JSONL progress log path. Default: {DEFAULT_PROGRESS_PATH}",
    )
    arg_parser.add_argument(
        "--no-progress-log",
        action="store_true",
        help="Disable the JSONL progress log.",
    )
    arg_parser.add_argument(
        "--save-all",
        action="store_true",
        help="Save every HTML response, not only flagged responses.",
    )
    arg_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Request timeout in seconds. Default: {DEFAULT_TIMEOUT:g}",
    )
    arg_parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"Retries per title after a request failure. Default: {DEFAULT_RETRIES}",
    )
    arg_parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"Delay between successful searches in seconds. Default: {DEFAULT_DELAY:g}",
    )
    args = arg_parser.parse_args()

    if args.title:
        titles = [args.title]
    else:
        titles = read_titles(args.titles_file)

    if not titles:
        print("No titles found.")
        return 1

    results = run_batch(
        titles=titles,
        response_dir=args.response_dir,
        save_all=args.save_all,
        timeout=args.timeout,
        retries=args.retries,
        delay=args.delay,
        progress_path=None if args.no_progress_log else args.progress_log,
    )
    write_report(results, args.report)

    bad_count = sum(not result.ok for result in results)
    print(f"\nReport written: {args.report}", flush=True)
    if not args.no_progress_log:
        print(f"Progress log written: {args.progress_log}", flush=True)
    print(f"Checked: {len(results)}", flush=True)
    print(f"OK: {len(results) - bad_count}", flush=True)
    print(f"Flagged or failed: {bad_count}", flush=True)
    return 1 if bad_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
