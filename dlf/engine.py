from __future__ import annotations

import csv
import fnmatch
import gzip
import html as html_lib
import json
import os
import queue
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import urldefrag, unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
import urllib3
from bs4 import BeautifulSoup
from urllib3.exceptions import InsecureRequestWarning

urllib3.disable_warnings(InsecureRequestWarning)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36 DomainLinkFinder/1.1"
)

FINAL_LINK_STATES = {"ok", "blocked", "missing", "failed", "error"}
PENDING_LINK_STATES = {"gathered", "queued", "verifying", "unverified"}
ASSET_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".avif",
    ".css", ".js", ".mjs", ".json", ".xml", ".txt", ".map", ".woff", ".woff2",
    ".ttf", ".otf", ".eot", ".zip", ".rar", ".7z", ".gz", ".tar", ".mkv",
    ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".mp3", ".wav", ".flac",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".exe", ".apk", ".dmg", ".iso",
}
SKIP_PATH_PARTS = (
    "/wp-admin", "/wp-json", "/wp-login", "/sub-api", "/xmlrpc.php",
    "/feed", "/comments/feed", "/trackback", "/wp-cron.php",
    "/page/", "/cdn-cgi/", "/.well-known/",
)
PAGE_URL_RE = re.compile(r"(?i)(?:https?:)?//[^\s\"'<>\\]+")
SIZE_RE = re.compile(r"bytes\s+\d+-\d+/(\d+)", re.I)


def now() -> float:
    return time.time()


def clock(ts: float | None = None) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts if ts is not None else now()))


def human_size(size) -> str:
    try:
        value = float(size)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024.0
        index += 1
    if index == 0:
        return f"{int(value)} {units[index]}"
    return f"{value:.1f} {units[index]}"


def trim_text(text: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def clean_url(raw: str) -> str:
    value = html_lib.unescape(str(raw or "")).strip()
    value = value.replace("\\/", "/")
    value = value.strip("\"'")
    value = value.rstrip(".,;:")
    while value.endswith(")") and value.count("(") < value.count(")"):
        value = value[:-1]
    return value


def normalize_url(raw: str, base: str) -> str:
    value = clean_url(raw)
    if not value or value.startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
        return ""
    if value.startswith("//"):
        value = (urlparse(base).scheme or "https") + ":" + value
    try:
        absolute = urljoin(base, value)
    except ValueError:
        return ""
    absolute, _ = urldefrag(absolute)
    parsed = urlparse(absolute)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    return absolute


def filename_from_url(url: str) -> str:
    try:
        path = urlparse(url).path
    except ValueError:
        return ""
    return unquote(path.rsplit("/", 1)[-1]) or ""


def normalize_extensions(values: Iterable[str]) -> list[str]:
    normalized: list[str] = []
    for raw in values:
        for part in str(raw).replace("\n", ",").split(","):
            ext = part.strip().lower()
            while ext.startswith(("*", ".")):
                ext = ext[1:]
            ext = ext.strip().strip(".")
            if not ext or any(char in ext for char in "/\\?&#"):
                continue
            if ext not in normalized:
                normalized.append(ext)
    return normalized


def extension_from_url(url: str, known: Iterable[str] | None = None) -> str:
    try:
        path = unquote(urlparse(url).path or "").rstrip("/")
    except ValueError:
        return ""
    name = path.rsplit("/", 1)[-1].lower()
    if not name:
        return ""
    for ext in sorted(normalize_extensions(known or []), key=len, reverse=True):
        if name.endswith("." + ext):
            return ext
    if "." not in name.strip("."):
        return ""
    return name.rsplit(".", 1)[-1]


def extension_matches(url: str, extensions: Iterable[str]) -> bool:
    normalized = normalize_extensions(extensions)
    if not normalized:
        return True
    try:
        path = unquote(urlparse(url).path or "").rstrip("/").lower()
    except ValueError:
        return False
    return any(path.endswith("." + ext) for ext in normalized)


DEMO_EXTENSIONS = ("mkv", "mp4", "zip", "pdf", "apk", "srt")
DEMO_CONTENT_TYPES = {
    "mkv": "video/x-matroska",
    "mp4": "video/mp4",
    "zip": "application/zip",
    "pdf": "application/pdf",
    "apk": "application/vnd.android.package-archive",
    "srt": "application/x-subrip",
}


@dataclass
class Config:
    start_url: str = "https://www.meereen.top/"
    patterns: list[str] = field(default_factory=lambda: ["*.abrtech.top"])
    extensions: list[str] = field(default_factory=list)
    match_mode: str = "both"
    max_pages: int = 0
    max_verify: int = 0
    verify_mode: str = "all"
    crawl_workers: int = 8
    verify_workers: int = 12
    crawl_delay: float = 0.2
    per_host_limit: int = 1
    timeout: float = 20.0
    retries: int = 2
    user_agent: str = DEFAULT_UA
    cookie: str = ""
    proxy: str = ""
    verify_ssl: bool = True
    respect_robots: bool = True
    use_sitemap: bool = True
    extra_seeds: list[str] = field(default_factory=list)
    output_dir: str = ""
    demo: bool = False
    demo_pages: int = 600
    demo_links_per_page: int = 7
    demo_delay: float = 0.02

    def __post_init__(self) -> None:
        self.extensions = normalize_extensions(self.extensions)
        if self.match_mode not in {"patterns", "both", "extensions"}:
            self.match_mode = "both"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{key: value for key, value in data.items() if key in valid})


@dataclass
class Pattern:
    raw: str
    host_glob: str = ""
    url_glob: str = ""
    regex: re.Pattern | None = None

    def matches(self, url: str, host: str) -> bool:
        if self.regex is not None:
            return bool(self.regex.search(url) or self.regex.search(host))
        if self.url_glob:
            return fnmatch.fnmatch(url.lower(), self.url_glob)
        if self.host_glob:
            return fnmatch.fnmatch(host.lower(), self.host_glob)
        return False


def compile_patterns(patterns: Iterable[str]) -> list[Pattern]:
    result: list[Pattern] = []
    for raw in patterns:
        for part in str(raw).replace("\n", ",").split(","):
            part = part.strip()
            if not part:
                continue
            if part.lower().startswith("re:"):
                try:
                    result.append(Pattern(part, regex=re.compile(part[3:], re.I)))
                except re.error:
                    continue
            elif "://" in part or "/" in part:
                result.append(Pattern(part, url_glob=part.lower()))
            else:
                result.append(Pattern(part, host_glob=part.lower()))
    return result


def match_pattern(url: str, patterns: list[Pattern]) -> str:
    host = (urlparse(url).hostname or "").lower()
    for pattern in patterns:
        if pattern.matches(url, host):
            return pattern.raw
    return ""


