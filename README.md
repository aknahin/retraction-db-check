# Retraction Database Check

Batch-check article titles against the Retraction Watch Database search page.

The script reads one title per line from a text file, searches each title in the
Retraction Watch Database, and writes a Markdown report. A title is marked `OK`
when the returned page contains:

```text
No Retractions found matching selected criteria
```

Any other response is marked `BAD` and its HTML is saved for manual review.

## Setup

Use Python 3.10 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

Put one title per line in `retraction_search_titles.txt`, then run:

```bash
python3 fetch_retraction_page.py
```

Useful options:

```bash
python3 fetch_retraction_page.py path/to/titles.txt
python3 fetch_retraction_page.py --title "Exact title to check"
python3 fetch_retraction_page.py --report report.md
python3 fetch_retraction_page.py --save-all
python3 fetch_retraction_page.py --timeout 45 --retries 3 --delay 1
python3 fetch_retraction_page.py --progress-log progress.jsonl
```

## Outputs

- `retraction_check_report.md`: Markdown summary for all checked titles.
- `retraction_check_progress.jsonl`: append-only progress events for tracking a run.
- `retraction_responses/`: saved HTML responses for flagged titles.

By default, successful `OK` responses are not saved. Use `--save-all` when you
want an HTML archive for every title.

The terminal also prints immediate progress for each title, including elapsed
time and current OK/flagged counts. Use `--no-progress-log` to disable the JSONL
progress file.

Generated reports, progress logs, and response HTML files are ignored by Git so
the repository stays focused on source code and input data.

## Notes

The site uses ASP.NET form state and cookie detection. The script handles that by
setting the cookie-detection cookie, preserving a session, and posting the live
hidden form fields with `__EVENTTARGET=btnSearch`.
