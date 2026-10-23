# Domain Link Finder

A terminal UI crawler for finding URLs that match host or URL patterns on a site, then verifying each match with a lightweight HTTP request. It is built with [Textual](https://textual.textualize.io/) and [Rich](https://rich.readthedocs.io/), and is designed for Windows Terminal.

The default settings target `https://www.meereen.top/` and the pattern `*.abrtech.top`. Both can be changed in the Settings view.

## Features

- Sitemap and `robots.txt` discovery, with a same-site crawl fallback.
- Host globs (`*.example.com`), URL globs (`https://example.com/files/*`), and `re:` regular expressions.
- Concurrent page crawling and link verification with per-host rate limiting.
- Verification uses a `GET` request with `Range: bytes=0-1`; response bodies are not downloaded.
- Handles redirects, final-host changes, timeouts, retries, and expired TLS certificates.
- Live dashboard with progress bars, throughput sparkline, host breakdown, recent matches, and issues.
- Clickable URLs and links tables with search, filtering, details, copy/open, re-scan, and re-verify actions.
- Pause, resume, stop, demo mode, run history, and resuming saved runs.
- Theme, animation, and page-size preferences persist in `config.json`.
- JSONL journals plus CSV/text exports for every run.

## Requirements

- Python 3.11 or newer.
- Windows Terminal is recommended for the best rendering. The classic Command Prompt also works.

## Quick start

Double-click **`Run Finder.bat`**. It checks for Python and the required packages, installs anything missing, and relaunches the UI in Windows Terminal when available.

Or run it manually:

```powershell
python -m pip install -r requirements.txt
python run.py
```

Useful launch options:

```powershell
# Demo data, no network access needed
python run.py --demo

# Crawl the default site and verify at most 50 matched links
python run.py -u https://www.meereen.top/ -m "*.abrtech.top" --max-verify 50

# Headless run with a bounded crawl
python run.py --cli -u https://www.meereen.top/ -m "*.abrtech.top" --max-pages 20 --max-verify 50

# Resume the newest saved run
python run.py --resume latest
python run.py --list-runs
```

## TUI controls

| Key / control | Action |
| --- | --- |
| `F1` | Home / run setup |
| `F2` | Dashboard |
| `F3` | URLs (pages) |
| `F4` | Links |
| `F5` | Logs |
| `F6` | Settings |
| `F9` | Export results |
| `Ctrl+Q` | Quit (stops an active run) |
| `Enter` | Open details for the selected table row |
| Dashboard buttons | Pause, Resume, or Stop the run |
| Help / Shortcuts | Open the help dialog from Home or Settings |

The Settings view saves crawl options and appearance settings to `config.json`. Advanced values are applied to new runs.

## Patterns

A pattern without a slash is matched against the link hostname:

```text
*.abrtech.top
```

A pattern containing `://` or `/` is matched against the complete URL:

```text
https://example.com/downloads/*
```

Use a regular expression with the `re:` prefix. It is searched against both the URL and hostname:

```text
re:^cdn\d+\.example\.com$
```

Separate multiple patterns with commas in the Settings view, or repeat `-m/--match` on the command line.

## Output

Each run writes to `output/run_<timestamp>/` unless `--output` or a resumed directory is used.

| File | Contents |
| --- | --- |
| `links.jsonl` | Append-only link journal, including status, HTTP code, redirect target, content type, size, timing, and TLS result. |
| `pages.jsonl` | Append-only page journal with scan status and error details. |
| `state.json` | Run configuration and progress snapshot, used by `--resume` and `--list-runs`. |
| `run.log` | Timestamped event log. |
| `links.csv` | Final link export. |
| `pages.csv` | Final page export. |
| `hosts.txt` | Unique matched hosts. |

## Status labels

- `gathered` - matched a pattern but verification has not started.
- `unverified` - gathered, but not verified because `--max-verify` was reached or verification was disabled.
- `ok` - HTTP 2xx response.
- `blocked` - HTTP 401/403.
- `missing` - HTTP 404/410.
- `failed` - another HTTP error response.
- `error` - transport, TLS, timeout, or connection error.

## Development

```powershell
python -m pytest -q
```

The test suite uses Textual's `run_test` pilot and a deterministic demo engine, so it does not need network access.

Project layout:

```text
run.py             CLI/TUI entry point
dlf/engine.py      crawler, verifier, persistence, and exports
dlf/tui.py         Textual application and views
dlf/app.tcss       Textual stylesheet
tests/             engine and TUI tests
Run Finder.bat     Windows launcher
```