def parse_sitemap_xml(data: bytes) -> tuple[str, list[str]]:
    if not data:
        return "", []
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError:
            return "", []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return "", []
    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag not in {"sitemapindex", "urlset"}:
        return "", []
    locations: list[str] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() == "loc" and element.text:
            value = element.text.strip()
            if value:
                locations.append(value)
    return ("index" if tag == "sitemapindex" else "urlset"), locations


@dataclass
class ExtractedLink:
    url: str
    anchor: str = ""
    tag: str = ""
    attr: str = ""


def extract_urls(page_html: str, base_url: str) -> list[ExtractedLink]:
    found: dict[str, ExtractedLink] = {}
    try:
        soup = BeautifulSoup(page_html, "lxml")
    except Exception:
        soup = BeautifulSoup(page_html, "html.parser")

    anchors: dict[str, str] = {}
    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if not href:
            continue
        normalized = normalize_url(str(href), base_url)
        if normalized and normalized not in anchors:
            anchors[normalized] = trim_text(anchor.get_text(" ", strip=True), 180)
            found.setdefault(normalized, ExtractedLink(normalized, anchors[normalized], "a", "href"))

    attributes = ("href", "src", "action", "poster", "data-href", "data-src",
                  "data-url", "data-link", "data-file", "data-download", "data-video")
    for tag in soup.find_all(True):
        for attr in attributes:
            value = tag.get(attr)
            if not value or not isinstance(value, str):
                continue
            normalized = normalize_url(value, base_url)
            if normalized:
                anchor_text = anchors.get(normalized) or trim_text(tag.get_text(" ", strip=True), 180)
                found.setdefault(normalized, ExtractedLink(normalized, anchor_text, tag.name or "", attr))

    raw = (page_html or "").replace("\\/", "/")
    for match in PAGE_URL_RE.finditer(raw):
        normalized = normalize_url(match.group(0), base_url)
        if normalized:
            found.setdefault(normalized, ExtractedLink(normalized, anchors.get(normalized, ""), "", "raw"))
    return list(found.values())


def is_page_url(url: str, start_host: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    base = start_host.lower()
    if base.startswith("www."):
        base = base[4:]
    if not (host == base or host.endswith("." + base)):
        return False
    path = (parsed.path or "/").lower()
    if any(part in path for part in SKIP_PATH_PARTS):
        return False
    suffix = Path(path).suffix.lower()
    if suffix in ASSET_EXTENSIONS:
        return False
    if parsed.query:
        query = parsed.query.lower()
        if any(token in query for token in ("s=", "replytocom", "share=", "preview=")):
            return False
    return True


class RateLimiter:
    def __init__(self, min_interval: float):
        self.min_interval = max(0.0, float(min_interval))
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self, stop: threading.Event) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            current = time.monotonic()
            delay = max(0.0, self._next - current)
            self._next = max(current, self._next) + self.min_interval
        end = time.monotonic() + delay
        while time.monotonic() < end:
            if stop.is_set():
                return
            time.sleep(min(0.05, end - time.monotonic()))


class Engine:
    def __init__(self, config: Config, resume_dir: str | Path | None = None):
        self.config = config
        self.patterns = compile_patterns(config.patterns)
        self.start_url = config.start_url.strip()
        parsed = urlparse(self.start_url)
        self.start_host = (parsed.hostname or "").lower()
        self.start_scheme = parsed.scheme or "https"
        self.site_root = f"{self.start_scheme}://{parsed.netloc}"

        root = Path(__file__).resolve().parents[1]
        if resume_dir:
            self.output_dir = Path(resume_dir)
        elif config.output_dir:
            self.output_dir = Path(config.output_dir)
        else:
            self.output_dir = root / "output" / time.strftime("run_%Y%m%d_%H%M%S")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.pages: dict[str, dict] = {}
        self.links: dict[str, dict] = {}
        self.hosts: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._log_entries: deque[dict] = deque(maxlen=5000)
        self._log_seq = 0
        self._link_events: deque[dict] = deque(maxlen=12000)
        self._link_event_seq = 0
        self._recent_links: deque[dict] = deque(maxlen=30)
        self._recent_errors: deque[dict] = deque(maxlen=30)
        self._revision = 0
        self._phase = "idle"
        self._pause = threading.Event()
        self._stop = threading.Event()
        self._finished = threading.Event()
        self._crawl_done = threading.Event()
        self._verify_done = threading.Event()
        self._pending_pages = 0
        self._pages_started = 0
        self._page_queue: queue.Queue[str] = queue.Queue()
        self._verify_queue: queue.Queue[str | None] = queue.Queue()
        self._verify_scheduled = 0
        self._verify_completed = 0
        self._verify_inflight = 0
        self._host_verify_scheduled: set[str] = set()
        self._host_semaphores: dict[str, threading.BoundedSemaphore] = {}
        self._page_scanned = 0
        self._page_failed = 0
        self._page_skipped = 0
        self._links_verified = 0
        self._links_ok = 0
        self._links_bad = 0
        self._links_filtered = 0
        self._scan_times: deque[float] = deque(maxlen=4000)
        self._verify_times: deque[float] = deque(maxlen=8000)
        self._start_time = now()
        self._end_time: float | None = None
        self._crawl_limiter = RateLimiter(config.crawl_delay)
        self._local = threading.local()
        self._robot_parser: RobotFileParser | None = None
        self._robot_sitemaps: list[str] = []
        self._robots_loaded = False
        self._robots_lock = threading.Lock()
        self._insecure_logged = False
        self._run_thread: threading.Thread | None = None
        self._state_thread: threading.Thread | None = None
        self._closed = False
        self._verifier_threads: list[threading.Thread] = []
        self._crawler_threads: list[threading.Thread] = []
        self._resume_dir = Path(resume_dir) if resume_dir else None
        self._log_path = self.output_dir / "run.log"
        self._pages_path = self.output_dir / "pages.jsonl"
        self._links_path = self.output_dir / "links.jsonl"
        self._state_path = self.output_dir / "state.json"
        self._log_file = open(self._log_path, "a", encoding="utf-8", buffering=1)
        if self._resume_dir:
            self._load_state()
        self.log("info", f"Output directory: {self.output_dir}")

    @classmethod
    def from_resume(cls, run_dir: str | Path) -> "Engine":
        run_path = Path(run_dir)
        state_path = run_path / "state.json"
        config = Config()
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                config = Config.from_dict(state.get("config", {}))
            except (OSError, json.JSONDecodeError):
                pass
        config.output_dir = str(run_path)
        return cls(config, resume_dir=run_path)

    @staticmethod
    def find_latest_run(output_root: Path) -> Path | None:
        if not output_root.exists():
            return None
        candidates = [path for path in output_root.glob("run_*") if path.is_dir()]
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None

    def log(self, level: str, message: str) -> None:
        timestamp = clock()
        with self._lock:
            self._log_seq += 1
            entry = {
                "seq": self._log_seq,
                "time": timestamp,
                "level": level.lower(),
                "message": message,
            }
            self._log_entries.append(entry)
            self._revision += 1
            if entry["level"] in {"error", "warn"}:
                self._recent_errors.append(dict(entry))
        with self._write_lock:
            try:
                self._log_file.write(f"{timestamp} {entry['level'].upper():<5} {message}\n")
            except OSError:
                pass

    def logs_since(self, seq: int) -> list[dict]:
        with self._lock:
            return [dict(entry) for entry in self._log_entries if entry["seq"] > seq]

    def logs_all(self) -> list[dict]:
        with self._lock:
            return [dict(entry) for entry in self._log_entries]

    def _emit_link_event(self, kind: str, link: dict) -> None:
        with self._lock:
            self._link_event_seq += 1
            self._link_events.append({
                "seq": self._link_event_seq,
                "kind": kind,
                "url": link.get("url", ""),
                "host": link.get("host", ""),
                "filename": link.get("filename", ""),
                "extension": link.get("extension", ""),
                "status": link.get("status", ""),
                "http": link.get("http"),
                "error": link.get("error", ""),
                "time": clock(),
                "updated_at": link.get("updated_at"),
            })

    def link_events_since(self, seq: int) -> list[dict]:
        with self._lock:
            return [dict(event) for event in self._link_events if event["seq"] > seq]

    @property
    def link_event_seq(self) -> int:
        with self._lock:
            return self._link_event_seq

    def _link_accepted(self, url: str, matched: str | None = None) -> bool:
        mode = self.config.match_mode
        if mode not in {"patterns", "both", "extensions"}:
            mode = "both"
        pattern_match = match_pattern(url, self.patterns) if matched is None else matched
        type_match = extension_matches(url, self.config.extensions)
        has_types = bool(self.config.extensions)
        if mode == "patterns":
            return bool(pattern_match)
        if mode == "extensions":
            return type_match
        return bool(pattern_match) and (type_match if has_types else True)

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    def _set_phase(self, phase: str) -> None:
        with self._lock:
            if self._phase != phase:
                self._phase = phase
                self._revision += 1

    def pause(self) -> None:
        self._pause.set()
        self.log("warn", "Paused")
        with self._lock:
            self._revision += 1

    def resume(self) -> None:
        self._pause.clear()
        self.log("info", "Resumed")
        with self._lock:
            self._revision += 1

    def stop(self) -> None:
        already_finished = self._phase in {"done", "stopped", "error"}
        self._stop.set()
        self._pause.clear()
        if not already_finished:
            self.log("warn", "Stop requested")
        with self._lock:
            self._revision += 1

    def _wait_if_paused(self) -> bool:
        while self._pause.is_set() and not self._stop.is_set():
            time.sleep(0.1)
        return not self._stop.is_set()

    @property
    def session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers.update({
                "User-Agent": self.config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.7,en;q=0.6",
            })
            if self.config.cookie:
                session.headers["Cookie"] = self.config.cookie
            if self.config.proxy:
                session.proxies = {"http": self.config.proxy, "https": self.config.proxy}
            session.verify = self.config.verify_ssl
            self._local.session = session
        return session

    def _request(self, url: str, referer: str = "", extra_headers: dict | None = None,
                 stream: bool = True, timeout: float | None = None,
                 allow_insecure_fallback: bool = True) -> requests.Response:
        headers = {}
        if referer:
            headers["Referer"] = referer
        if extra_headers:
            headers.update(extra_headers)
        last_error: Exception | None = None
        for attempt in range(self.config.retries + 1):
            if self._stop.is_set():
                raise requests.RequestException("stopped")
            try:
                response = self.session.get(
                    url,
                    headers=headers,
                    stream=stream,
                    allow_redirects=True,
                    timeout=(10, timeout or self.config.timeout),
                )
                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.config.retries:
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        wait = min(8.0, max(0.5, float(retry_after)))
                    except ValueError:
                        wait = 0.6 * (attempt + 1)
                    response.close()
                    time.sleep(wait)
                    continue
                return response
            except requests.exceptions.SSLError as exc:
                if allow_insecure_fallback:
                    try:
                        response = self.session.get(
                            url,
                            headers=headers,
                            stream=stream,
                            allow_redirects=True,
                            timeout=(10, timeout or self.config.timeout),
                            verify=False,
                        )
                        response.dlf_insecure = True
                        response.dlf_insecure_reason = str(exc)
                        if not self._insecure_logged:
                            self._insecure_logged = True
                            self.log("warn", f"TLS verification failed for {url}; continuing without verification")
                        return response
                    except requests.RequestException as fallback_exc:
                        last_error = fallback_exc
                else:
                    last_error = exc
                if attempt >= self.config.retries:
                    raise last_error
                time.sleep(0.5 * (attempt + 1))
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.config.retries:
                    raise
                time.sleep(0.5 * (attempt + 1))
        raise requests.RequestException(str(last_error or "request failed"))

    @staticmethod
    def _read_limited(response: requests.Response, limit: int = 4 * 1024 * 1024) -> bytes:
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(65536):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= limit:
                break
        return b"".join(chunks)

    def _load_robots(self) -> None:
        if self._robots_loaded or not self.config.respect_robots:
            return
        with self._robots_lock:
            if self._robots_loaded:
                return
            self._robots_loaded = True
            url = self.site_root + "/robots.txt"
            try:
                response = self._request(url, stream=True, timeout=10)
                text = self._read_limited(response, 512 * 1024).decode("utf-8", errors="replace")
                response.close()
                parser = RobotFileParser()
                parser.set_url(url)
                parser.parse(text.splitlines())
                self._robot_parser = parser
                for line in text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        sitemap = line.split(":", 1)[1].strip()
                        if sitemap:
                            self._robot_sitemaps.append(sitemap)
            except Exception as exc:
                self.log("warn", f"robots.txt unavailable: {exc}")
                self._robot_parser = None

    def _can_fetch(self, url: str) -> bool:
        if not self.config.respect_robots:
            return True
        self._load_robots()
        if self._robot_parser is None:
            return True
        try:
            return self._robot_parser.can_fetch("*", url)
        except Exception:
            return True

    def _collect_sitemap(self, url: str, depth: int, seen: set[str], pages: list[str]) -> None:
        if depth > 4 or url in seen or len(seen) > 200:
            return
        seen.add(url)
        try:
            response = self._request(url, stream=True, timeout=self.config.timeout)
            data = self._read_limited(response, 12 * 1024 * 1024)
            response.close()
        except Exception as exc:
            self.log("warn", f"Sitemap failed: {url} ({exc})")
            return
        kind, locations = parse_sitemap_xml(data)
        if not kind:
            return
        self.log("info", f"Sitemap {kind}: {url} -> {len(locations)} entries")
        if kind == "index":
            for location in locations:
                if self._same_site(location):
                    self._collect_sitemap(location, depth + 1, seen, pages)
        else:
            pages.extend(locations)

    def discover_seeds(self) -> list[str]:
        seeds: list[str] = []
        extra: list[str] = []
        for seed in self.config.extra_seeds:
            normalized = normalize_url(seed, self.start_url)
            if normalized:
                extra.append(normalized)
        collected: list[str] = []
        if self.config.use_sitemap:
            self.log("info", f"Discovering sitemaps for {self.site_root}")
            self._load_robots()
            candidates = [
                self.site_root + "/sitemap.xml",
                self.site_root + "/wp-sitemap.xml",
                self.site_root + "/sitemap_index.xml",
            ]
            seen: set[str] = set()
            for candidate in candidates:
                before = len(collected)
                self._collect_sitemap(candidate, 0, seen, collected)
                if len(collected) > before:
                    break
            if not collected:
                for sitemap in self._robot_sitemaps:
                    if self._same_site(sitemap):
                        self._collect_sitemap(sitemap, 0, seen, collected)
        else:
            self.log("info", "Sitemap discovery disabled; using seed URLs")
        filtered: list[str] = []
        seen_pages: set[str] = set()
        for raw in extra + collected:
            normalized = normalize_url(raw, self.start_url)
            if normalized and normalized not in seen_pages and is_page_url(normalized, self.start_host):
                seen_pages.add(normalized)
                filtered.append(normalized)
        if self.config.max_pages > 0:
            filtered = filtered[: self.config.max_pages]
        if not filtered:
            filtered = [self.start_url]
        self.log("info", f"Discovered {len(filtered)} page URLs")
        return filtered

    def _host_scope(self) -> tuple[str, str]:
        base = self.start_host
        if base.startswith("www."):
            base = base[4:]
        return self.start_host, base

    def _same_site(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        if not host:
            return False
        base = self.start_host[4:] if self.start_host.startswith("www.") else self.start_host
        return host == base or host.endswith("." + base)

    def _page_set_status(self, page: dict, status: str) -> None:
        old = page.get("status")
        if old == status:
            return
        if old in {"scanned", "failed", "skipped"}:
            self._page_scanned -= int(old == "scanned")
            self._page_failed -= int(old == "failed")
            self._page_skipped -= int(old == "skipped")
        page["status"] = status
        page["updated_at"] = now()
        if status in {"scanned", "failed", "skipped"}:
            self._page_scanned += int(status == "scanned")
            self._page_failed += int(status == "failed")
            self._page_skipped += int(status == "skipped")
            page["finished_at"] = now()
            if status == "failed":
                self._recent_errors.append({
                    "time": clock(), "level": "error",
                    "message": f"{page['url']} - {page.get('error', 'failed')}",
                })
            if status == "scanned":
                self._scan_times.append(now())
            if "url" in page:
                self._persist_page(page)
        self._revision += 1

    def _link_set_status(self, link: dict, status: str) -> None:
        old = link.get("status")
        if old == status:
            return
        if old in FINAL_LINK_STATES:
            self._links_verified -= 1
            if old == "ok":
                self._links_ok -= 1
            else:
                self._links_bad -= 1
            host_stats = self.hosts.get(link["host"])
            if host_stats:
                if old == "ok":
                    host_stats["ok"] = max(0, host_stats.get("ok", 0) - 1)
                else:
                    host_stats["bad"] = max(0, host_stats.get("bad", 0) - 1)
        link["status"] = status
        link["updated_at"] = now()
        if status in FINAL_LINK_STATES:
            self._links_verified += 1
            if status == "ok":
                self._links_ok += 1
            else:
                self._links_bad += 1
            self._verify_times.append(now())
            link["verified_at"] = now()
            host_stats = self.hosts.get(link["host"])
            if host_stats:
                if status == "ok":
                    host_stats["ok"] = host_stats.get("ok", 0) + 1
                else:
                    host_stats["bad"] = host_stats.get("bad", 0) + 1
            if status != "ok":
                self._recent_errors.append({
                    "time": clock(),
                    "level": "error" if status in {"failed", "error"} else "warn",
                    "message": f"{link['host']} - {status} {link.get('http', '')} {link.get('error', '')}".strip(),
                })
            self._persist_link(link)
        elif status in {"skipped", "unverified"}:
            self._persist_link(link)
        for item in self._recent_links:
            if item.get("url") == link["url"]:
                item.update({
                    "status": status,
                    "http": link.get("http"),
                    "error": link.get("error", ""),
                    "time": clock(),
                })
                break
        self._emit_link_event("status", link)
        self._revision += 1

    def _persist_link(self, link: dict) -> None:
        with self._write_lock:
            try:
                with self._links_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(link, ensure_ascii=False) + "\n")
            except OSError:
                pass

    def _upsert_page(self, url: str, status: str = "queued", source: str = "sitemap",
                     **fields) -> dict:
        page = self.pages.get(url)
        created = page is None
        if created:
            page = {
                "url": url,
                "status": status,
                "source": source,
                "http": None,
                "final_url": "",
                "links_found": 0,
                "elapsed_ms": None,
                "error": "",
                "gathered_at": now(),
                "scanned_at": None,
                "updated_at": now(),
            }
            self.pages[url] = page
            self._revision += 1
        for key, value in fields.items():
            page[key] = value
        if created:
            self._page_set_status(page, status)
            self._persist_page(page)
        return page

    def _persist_page(self, page: dict) -> None:
        with self._write_lock:
            try:
                with self._pages_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(page, ensure_ascii=False) + "\n")
            except OSError:
                pass

    def _upsert_link(self, url: str, source_page: str, anchor: str, matched: str,
                     tag: str = "", attr: str = "") -> dict:
        link = self.links.get(url)
        if link is not None:
            return link
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        link = {
            "url": url,
            "host": host,
            "filename": filename_from_url(url),
            "extension": extension_from_url(url, self.config.extensions),
            "source_page": source_page,
            "anchor": anchor,
            "pattern": matched,
            "tag": tag,
            "attr": attr,
            "status": "gathered",
            "http": None,
            "final_url": "",
            "final_host": "",
            "content_type": "",
            "content_disposition": "",
            "size": None,
            "elapsed_ms": None,
            "ssl_verified": None,
            "error": "",
            "gathered_at": now(),
            "verified_at": None,
            "updated_at": now(),
        }
        self.links[url] = link
        host_stats = self.hosts.setdefault(host, {
            "host": host, "count": 0, "ok": 0, "bad": 0, "example": url,
        })
        host_stats["count"] += 1
        if not host_stats.get("example"):
            host_stats["example"] = url
        self._recent_links.appendleft({
            "url": url, "host": host, "filename": link["filename"],
            "extension": link["extension"], "time": clock(), "status": "gathered",
        })
        self._emit_link_event("found", link)
        self._revision += 1
        with self._write_lock:
            try:
                with self._links_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(link, ensure_ascii=False) + "\n")
            except OSError:
                pass
        return link

    def _schedule_verify(self, link: dict) -> None:
        if self.config.verify_mode == "none":
            self._link_set_status(link, "unverified")
            return
        if self.config.verify_mode == "hosts" and link["host"] in self._host_verify_scheduled:
            self._link_set_status(link, "skipped")
            return
        if self.config.max_verify and self._verify_scheduled >= self.config.max_verify:
            self._link_set_status(link, "unverified")
            return
        self._host_verify_scheduled.add(link["host"])
        self._verify_scheduled += 1
        self._link_set_status(link, "queued")
        self._verify_queue.put(link["url"])

    def _enqueue_verify_existing(self) -> None:
        if self.config.verify_mode == "none":
            return
        for link in list(self.links.values()):
            status = link["status"]
            if status in {"queued", "verifying"}:
                self._link_set_status(link, "queued")
                self._verify_queue.put(link["url"])
            elif status == "gathered":
                self._schedule_verify(link)

    def start(self) -> None:
        if self._run_thread and self._run_thread.is_alive():
            return
        self._finished.clear()
        self._run_thread = threading.Thread(target=self.run, name="dlf-engine", daemon=True)
        self._run_thread.start()

    def wait(self, timeout: float | None = None) -> None:
        if self._run_thread:
            self._run_thread.join(timeout)

    def run(self) -> None:
        self._start_time = now()
        self._end_time = None
        self._state_thread = threading.Thread(target=self._state_writer, name="dlf-state", daemon=True)
        self._state_thread.start()
        try:
            if self.config.demo:
                self._run_demo()
                return
            self._set_phase("discovering")
            seeds = self.discover_seeds()
            self._set_phase("crawling")
            with self._lock:
                for url in seeds:
                    if url not in self.pages:
                        self._upsert_page(url, "queued", "sitemap")
                pending_existing = [url for url, page in self.pages.items()
                                    if page["status"] in {"queued", "fetching"}]
                self._pending_pages = len(pending_existing)
                for url in pending_existing:
                    page = self.pages[url]
                    page["status"] = "queued"
                    self._page_queue.put(url)
            self.log("info", f"Queued {self._pending_pages} pages for scanning")
            self._start_verifiers()
            self._enqueue_verify_existing()
            self._start_crawlers()
            self._monitor_crawl()
            self._crawl_done.set()
            self._join_crawlers()
            if self._stop.is_set():
                self.log("warn", "Crawl stopped by user")
            self._finish_verifiers()
            self._set_phase("stopped" if self._stop.is_set() else "done")
        except Exception as exc:
            self.log("error", f"Fatal error: {exc}")
            self._set_phase("error")
        finally:
            self._end_time = now()
            self._crawl_done.set()
            self._finished.set()
            try:
                self._write_state()
                self.export_csv()
            except Exception:
                pass
            self.log("info", "Run finished")
            try:
                self._log_file.flush()
            except OSError:
                pass

    def _start_crawlers(self) -> None:
        self._crawler_threads = []
        for index in range(max(1, self.config.crawl_workers)):
            thread = threading.Thread(target=self._crawl_worker, name=f"dlf-crawl-{index}", daemon=True)
            thread.start()
            self._crawler_threads.append(thread)

    def _start_verifiers(self) -> None:
        if self.config.verify_mode == "none":
            self._verify_done.set()
            return
        self._verifier_threads = []
        for index in range(max(1, self.config.verify_workers)):
            thread = threading.Thread(target=self._verify_worker, name=f"dlf-verify-{index}", daemon=True)
            thread.start()
            self._verifier_threads.append(thread)

    def _monitor_crawl(self) -> None:
        last_state = 0.0
        while not self._stop.is_set():
            with self._lock:
                pending = self._pending_pages
            if pending <= 0:
                return
            if now() - last_state > 2.0:
                last_state = now()
                self._write_state()
            time.sleep(0.15)

    def _join_crawlers(self) -> None:
        for thread in self._crawler_threads:
            thread.join(timeout=10)

    def _finish_verifiers(self) -> None:
        if self.config.verify_mode == "none":
            return
        for _ in self._verifier_threads:
            self._verify_queue.put(None)
        for thread in self._verifier_threads:
            thread.join(timeout=30)
        self._verify_done.set()

    def _crawl_worker(self) -> None:
        while True:
            if not self._wait_if_paused():
                return
            try:
                url = self._page_queue.get(timeout=0.3)
            except queue.Empty:
                if self._crawl_done.is_set() or self._stop.is_set():
                    return
                continue
            try:
                with self._lock:
                    page = self.pages.get(url)
                    if page is None or page["status"] not in {"queued", "fetching"}:
                        continue
                    if self.config.max_pages and self._pages_started >= self.config.max_pages:
                        self._page_set_status(page, "skipped")
                        page["error"] = "max-pages limit reached"
                        continue
                    self._pages_started += 1
                    page["status"] = "fetching"
                    self._revision += 1
                self._crawl_limiter.wait(self._stop)
                if self._stop.is_set():
                    return
                self._fetch_and_parse(url)
            except Exception as exc:
                with self._lock:
                    page = self.pages.get(url)
                    if page is not None:
                        page["error"] = trim_text(str(exc), 300)
                        page["http"] = page.get("http")
                        self._page_set_status(page, "failed")
                self.log("error", f"Page failed: {url} - {exc}")
            finally:
                with self._lock:
                    self._pending_pages = max(0, self._pending_pages - 1)
                self._page_queue.task_done()

    def _fetch_and_parse(self, url: str) -> None:
        started = time.monotonic()
        with self._lock:
            page = self.pages[url]
        if not self._can_fetch(url):
            with self._lock:
                page["error"] = "blocked by robots.txt"
                self._page_set_status(page, "skipped")
            self.log("warn", f"robots.txt skip: {url}")
            return
        response = self._request(url)
        try:
            http_status = response.status_code
            final_url = response.url
            content_type = (response.headers.get("Content-Type") or "").lower()
            with self._lock:
                page["http"] = http_status
                page["final_url"] = final_url
                page["content_type"] = content_type
            if http_status >= 400:
                with self._lock:
                    page["error"] = f"HTTP {http_status}"
                    self._page_set_status(page, "failed")
                self.log("warn", f"HTTP {http_status}: {url}")
                return
            if content_type and not any(kind in content_type for kind in ("html", "xhtml", "xml")):
                with self._lock:
                    page["error"] = f"non-HTML content: {content_type}"
                    self._page_set_status(page, "skipped")
                return
            body = self._read_limited(response)
            encoding = response.encoding or "utf-8"
            try:
                text = body.decode(encoding, errors="replace")
            except LookupError:
                text = body.decode("utf-8", errors="replace")
        finally:
            response.close()

        extracted = extract_urls(text, final_url or url)
        matched_count = 0
        filtered_count = 0
        new_pages: list[str] = []
        mode = self.config.match_mode
        for item in extracted:
            matched = match_pattern(item.url, self.patterns)
            if self._link_accepted(item.url, matched):
                label = matched
                if mode == "extensions":
                    label = f"filetype:{extension_from_url(item.url, self.config.extensions)}"
                with self._lock:
                    link = self._upsert_link(item.url, url, item.anchor, label, item.tag, item.attr)
                    if link["status"] == "gathered":
                        self._schedule_verify(link)
                matched_count += 1
            elif matched and mode == "both" and self.config.extensions:
                with self._lock:
                    self._links_filtered += 1
                filtered_count += 1
            if self._same_site(item.url) and is_page_url(item.url, self.start_host):
                with self._lock:
                    exists = item.url in self.pages
                if not exists:
                    new_pages.append(item.url)

        added = 0
        for page_url in new_pages:
            with self._lock:
                if page_url in self.pages:
                    continue
                if self.config.max_pages and len(self.pages) >= self.config.max_pages:
                    continue
                self._upsert_page(page_url, "queued", "link")
                self._pending_pages += 1
                self._page_queue.put(page_url)
                added += 1

        elapsed_ms = int((time.monotonic() - started) * 1000)
        with self._lock:
            page["links_found"] = matched_count
            page["elapsed_ms"] = elapsed_ms
            page["scanned_at"] = now()
            page["error"] = ""
            self._page_set_status(page, "scanned")
        self.log(
            "info",
            f"Scanned {url} [{http_status}] links={matched_count} new_pages={added} "
            f"filtered={filtered_count} {elapsed_ms}ms",
        )

    def _host_semaphore(self, host: str) -> threading.BoundedSemaphore:
        with self._lock:
            semaphore = self._host_semaphores.get(host)
            if semaphore is None:
                semaphore = threading.BoundedSemaphore(max(1, self.config.per_host_limit))
                self._host_semaphores[host] = semaphore
            return semaphore

    def _verify_worker(self) -> None:
        while True:
            item = self._verify_queue.get()
            if item is None:
                self._verify_queue.task_done()
                return
            if not self._wait_if_paused():
                self._verify_queue.task_done()
                return
            if self._stop.is_set():
                self._verify_queue.task_done()
                continue
            try:
                with self._lock:
                    link = self.links.get(item)
                    if link is None or link["status"] in FINAL_LINK_STATES:
                        continue
                    self._verify_inflight += 1
                    self._link_set_status(link, "verifying")
                self._verify_link(link)
            except Exception as exc:
                with self._lock:
                    link = self.links.get(item)
                    if link is not None:
                        link["error"] = trim_text(str(exc), 300)
                        self._link_set_status(link, "error")
                self.log("error", f"Verify failed: {item} - {exc}")
            finally:
                with self._lock:
                    self._verify_inflight = max(0, self._verify_inflight - 1)
                    self._verify_completed += 1
                self._verify_queue.task_done()

    def _verify_link(self, link: dict) -> None:
        semaphore = self._host_semaphore(link["host"])
        while not semaphore.acquire(timeout=0.3):
            if self._stop.is_set():
                return
        try:
            headers = {"Range": "bytes=0-1", "Accept": "*/*"}
            if link.get("source_page"):
                headers["Referer"] = link["source_page"]
            started = time.monotonic()
            try:
                response = self._request(
                    link["url"], extra_headers=headers,
                    stream=True, timeout=self.config.timeout,
                )
            except requests.RequestException as exc:
                with self._lock:
                    link["error"] = trim_text(str(exc), 300)
                    link["elapsed_ms"] = int((time.monotonic() - started) * 1000)
                    self._link_set_status(link, "error")
                return
            try:
                code = response.status_code
                final_url = response.url or link["url"]
                final_host = (urlparse(final_url).hostname or "").lower()
                content_type = response.headers.get("Content-Type", "")
                content_disposition = response.headers.get("Content-Disposition", "")
                content_range = response.headers.get("Content-Range", "")
                insecure = bool(getattr(response, "dlf_insecure", False))
                size = None
                range_match = SIZE_RE.search(content_range)
                if range_match:
                    size = int(range_match.group(1))
                elif response.headers.get("Content-Length"):
                    try:
                        size = int(response.headers["Content-Length"])
                    except ValueError:
                        size = None
                result = {
                    "http": code,
                    "final_url": final_url,
                    "final_host": final_host,
                    "content_type": content_type,
                    "content_disposition": content_disposition,
                    "size": size,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    "ssl_verified": not insecure,
                    "error": "",
                }
                if 200 <= code < 300:
                    status = "ok"
                elif code in {401, 403}:
                    status = "blocked"
                    result["error"] = f"HTTP {code}"
                elif code in {404, 410}:
                    status = "missing"
                    result["error"] = f"HTTP {code}"
                else:
                    status = "failed"
                    result["error"] = f"HTTP {code}"
                with self._lock:
                    link.update(result)
                    self._link_set_status(link, status)
                    if link["status"] == "ok":
                        self._recent_links.appendleft({
                            "url": link["url"], "host": link["host"],
                            "filename": link["filename"],
                            "time": clock(), "status": "ok" if final_host == link["host"] else "redirect",
                        })
            finally:
                response.close()
        finally:
            semaphore.release()

    def verify_links(self, urls: Iterable[str]) -> int:
        selected: list[str] = []
        with self._lock:
            for url in urls:
                link = self.links.get(url)
                if link is None or link["status"] == "verifying":
                    continue
                self._link_set_status(link, "queued")
                selected.append(url)
        if not selected:
            return 0
        thread = threading.Thread(
            target=self._manual_verify_batch, args=(selected,),
            name="dlf-manual-verify", daemon=True,
        )
        thread.start()
        self.log("info", f"Manual verification started for {len(selected)} link(s)")
        return len(selected)

    def _manual_verify_batch(self, urls: list[str]) -> None:
        workers = max(1, min(self.config.verify_workers, len(urls)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(self._manual_verify_one, urls))

    def _manual_verify_one(self, url: str) -> None:
        with self._lock:
            link = self.links.get(url)
            if link is None:
                return
            self._link_set_status(link, "verifying")
        try:
            self._verify_link(link)
        except Exception as exc:
            with self._lock:
                link["error"] = trim_text(str(exc), 300)
                self._link_set_status(link, "error")

    def scan_page_now(self, url: str) -> None:
        with self._lock:
            page = self.pages.get(url)
            if page is None:
                return
            self._page_set_status(page, "queued")
        thread = threading.Thread(target=self._scan_single_page, args=(url,), daemon=True)
        thread.start()

    def _scan_single_page(self, url: str) -> None:
        try:
            with self._lock:
                page = self.pages.get(url)
                if page is None:
                    return
                page["status"] = "fetching"
                self._revision += 1
            self._fetch_and_parse(url)
        except Exception as exc:
            with self._lock:
                page = self.pages.get(url)
                if page is not None:
                    page["error"] = trim_text(str(exc), 300)
                    self._page_set_status(page, "failed")

    def stats(self) -> dict:
        with self._lock:
            total_pages = len(self.pages)
            remaining_pages = max(0, total_pages - self._page_scanned - self._page_failed - self._page_skipped)
            total_links = len(self.links)
            remaining_links = max(0, total_links - self._links_verified)
            elapsed = max(0.001, (self._end_time or now()) - self._start_time)
            cutoff = now() - 10.0
            page_rate = sum(1 for stamp in self._scan_times if stamp >= cutoff) / 10.0
            verify_rate = sum(1 for stamp in self._verify_times if stamp >= cutoff) / 10.0
            return {
                "phase": self._phase,
                "paused": self._pause.is_set(),
                "stopped": self._stop.is_set(),
                "crawl_done": self._crawl_done.is_set(),
                "verify_done": self._verify_done.is_set(),
                "pages_gathered": total_pages,
                "pages_scanned": self._page_scanned,
                "pages_failed": self._page_failed,
                "pages_skipped": self._page_skipped,
                "pages_pending": remaining_pages,
                "links_gathered": total_links,
                "links_verified": self._links_verified,
                "links_ok": self._links_ok,
                "links_bad": self._links_bad,
                "links_filtered": self._links_filtered,
                "links_pending": remaining_links,
                "hosts": len(self.hosts),
                "page_rate": page_rate,
                "verify_rate": verify_rate,
                "page_eta": (remaining_pages / page_rate) if page_rate > 0 else None,
                "verify_eta": (remaining_links / verify_rate) if verify_rate > 0 else None,
                "elapsed": elapsed,
                "output_dir": str(self.output_dir),
                "verify_scheduled": self._verify_scheduled,
                "verify_completed": self._verify_completed,
                "pending_pages": self._pending_pages,
                "revision": self._revision,
            }

    def recent_links(self, limit: int = 12) -> list[dict]:
        with self._lock:
            return [dict(item) for item in list(self._recent_links)[:limit]]

    def recent_errors(self, limit: int = 12) -> list[dict]:
        with self._lock:
            return [dict(item) for item in list(self._recent_errors)[:limit]]

    def top_hosts(self, limit: int = 12) -> list[dict]:
        with self._lock:
            items = list(self.hosts.values())
        items.sort(key=lambda item: item.get("count", 0), reverse=True)
        return [dict(item) for item in items[:limit]]

    def page_rows(self, status: str = "all", search: str = "",
                  offset: int = 0, limit: int = 100) -> tuple[list[dict], int]:
        search_lower = search.lower().strip()
        rows: list[dict] = []
        total = 0
        with self._lock:
            for page in self.pages.values():
                page_status = page["status"]
                if status == "pending":
                    if page_status not in {"queued", "fetching"}:
                        continue
                elif status != "all" and page_status != status:
                    continue
                if search_lower and search_lower not in page["url"].lower():
                    continue
                total += 1
                if offset <= total - 1 < offset + limit:
                    rows.append(dict(page))
        return rows, total

    def link_rows(self, status: str = "all", search: str = "", host_filter: str = "",
                  offset: int = 0, limit: int = 100) -> tuple[list[dict], int]:
        search_lower = search.lower().strip()
        host_lower = host_filter.lower().strip()
        rows: list[dict] = []
        total = 0
        with self._lock:
            for link in self.links.values():
                link_status = link["status"]
                if status == "pending":
                    if link_status not in PENDING_LINK_STATES:
                        continue
                elif status == "verified":
                    if link_status not in FINAL_LINK_STATES:
                        continue
                elif status == "failed":
                    if link_status not in {"blocked", "missing", "failed", "error"}:
                        continue
                elif status != "all" and link_status != status:
                    continue
                if host_lower and link["host"] != host_lower:
                    continue
                if search_lower:
                    haystack = (
                        f"{link['url']} {link['host']} {link['filename']} "
                        f"{link.get('extension', '')}"
                    ).lower()
                    if search_lower not in haystack:
                        continue
                total += 1
                if offset <= total - 1 < offset + limit:
                    rows.append(dict(link))
        return rows, total

    def links_for_page(self, page_url: str, limit: int = 500) -> list[dict]:
        rows: list[dict] = []
        with self._lock:
            for link in self.links.values():
                if link["source_page"] == page_url:
                    rows.append(dict(link))
                    if len(rows) >= limit:
                        break
        return rows

    def get_link(self, url: str) -> dict | None:
        with self._lock:
            link = self.links.get(url)
            return dict(link) if link else None

    def output_paths(self) -> dict:
        return {
            "dir": str(self.output_dir),
            "links": str(self.output_dir / "links.csv"),
            "pages": str(self.output_dir / "pages.csv"),
            "hosts": str(self.output_dir / "hosts.txt"),
            "log": str(self._log_path),
            "state": str(self._state_path),
        }

    def export_csv(self) -> dict:
        with self._lock:
            link_rows = [
                (
                    link["host"], link["url"], link["filename"], link.get("extension", ""),
                    link["status"],
                    link.get("http"), link.get("final_url", ""), link.get("final_host", ""),
                    link.get("content_type", ""), link.get("size"), human_size(link.get("size")),
                    link.get("ssl_verified"), link["source_page"], link.get("anchor", ""),
                    link.get("pattern", ""), link.get("elapsed_ms"), link.get("error", ""),
                )
                for link in self.links.values()
            ]
            page_rows = [
                (
                    page["url"], page["status"], page["source"], page.get("http"),
                    page.get("links_found", 0), page.get("elapsed_ms"),
                    page.get("error", ""), page.get("final_url", ""),
                )
                for page in self.pages.values()
            ]
            host_rows = sorted(
                (
                    (stats["count"], stats["host"], stats.get("ok", 0),
                     stats.get("bad", 0), stats.get("example", ""))
                    for stats in self.hosts.values()
                ),
                reverse=True,
            )
        links_path = self.output_dir / "links.csv"
        pages_path = self.output_dir / "pages.csv"
        hosts_path = self.output_dir / "hosts.txt"
        with self._write_lock:
            with links_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "host", "url", "filename", "extension", "status", "http", "final_url",
                    "final_host", "content_type", "size_bytes", "size_human",
                    "ssl_verified", "source_page", "anchor", "matched_pattern",
                    "elapsed_ms", "error",
                ])
                writer.writerows(link_rows)
            with pages_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "url", "status", "source", "http", "links_found",
                    "elapsed_ms", "error", "final_url",
                ])
                writer.writerows(page_rows)
            with hosts_path.open("w", encoding="utf-8", newline="") as handle:
                handle.write("count\tok\tbad\thost\texample\n")
                for count, host, ok, bad, example in host_rows:
                    handle.write(f"{count}\t{ok}\t{bad}\t{host}\t{example}\n")
        return {
            "links": str(links_path),
            "pages": str(pages_path),
            "hosts": str(hosts_path),
            "link_count": len(link_rows),
            "page_count": len(page_rows),
            "host_count": len(host_rows),
        }

    def _state_writer(self) -> None:
        while not self._finished.wait(2.0):
            try:
                self._write_state()
            except Exception:
                pass

    def _write_state(self) -> None:
        with self._lock:
            state = {
                "config": self.config.to_dict(),
                "phase": self._phase,
                "started_at": self._start_time,
                "updated_at": now(),
                "stats": {
                    "pages": len(self.pages),
                    "pages_scanned": self._page_scanned,
                    "pages_failed": self._page_failed,
                    "links": len(self.links),
                    "links_verified": self._links_verified,
                    "links_ok": self._links_ok,
                    "links_filtered": self._links_filtered,
                    "hosts": len(self.hosts),
                },
            }
        temporary = self._state_path.with_suffix(".tmp")
        with self._write_lock:
            try:
                temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(temporary, self._state_path)
            except OSError:
                pass

    def _load_state(self) -> None:
        loaded_pages: dict[str, dict] = {}
        if self._pages_path.exists():
            with self._pages_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                        loaded_pages[record["url"]] = record
                    except (json.JSONDecodeError, KeyError):
                        continue
        loaded_links: dict[str, dict] = {}
        if self._links_path.exists():
            with self._links_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                        url = record.get("url")
                        if not url:
                            continue
                        existing = loaded_links.get(url, {})
                        existing.update(record)
                        loaded_links[url] = existing
                    except json.JSONDecodeError:
                        continue
        with self._lock:
            self.pages = loaded_pages
            self.links = loaded_links
            self.hosts = {}
            self._page_scanned = 0
            self._page_failed = 0
            self._page_skipped = 0
            self._links_verified = 0
            self._links_ok = 0
            self._links_bad = 0
            self._pages_started = 0
            for page in self.pages.values():
                status = page.get("status")
                if status in {"fetching", "queued", "scanned", "failed", "skipped"}:
                    if status in {"scanned", "failed", "skipped"}:
                        self._pages_started += 1
                    if status == "scanned":
                        self._page_scanned += 1
                    elif status == "failed":
                        self._page_failed += 1
                    elif status == "skipped":
                        self._page_skipped += 1
            self._verify_scheduled = 0
            self._host_verify_scheduled.clear()
            for link in self.links.values():
                host = link.get("host") or (urlparse(link["url"]).hostname or "").lower()
                link["host"] = host
                link["extension"] = link.get("extension") or extension_from_url(
                    link["url"], self.config.extensions,
                )
                status = link.get("status")
                if status in FINAL_LINK_STATES or status in {"queued", "verifying"}:
                    self._verify_scheduled += 1
                    self._host_verify_scheduled.add(host)
                stats = self.hosts.setdefault(host, {
                    "host": host, "count": 0, "ok": 0, "bad": 0, "example": link["url"],
                })
                stats["count"] += 1
                if status in FINAL_LINK_STATES:
                    self._links_verified += 1
                    if status == "ok":
                        self._links_ok += 1
                        stats["ok"] = stats.get("ok", 0) + 1
                    else:
                        self._links_bad += 1
                        stats["bad"] = stats.get("bad", 0) + 1
            self._recent_links.clear()
            recent = sorted(
                self.links.values(),
                key=lambda item: item.get("updated_at") or 0,
                reverse=True,
            )
            for link in recent[:30]:
                self._recent_links.append({
                    "url": link["url"], "host": link["host"],
                    "filename": link["filename"], "extension": link["extension"],
                    "time": clock(link.get("updated_at")), "status": link.get("status", ""),
                    "http": link.get("http"), "error": link.get("error", ""),
                })
        self.log("info", f"Loaded previous run: {len(self.pages)} pages, {len(self.links)} links")

    def _run_demo(self) -> None:
        self._set_phase("crawling")
        self._finished.clear()
        total = max(1, self.config.demo_pages)
        links_per_page = max(1, self.config.demo_links_per_page)
        pattern = self.config.patterns[0].strip() if self.config.patterns else "*.abrtech.top"
        if self.config.match_mode == "extensions":
            demo_domain = "demo.example"
        elif pattern.startswith("*."):
            demo_domain = pattern[2:]
        elif "://" in pattern:
            demo_domain = urlparse(pattern).hostname or "demo.example"
        elif pattern.startswith("re:"):
            demo_domain = "demo.example"
        else:
            demo_domain = pattern
        for index in range(1, total + 1):
            if self._stop.is_set():
                break
            page_url = f"{self.site_root.rstrip('/')}/page/{index}/"
            with self._lock:
                page = self._upsert_page(page_url, "fetching", "demo")
            token = f"demo{index:05d}"
            host = f"{token}.{demo_domain}"
            accepted_count = 0
            for file_index in range(1, links_per_page + 1):
                quality = ["480p", "720p", "1080p", "2160p"][file_index % 4]
                extension = DEMO_EXTENSIONS[(index + file_index) % len(DEMO_EXTENSIONS)]
                link_url = (
                    f"https://{host}/Film/{index}/Movie.{index}."
                    f"{quality}.Part{file_index:02d}.Demo.DLF.{extension}"
                )
                matched = match_pattern(link_url, self.patterns)
                if not self._link_accepted(link_url, matched):
                    if matched and self.config.match_mode == "both" and self.config.extensions:
                        with self._lock:
                            self._links_filtered += 1
                    continue
                label = matched
                if self.config.match_mode == "extensions":
                    label = f"filetype:{extension}"
                with self._lock:
                    link = self._upsert_link(
                        link_url, page_url, f"Download {quality}", label,
                        "a", "href",
                    )
                    roll = (index + file_index) % 100
                    if roll < 82:
                        link["http"] = 200
                        link["final_url"] = f"https://cdn{index % 7}.example/{link['filename']}"
                        link["final_host"] = f"cdn{index % 7}.example"
                        link["content_type"] = DEMO_CONTENT_TYPES.get(extension, "application/octet-stream")
                        link["size"] = 1_200_000_000 + index * 1000
                        link["elapsed_ms"] = 90 + file_index * 7
                        self._link_set_status(link, "ok")
                    elif roll < 90:
                        link["http"] = 403
                        link["error"] = "HTTP 403"
                        self._link_set_status(link, "blocked")
                    elif roll < 96:
                        link["http"] = 404
                        link["error"] = "HTTP 404"
                        self._link_set_status(link, "missing")
                    else:
                        link["error"] = "connection timed out"
                        self._link_set_status(link, "error")
                accepted_count += 1
            with self._lock:
                page["http"] = 200
                page["links_found"] = accepted_count
                page["elapsed_ms"] = 120
                self._page_set_status(page, "scanned")
            if index % 10 == 0:
                self.log("info", f"Demo progress: {index}/{total} pages")
            time.sleep(max(0.0, self.config.demo_delay))
        self._set_phase("stopped" if self._stop.is_set() else "done")
        self._end_time = now()
        self._finished.set()
        self._write_state()
        self.export_csv()
        self.log("info", "Demo run finished")
        try:
            self._log_file.flush()
        except OSError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stop()
        try:
            self._write_state()
        except Exception:
            pass
        try:
            self._log_file.flush()
            self._log_file.close()
        except (OSError, ValueError):
            pass
