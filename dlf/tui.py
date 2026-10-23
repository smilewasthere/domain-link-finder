from __future__ import annotations

import json
import math
import os
import time
import webbrowser
from collections import deque
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Container, Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import (
    Button,
    Checkbox,
    ContentSwitcher,
    DataTable,
    Digits,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Select,
    Sparkline,
    Static,
    Switch,
)

from .engine import Config, Engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config.json"

PALETTES = {
    "neon-lime": {
        "bg": "#070c03", "panel": "#101a06", "surface": "#0c1405",
        "primary": "#96f207", "secondary": "#46e0ff", "accent": "#ffd166",
        "text": "#e6f5d5", "muted": "#7c8f6a", "error": "#ff5d5d",
        "warning": "#ffb000", "success": "#96f207",
    },
    "cyber-blue": {
        "bg": "#050b12", "panel": "#0b1622", "surface": "#08111a",
        "primary": "#3fd0ff", "secondary": "#9b8cff", "accent": "#ffcc66",
        "text": "#d9efff", "muted": "#6d879b", "error": "#ff6b81",
        "warning": "#ffb000", "success": "#5eff9e",
    },
    "amber-crt": {
        "bg": "#120d03", "panel": "#1c1406", "surface": "#171005",
        "primary": "#ffb000", "secondary": "#ff7a00", "accent": "#ffe08a",
        "text": "#ffeccf", "muted": "#9a855f", "error": "#ff5555",
        "warning": "#ffb000", "success": "#9dff5e",
    },
    "mono": {
        "bg": "#080808", "panel": "#151515", "surface": "#0f0f0f",
        "primary": "#e8e8e8", "secondary": "#a8a8a8", "accent": "#ffffff",
        "text": "#d0d0d0", "muted": "#777777", "error": "#ff6b6b",
        "warning": "#d0d0d0", "success": "#b8ffb8",
    },
}

THEME_OPTIONS = [
    ("Neon Lime", "neon-lime"),
    ("Cyber Blue", "cyber-blue"),
    ("Amber CRT", "amber-crt"),
    ("Mono", "mono"),
]

ANIMATION_OPTIONS = [("Full", "full"), ("Basic", "basic"), ("Off", "none")]

STATUS_STYLES = {
    "queued": "muted", "fetching": "secondary", "scanned": "success",
    "failed": "error", "skipped": "warning",
    "gathered": "muted", "verifying": "secondary", "unverified": "muted",
    "ok": "success", "blocked": "warning", "missing": "warning",
    "error": "error",
}

CARD_CONFIG = [
    ("pages", "Pages Gathered", "pages_gathered", "all", "urls"),
    ("scanned", "Pages Scanned", "pages_scanned", "scanned", "urls"),
    ("remaining", "Pages Remaining", "pages_pending", "pending", "urls"),
    ("links", "Links Gathered", "links_gathered", "all", "links"),
    ("hosts", "Unique Hosts", "hosts", "all", "links"),
    ("verified", "Links Verified", "links_verified", "verified", "links"),
    ("ok", "Verify OK", "links_ok", "ok", "links"),
    ("bad", "Verify Issues", "links_bad", "failed", "links"),
]


def load_saved_config() -> Config:
    if CONFIG_PATH.exists():
        try:
            return Config.from_dict(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return Config()


def load_ui_settings() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    ui = data.get("ui")
    return ui if isinstance(ui, dict) else {}


def format_seconds(value: float | None) -> str:
    if value is None or value != value or value < 0:
        return "--"
    value = int(value)
    if value < 60:
        return f"{value}s"
    minutes, seconds = divmod(value, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def format_elapsed(seconds: float) -> str:
    seconds = int(max(0, seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class StatCard(Vertical):
    class Clicked(Message):
        def __init__(self, card_key: str) -> None:
            self.card_key = card_key
            super().__init__()

    def __init__(self, card_key: str, title: str, hint: str = "") -> None:
        super().__init__(id=f"stat-{card_key}", classes="stat-card")
        self.card_key = card_key
        self.card_title = title
        self.card_hint = hint

    def compose(self) -> ComposeResult:
        yield Static(self.card_title, classes="stat-title")
        yield Digits("0", id=f"digits-{self.card_key}", classes="stat-value")
        yield Static(self.card_hint, id=f"hint-{self.card_key}", classes="stat-hint")

    def set_value(self, value: int, hint: str | None = None) -> None:
        self.query_one(Digits).update(str(max(0, int(value))))
        if hint is not None:
            self.query_one(f"#hint-{self.card_key}", Static).update(hint)

    def on_click(self) -> None:
        self.post_message(self.Clicked(self.card_key))


class HelpScreen(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("HELP & SHORTCUTS", classes="dialog-title")
            yield Static(
                Text.from_markup(
                    "[b]F1[/b] Home   [b]F2[/b] Dashboard   [b]F3[/b] URLs   "
                    "[b]F4[/b] Links   [b]F5[/b] Logs   [b]F6[/b] Settings\n"
                    "[b]Ctrl+P[/b] Command palette   [b]Ctrl+Q[/b] Quit   "
                    "[b]F9[/b] Export CSV\n\n"
                    "[b]Home[/b]\n"
                    "Set the site URL and one or more host patterns, then Start.\n"
                    "Patterns support wildcards (`*.abrtech.top`), full-URL globs, "
                    "and `re:` regular expressions.\n\n"
                    "[b]Dashboard[/b]\n"
                    "Click any stat card to jump to the filtered URLs or Links view.\n"
                    "Pause and Resume affect both crawling and verification. Stop is graceful "
                    "and keeps every gathered result.\n\n"
                    "[b]URLs[/b]\n"
                    "Every page discovered from the sitemap or followed links is listed with "
                    "its gather/scan status. Click a row for details and actions.\n\n"
                    "[b]Links[/b]\n"
                    "All URLs matching your patterns. Click a row to copy, open, verify, or "
                    "jump to its source page. Verify filtered/bulk actions are available above "
                    "the table.\n\n"
                    "[b]Logs[/b]\n"
                    "Live, color-coded log tail. Every line is also stored in run.log.\n\n"
                    "[b]Settings[/b]\n"
                    "Change theme, animations, page size, headers, proxy and extra seeds.\n"
                    "Settings are saved to config.json for future runs."
                ),
                classes="dialog-body",
            )
            yield Button("Close", id="help-close", variant="primary")

    def on_mount(self) -> None:
        for button in self.query(Button):
            button.styles.line_pad = 0

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "help-close":
            self.dismiss(None)


class DLFApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "Domain Link Finder"
    SUB_TITLE = "pattern-based download link crawler"

    BINDINGS = [
        Binding("f1", "view_home", "Home", show=False),
        Binding("f2", "view_dash", "Dashboard", show=False),
        Binding("f3", "view_urls", "URLs", show=False),
        Binding("f4", "view_links", "Links", show=False),
        Binding("f5", "view_logs", "Logs", show=False),
        Binding("f6", "view_settings", "Settings", show=False),
        Binding("f9", "export_csv", "Export", show=False),
        Binding("ctrl+p", "command_palette", "Palette"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, initial_config: Config | None = None,
                 resume_dir: str | Path | None = None,
                 demo: bool = False, auto_start: bool = False,
                 ui_settings: dict | None = None) -> None:
        super().__init__()
        self.initial_config = initial_config or load_saved_config()
        self.resume_dir = Path(resume_dir) if resume_dir else None
        self.demo_requested = demo
        self.auto_start = auto_start
        ui = ui_settings or {}
        theme_name = str(ui.get("theme", "neon-lime"))
        self._theme_name = theme_name if theme_name in PALETTES else "neon-lime"
        animations = str(ui.get("animations", "full"))
        self._animations = (
            animations if animations in {value for _, value in ANIMATION_OPTIONS} else "full"
        )
        try:
            page_size = int(ui.get("page_size", 50))
        except (TypeError, ValueError):
            page_size = 50
        self._page_size = max(10, min(500, page_size))
        self.engine: Engine | None = None
        self._view = "home"
        self._palette = PALETTES[self._theme_name]
        self._url_page = 0
        self._link_page = 0
        self._host_filter = ""
        self._selected_page_url = ""
        self._selected_link_url = ""
        self._last_log_seq = 0
        self._log_cache: deque[dict] = deque(maxlen=5000)
        self._last_revision = -1
        self._last_url_refresh = 0.0
        self._last_link_refresh = 0.0
        self._last_recent_refresh = 0.0
        self._last_host_refresh = 0.0
        self._last_stats_tick = 0.0
        self._display_stats: dict[str, int] = {}
        self._stats_snapshot: dict = {}
        self._url_table_keys: list[str] = []
        self._link_table_keys: list[str] = []
        self._url_rows_cache: list[dict] = []
        self._link_rows_cache: list[dict] = []
        self._host_signature: tuple = ()
        self._pulse_on = True
        self._spinner_index = 0
        self._run_notified = False
        self._rate_history: deque[float] = deque(maxlen=60)
        self._last_rate_sample = 0.0
        self._recent_signature: tuple = ()
        self._issues_signature: tuple | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static(id="topbar-left")
            yield Static(id="topbar-right")
        with Horizontal(id="shell"):
            with Vertical(id="sidebar"):
                yield Static("DOMAIN\nLINK\nFINDER", id="brand")
                yield Static("TERMINAL CRAWLER", id="brand-sub")
                yield Button("Home", id="nav-home", classes="nav active")
                yield Button("Dashboard", id="nav-dash", classes="nav")
                yield Button("URLs", id="nav-urls", classes="nav")
                yield Button("Links", id="nav-links", classes="nav")
                yield Button("Logs", id="nav-logs", classes="nav")
                yield Button("Settings", id="nav-settings", classes="nav")
                yield Static(id="side-status", classes="side-status")
                yield Static(
                    "Pattern-driven crawler\nSitemap + link discovery\n"
                    "Live page and verify state",
                    id="side-tip",
                )
            with Vertical(id="main"):
                with ContentSwitcher(initial="view-home", id="views"):
                    with VerticalScroll(id="view-home", classes="view"):
                        with Center():
                            with Vertical(classes="home-panel"):
                                yield Static(
                                    Text.assemble(
                                        ("DOMAIN ", "bold #96f207"),
                                        ("LINK FINDER", "bold #46e0ff"),
                                    ),
                                    classes="home-title",
                                )
                                yield Static(
                                    "Crawl a site, collect every URL matching your host patterns, "
                                    "and verify each link live.",
                                    classes="home-subtitle",
                                )
                                with Horizontal(classes="field"):
                                    yield Label("Site URL", classes="field-label")
                                    yield Input(
                                        value=self.initial_config.start_url,
                                        placeholder="https://www.example.com/",
                                        id="inp-url",
                                    )
                                with Horizontal(classes="field"):
                                    yield Label("Match patterns", classes="field-label")
                                    yield Input(
                                        value=", ".join(self.initial_config.patterns),
                                        placeholder="*.abrtech.top, *.example.net",
                                        id="inp-patterns",
                                    )
                                with Horizontal(classes="field-row"):
                                    with Vertical(classes="field compact"):
                                        yield Label("Max pages (0 = all)", classes="field-label")
                                        yield Input(
                                            value=str(self.initial_config.max_pages),
                                            id="inp-max-pages", type="integer",
                                        )
                                    with Vertical(classes="field compact"):
                                        yield Label("Verify limit (0 = all)", classes="field-label")
                                        yield Input(
                                            value=str(self.initial_config.max_verify),
                                            id="inp-max-verify", type="integer",
                                        )
                                    with Vertical(classes="field compact"):
                                        yield Label("Crawl workers", classes="field-label")
                                        yield Input(
                                            value=str(self.initial_config.crawl_workers),
                                            id="inp-workers", type="integer",
                                        )
                                    with Vertical(classes="field compact"):
                                        yield Label("Delay seconds", classes="field-label")
                                        yield Input(
                                            value=str(self.initial_config.crawl_delay),
                                            id="inp-delay",
                                        )
                                with Horizontal(classes="field-row"):
                                    with Vertical(classes="field compact"):
                                        yield Label("Verification", classes="field-label")
                                        yield Select(
                                            [("Every link", "all"), ("One per host", "hosts"),
                                             ("Off", "none")],
                                            value=self.initial_config.verify_mode,
                                            allow_blank=False, id="sel-verify-mode",
                                        )
                                    with Vertical(classes="field compact"):
                                        yield Label("Discovery", classes="field-label")
                                        with Horizontal(classes="toggle-row"):
                                            yield Switch(value=self.initial_config.use_sitemap, id="sw-sitemap")
                                            yield Static("Use sitemap", classes="toggle-label")
                                        with Horizontal(classes="toggle-row"):
                                            yield Switch(value=self.initial_config.respect_robots, id="sw-robots")
                                            yield Static("Respect robots", classes="toggle-label")
                                with Horizontal(classes="button-row"):
                                    yield Button("Start Crawl", id="btn-start", variant="success")
                                    yield Button("Resume Last Run", id="btn-resume")
                                    yield Button("Demo Mode", id="btn-demo")
                                yield Static(id="home-hint", classes="home-hint")
                                yield Static(id="home-latest", classes="home-latest")
                    with VerticalScroll(id="view-dash", classes="view"):
                        with Horizontal(classes="stats-row"):
                            for index in range(4):
                                card = CARD_CONFIG[index]
                                yield StatCard(card[0], card[1])
                        with Horizontal(classes="stats-row"):
                            for index in range(4, 8):
                                card = CARD_CONFIG[index]
                                yield StatCard(card[0], card[1])
                        with Horizontal(classes="panels-row"):
                            with Vertical(classes="panel progress-panel"):
                                yield Static("CRAWL PROGRESS", classes="panel-title")
                                yield Static(id="crawl-label", classes="progress-label")
                                yield ProgressBar(total=100, show_eta=False, id="bar-crawl")
                                yield Static("VERIFY PROGRESS", classes="panel-title")
                                yield Static(id="verify-label", classes="progress-label")
                                yield ProgressBar(total=100, show_eta=False, id="bar-verify")
                            with Vertical(classes="panel spark-panel"):
                                yield Static("PAGE THROUGHPUT", classes="panel-title")
                                yield Sparkline([], id="spark-pages")
                                yield Static(id="throughput-label", classes="progress-label")
                        with Horizontal(classes="panels-row recent-row"):
                            with Vertical(classes="panel recent-panel"):
                                yield Static("RECENT MATCHES", classes="panel-title")
                                yield DataTable(id="recent-table", cursor_type="none")
                            with Vertical(classes="panel issues-panel"):
                                yield Static("RECENT ISSUES", classes="panel-title")
                                yield Static(
                                    "No issues yet. Live log is available in the Logs view.",
                                    id="issues-body",
                                    classes="issues-body",
                                )
                        with Horizontal(classes="button-row"):
                            yield Button("Pause", id="btn-pause")
                            yield Button("Resume", id="btn-resume-run")
                            yield Button("Stop", id="btn-stop", variant="error")
                            yield Button("Export CSV", id="btn-export")
                            yield Button("Open Output Folder", id="btn-open-output")
                    with Vertical(id="view-urls", classes="view"):
                        with Horizontal(classes="toolbar"):
                            yield Input(placeholder="Search page URLs...", id="url-search")
                            yield Select(
                                [("All statuses", "all"), ("Gathered / queued", "pending"),
                                 ("Scanned", "scanned"), ("Failed", "failed"),
                                 ("Skipped", "skipped")],
                                value="all", allow_blank=False, id="url-status",
                            )
                            yield Button("Prev", id="url-prev", classes="page-btn")
                            yield Static(id="url-page-label", classes="page-label")
                            yield Button("Next", id="url-next", classes="page-btn")
                        with Horizontal(id="urls-body"):
                            yield DataTable(id="urls-table", cursor_type="row", zebra_stripes=True)
                            with Vertical(classes="detail-panel"):
                                yield Static("Select a page row", id="url-detail-body")
                                with Horizontal(classes="detail-actions"):
                                    yield Button("Copy URL", id="url-copy", disabled=True)
                                    yield Button("Open", id="url-open", disabled=True)
                                    yield Button("Rescan", id="url-rescan", disabled=True)
                    with Vertical(id="view-links", classes="view"):
                        with Horizontal(classes="toolbar"):
                            yield Input(placeholder="Search links, hosts, filenames...", id="link-search")
                            yield Select(
                                [("All links", "all"), ("Verified (any)", "verified"),
                                 ("OK", "ok"), ("Issues", "failed"),
                                 ("Pending", "pending"), ("Unverified", "unverified"),
                                 ("Skipped", "skipped")],
                                value="all", allow_blank=False, id="link-status",
                            )
                            yield Button("Prev", id="link-prev", classes="page-btn")
                            yield Static(id="link-page-label", classes="page-label")
                            yield Button("Next", id="link-next", classes="page-btn")
                        with Horizontal(classes="host-bar"):
                            yield Static("TOP HOSTS", classes="host-label")
                            yield Horizontal(id="host-chips")
                            yield Button("Clear host", id="host-clear", classes="chip-clear")
                        with Horizontal(id="links-body"):
                            yield DataTable(id="links-table", cursor_type="row", zebra_stripes=True)
                            with Vertical(classes="detail-panel"):
                                yield Static("Select a link row", id="link-detail-body")
                                with Horizontal(classes="detail-actions"):
                                    yield Button("Copy URL", id="link-copy", disabled=True)
                                    yield Button("Open", id="link-open", disabled=True)
                                    yield Button("Verify now", id="link-verify", disabled=True)
                                    yield Button("Source page", id="link-source", disabled=True)
                        with Horizontal(classes="button-row bulk-row"):
                            yield Button("Verify filtered", id="btn-verify-filtered")
                            yield Button("Verify failed", id="btn-verify-failed")
                            yield Button("Copy filtered", id="btn-copy-filtered")
                            yield Button("Export CSV", id="btn-export-links")
                    with Vertical(id="view-logs", classes="view"):
                        with Horizontal(classes="toolbar"):
                            yield Select(
                                [("All levels", "all"), ("Debug", "debug"),
                                 ("Info", "info"), ("Warnings", "warn"),
                                 ("Errors", "error")],
                                value="all", allow_blank=False, id="log-level",
                            )
                            yield Input(placeholder="Search log...", id="log-search")
                            yield Checkbox("Auto-scroll", value=True, id="log-autoscroll")
                            yield Button("Clear view", id="log-clear")
                            yield Button("Open log file", id="log-open")
                        yield RichLog(id="logs", wrap=True, highlight=False, markup=False,
                                      max_lines=5000)
                    with VerticalScroll(id="view-settings", classes="view"):
                        with Vertical(classes="settings-panel"):
                            yield Static("APPEARANCE", classes="panel-title")
                            with Horizontal(classes="field"):
                                yield Label("Theme", classes="field-label")
                                yield Select(THEME_OPTIONS, value=self._theme_name,
                                             allow_blank=False, id="sel-theme")
                            with Horizontal(classes="field"):
                                yield Label("Animations", classes="field-label")
                                yield Select(ANIMATION_OPTIONS, value=self._animations,
                                             allow_blank=False, id="sel-anim")
                            with Horizontal(classes="field"):
                                yield Label("Rows per page", classes="field-label")
                                yield Input(value=str(self._page_size), id="inp-page-size",
                                            type="integer")
                            yield Static("NETWORK & ADVANCED", classes="panel-title")
                            with Horizontal(classes="field"):
                                yield Label("Extra seed URLs", classes="field-label")
                                yield Input(
                                    value=", ".join(self.initial_config.extra_seeds),
                                    placeholder="https://site/post-1/, https://site/post-2/",
                                    id="inp-seeds",
                                )
                            with Horizontal(classes="field"):
                                yield Label("User-Agent", classes="field-label")
                                yield Input(value=self.initial_config.user_agent, id="inp-ua")
                            with Horizontal(classes="field"):
                                yield Label("Cookie header", classes="field-label")
                                yield Input(value=self.initial_config.cookie, id="inp-cookie",
                                            password=True)
                            with Horizontal(classes="field"):
                                yield Label("Proxy", classes="field-label")
                                yield Input(value=self.initial_config.proxy,
                                            placeholder="http://127.0.0.1:8080", id="inp-proxy")
                            with Horizontal(classes="field-row"):
                                with Vertical(classes="field compact"):
                                    yield Label("Per-host verify limit", classes="field-label")
                                    yield Input(value=str(self.initial_config.per_host_limit),
                                                id="inp-per-host", type="integer")
                                with Vertical(classes="field compact"):
                                    yield Label("Request timeout", classes="field-label")
                                    yield Input(value=str(self.initial_config.timeout),
                                                id="inp-timeout")
                                with Vertical(classes="field compact"):
                                    yield Label("Retries", classes="field-label")
                                    yield Input(value=str(self.initial_config.retries),
                                                id="inp-retries", type="integer")
                            with Horizontal(classes="button-row"):
                                yield Button("Save Settings", id="btn-save-settings",
                                             variant="primary")
                                yield Button("Help / Shortcuts", id="btn-help")
                            yield Static(id="settings-hint", classes="home-hint")
        yield Static(id="footerbar")

    def on_mount(self) -> None:
        for button in self.query(Button):
            button.styles.line_pad = 0
        self._register_themes()
        self._apply_theme(self._theme_name)
        self._build_tables()
        self._update_home()
        self._refresh_latest_run()
        self.set_interval(0.2, self._tick)
        self.set_interval(0.5, self._pulse)
        self.set_interval(1.0, self._slow_tick)
        self.call_after_refresh(self._initial_action)
        if self.resume_dir:
            self._load_resume(self.resume_dir)
        if self.demo_requested:
            self.call_later(self.start_demo)
        elif self.auto_start:
            self.call_later(self.start_crawl)

    def _initial_action(self) -> None:
        self._switch_view("home", animate=False)
        self._update_topbar()
        self._update_footer()

    def _apply_theme(self, name: str) -> None:
        self.theme = name
        self.call_after_refresh(self._invalidate_style_caches)

    def _invalidate_style_caches(self) -> None:
        screen = self.screen_stack[0] if self.screen_stack else self.screen
        for node in screen.walk_children(with_self=True):
            node.notify_style_update()
            node.refresh(repaint=True)

    def _ui_ready(self) -> bool:
        try:
            self.query_one("#topbar-left")
        except NoMatches:
            return False
        return True

    def _register_themes(self) -> None:
        for name, palette in PALETTES.items():
            self.register_theme(Theme(
                name=name,
                primary=palette["primary"],
                secondary=palette["secondary"],
                accent=palette["accent"],
                warning=palette["warning"],
                error=palette["error"],
                success=palette["success"],
                foreground=palette["text"],
                background=palette["bg"],
                surface=palette["surface"],
                panel=palette["panel"],
                dark=True,
            ))

    def _build_tables(self) -> None:
        urls = self.query_one("#urls-table", DataTable)
        urls.add_column("#", key="idx", width=4)
        urls.add_column("Page URL", key="url", width=64)
        urls.add_column("Source", key="source", width=9)
        urls.add_column("Gathered", key="gathered", width=9)
        urls.add_column("Scan", key="status", width=10)
        urls.add_column("HTTP", key="http", width=6)
        urls.add_column("Links", key="links", width=6)
        urls.add_column("Time", key="time", width=8)
        links = self.query_one("#links-table", DataTable)
        links.add_column("#", key="idx", width=4)
        links.add_column("Host", key="host", width=34)
        links.add_column("File", key="file", width=44)
        links.add_column("Verify", key="status", width=10)
        links.add_column("HTTP", key="http", width=6)
        links.add_column("Size", key="size", width=9)
        links.add_column("Final host", key="final", width=32)
        recent = self.query_one("#recent-table", DataTable)
        recent.add_column("Host", key="host", width=20)
        recent.add_column("File", key="file", width=24)
        recent.add_column("State", key="state", width=9)

    def _update_home(self) -> None:
        cfg = self.initial_config
        self.query_one("#inp-url", Input).value = cfg.start_url
        self.query_one("#inp-patterns", Input).value = ", ".join(cfg.patterns)
        self.query_one("#inp-max-pages", Input).value = str(cfg.max_pages)
        self.query_one("#inp-max-verify", Input).value = str(cfg.max_verify)
        self.query_one("#inp-workers", Input).value = str(cfg.crawl_workers)
        self.query_one("#inp-delay", Input).value = str(cfg.crawl_delay)
        self.query_one("#inp-seeds", Input).value = ", ".join(cfg.extra_seeds)
        self.query_one("#inp-ua", Input).value = cfg.user_agent
        self.query_one("#inp-cookie", Input).value = cfg.cookie
        self.query_one("#inp-proxy", Input).value = cfg.proxy
        self.query_one("#inp-per-host", Input).value = str(cfg.per_host_limit)
        self.query_one("#inp-timeout", Input).value = str(cfg.timeout)
        self.query_one("#inp-retries", Input).value = str(cfg.retries)

    def _refresh_latest_run(self) -> None:
        latest = Engine.find_latest_run(PROJECT_ROOT / "output")
        target = self.query_one("#home-latest", Static)
        if latest is None:
            target.update("No previous run found. Start a crawl or try Demo Mode.")
            return
        state = {}
        state_path = latest / "state.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = {}
        stats = state.get("stats", {})
        target.update(
            f"Latest run: {latest.name}  |  pages {stats.get('pages', 0):,}  "
            f"links {stats.get('links', 0):,}  |  press Resume Last Run"
        )

    def _switch_view(self, name: str, animate: bool = True, filter_value: str | None = None) -> None:
        self._view = name
        switcher = self.query_one("#views", ContentSwitcher)
        switcher.current = f"view-{name}"
        for button in self.query(".nav"):
            button.remove_class("active")
            if button.id == f"nav-{name}":
                button.add_class("active")
        if filter_value is not None:
            if name == "urls":
                self.query_one("#url-status", Select).value = filter_value
                self._url_page = 0
            elif name == "links":
                self.query_one("#link-status", Select).value = filter_value
                self._link_page = 0
        view = self.query_one(f"#view-{name}")
        if animate and self._animations != "none":
            view.styles.opacity = 0.0

            def fade_in() -> None:
                try:
                    view.styles.opacity = 1.0
                except Exception:
                    pass
                self.set_timer(0.25, self._invalidate_style_caches)

            self.call_after_refresh(fade_in)
        else:
            view.styles.opacity = 1.0
        if name == "urls":
            self._refresh_urls(force=True)
        elif name == "links":
            self._refresh_links(force=True)
        elif name == "logs":
            self._rebuild_logs()
        elif name == "settings":
            self.query_one("#settings-hint", Static).update(
                "Advanced values are saved to config.json and applied to new runs."
            )

    def action_view_home(self) -> None:
        self._switch_view("home")

    def action_view_dash(self) -> None:
        self._switch_view("dash")

    def action_view_urls(self) -> None:
        self._switch_view("urls")

    def action_view_links(self) -> None:
        self._switch_view("links")

    def action_view_logs(self) -> None:
        self._switch_view("logs")

    def action_view_settings(self) -> None:
        self._switch_view("settings")

    def action_export_csv(self) -> None:
        if self.engine is None:
            self.notify("No run to export yet", severity="warning")
            return
        result = self.engine.export_csv()
        self.notify(
            f"Exported {result['link_count']:,} links and {result['page_count']:,} pages",
            title="Export complete", severity="information",
        )
        self._refresh_latest_run()

    def action_quit(self) -> None:
        if self.engine is not None:
            self.engine.close()
        self.exit()

    def on_unmount(self) -> None:
        if self.engine is not None:
            self.engine.close()

    def _config_from_inputs(self) -> Config:
        url = self.query_one("#inp-url", Input).value.strip()
        patterns = [part.strip() for part in self.query_one("#inp-patterns", Input).value.split(",")
                    if part.strip()]
        if not url.startswith(("http://", "https://")):
            raise ValueError("Site URL must start with http:// or https://")
        if not patterns:
            raise ValueError("Add at least one match pattern")
        def as_int(widget_id: str, default: int) -> int:
            raw = self.query_one(widget_id, Input).value.strip()
            return int(raw) if raw else default
        def as_float(widget_id: str, default: float) -> float:
            raw = self.query_one(widget_id, Input).value.strip()
            return float(raw) if raw else default
        seeds = [part.strip() for part in self.query_one("#inp-seeds", Input).value.split(",")
                 if part.strip()]
        mode = self.query_one("#sel-verify-mode", Select).value
        return Config(
            start_url=url,
            patterns=patterns,
            max_pages=max(0, as_int("#inp-max-pages", 0)),
            max_verify=max(0, as_int("#inp-max-verify", 0)),
            verify_mode=str(mode),
            crawl_workers=max(1, as_int("#inp-workers", 8)),
            crawl_delay=max(0.0, as_float("#inp-delay", 0.2)),
            use_sitemap=bool(self.query_one("#sw-sitemap", Switch).value),
            respect_robots=bool(self.query_one("#sw-robots", Switch).value),
            extra_seeds=seeds,
            user_agent=self.query_one("#inp-ua", Input).value.strip() or Config().user_agent,
            cookie=self.query_one("#inp-cookie", Input).value.strip(),
            proxy=self.query_one("#inp-proxy", Input).value.strip(),
            per_host_limit=max(1, as_int("#inp-per-host", 1)),
            timeout=max(1.0, as_float("#inp-timeout", 20.0)),
            retries=max(0, as_int("#inp-retries", 2)),
        )

    def start_crawl(self) -> None:
        if self.engine is not None and self.engine.phase not in {"done", "stopped", "error"}:
            self.notify("A run is already active. Stop it first.", severity="warning")
            return
        try:
            config = self._config_from_inputs()
        except ValueError as exc:
            self.notify(str(exc), title="Configuration", severity="error")
            return
        if self.engine is not None:
            self.engine.close()
        self.engine = Engine(config)
        self._begin_engine(f"Crawling {config.start_url}")

    def start_demo(self) -> None:
        if self.engine is not None and self.engine.phase not in {"done", "stopped", "error"}:
            self.notify("A run is already active. Stop it first.", severity="warning")
            return
        if self.engine is not None:
            self.engine.close()
        config = Config(
            start_url="https://demo.local/",
            patterns=["*.abrtech.top"],
            demo=True,
            demo_pages=800,
            demo_links_per_page=7,
            demo_delay=0.012,
            verify_mode="all",
        )
        self.engine = Engine(config)
        self._begin_engine("Demo mode is generating synthetic pages and links")

    def resume_last(self) -> None:
        latest = Engine.find_latest_run(PROJECT_ROOT / "output")
        if latest is None:
            self.notify("No previous run to resume", severity="warning")
            return
        if self.engine is not None and self.engine.phase not in {"done", "stopped", "error"}:
            self.notify("A run is already active. Stop it first.", severity="warning")
            return
        if self.engine is not None:
            self.engine.close()
        self.engine = Engine.from_resume(latest)
        self._begin_engine(f"Resuming {latest.name}")

    def _load_resume(self, run_dir: Path) -> None:
        self.engine = Engine.from_resume(run_dir)
        self._begin_engine(f"Resumed {run_dir.name}")

    def _begin_engine(self, message: str) -> None:
        self._reset_run_ui()
        if self.engine is None:
            return
        self.engine.start()
        self._switch_view("dash")
        self._run_notified = False
        self.notify(message, title="Run started", severity="information")

    def _reset_run_ui(self) -> None:
        self._last_log_seq = 0
        self._log_cache.clear()
        self._last_revision = -1
        self._url_table_keys = []
        self._link_table_keys = []
        self._url_rows_cache = []
        self._link_rows_cache = []
        self._selected_page_url = ""
        self._selected_link_url = ""
        self._url_page = 0
        self._link_page = 0
        self._host_filter = ""
        self._host_signature = ()
        self._recent_signature = ()
        self._issues_signature = None
        self._display_stats = {}
        self._stats_snapshot = {}
        self._run_notified = False
        self._rate_history.clear()
        for table_id in ("#urls-table", "#links-table", "#recent-table"):
            self.query_one(table_id, DataTable).clear()
        self.query_one("#url-detail-body", Static).update("Select a page row")
        self.query_one("#link-detail-body", Static).update("Select a link row")
        self.query_one("#issues-body", Static).update(
            "No issues yet. Live log is available in the Logs view."
        )
        self.query_one("#logs", RichLog).clear()
        self.query_one("#url-search", Input).value = ""
        self.query_one("#link-search", Input).value = ""
        for button_id in ("#url-copy", "#url-open", "#url-rescan"):
            self.query_one(button_id, Button).disabled = True
        for button_id in ("#link-copy", "#link-open", "#link-verify", "#link-source"):
            self.query_one(button_id, Button).disabled = True

    def _tick(self) -> None:
        if not self._ui_ready():
            return
        engine = self.engine
        if engine is None:
            self._update_topbar()
            return
        stats = engine.stats()
        self._stats_snapshot = stats
        logs = engine.logs_since(self._last_log_seq)
        if logs:
            self._last_log_seq = logs[-1]["seq"]
            self._log_cache.extend(logs)
            if self._view == "logs":
                for entry in logs:
                    if self._log_matches(entry):
                        self._write_log(entry)
        self._update_stats(stats)
        self._update_progress(stats)
        self._update_topbar(stats)
        self._update_footer(stats)
        now = time.monotonic()
        if now - self._last_rate_sample >= 1.0:
            self._last_rate_sample = now
            self._rate_history.append(stats.get("page_rate", 0.0))
            try:
                self.query_one("#spark-pages", Sparkline).data = list(self._rate_history)
            except Exception:
                pass
            self.query_one("#throughput-label", Static).update(
                f"pages/s {stats.get('page_rate', 0):.2f}   verify/s {stats.get('verify_rate', 0):.2f}"
            )
        if self._view == "urls" and now - self._last_url_refresh > 0.55:
            self._refresh_urls()
        if self._view == "links" and now - self._last_link_refresh > 0.7:
            self._refresh_links()
        if now - self._last_recent_refresh > 1.2:
            self._last_recent_refresh = now
            self._refresh_recent()
            self._refresh_hosts()
        if stats["phase"] in {"done", "stopped", "error"} and not self._run_notified:
            self._run_notified = True
            severity = "error" if stats["phase"] == "error" else (
                "warning" if stats["phase"] == "stopped" else "information"
            )
            self.notify(
                f"Pages scanned {stats['pages_scanned']:,}  |  "
                f"links gathered {stats['links_gathered']:,}  |  "
                f"verified {stats['links_verified']:,}",
                title=f"Run {stats['phase']}", severity=severity,
            )
            self._refresh_latest_run()
        self._update_button_states(stats)

    def _update_stats(self, stats: dict) -> None:
        now = time.monotonic()
        if now - self._last_stats_tick < 0.05:
            return
        self._last_stats_tick = now
        card_values = {
            "pages": (stats["pages_gathered"], f"{stats['pages_pending']:,} remaining"),
            "scanned": (stats["pages_scanned"], f"{stats['pages_failed']:,} failed"),
            "remaining": (stats["pages_pending"], f"{stats['pages_gathered']:,} gathered"),
            "links": (stats["links_gathered"], f"{stats['links_pending']:,} pending"),
            "hosts": (stats["hosts"], "unique matching hosts"),
            "verified": (stats["links_verified"], f"of {stats['links_gathered']:,} gathered"),
            "ok": (stats["links_ok"], "confirmed links"),
            "bad": (stats["links_bad"], "blocked / failed"),
        }
        for key, (target, hint) in card_values.items():
            current = self._display_stats.get(key, 0)
            if current != target:
                delta = target - current
                step = int(delta * 0.35)
                if step == 0:
                    step = 1 if delta > 0 else -1
                current += step
                self._display_stats[key] = current
            try:
                card = self.query_one(f"#stat-{key}", StatCard)
                card.set_value(current, hint)
            except Exception:
                pass

    def _update_progress(self, stats: dict) -> None:
        pages_total = max(1, stats["pages_gathered"])
        pages_done = stats["pages_scanned"] + stats["pages_failed"] + stats["pages_skipped"]
        pages_left = max(0, pages_total - pages_done)
        self.query_one("#bar-crawl", ProgressBar).update(total=pages_total, progress=pages_done)
        self.query_one("#crawl-label", Static).update(
            f"{pages_done:,} / {stats['pages_gathered']:,} pages  |  "
            f"{pages_left:,} left  |  ETA {format_seconds(stats['page_eta'])}  |  "
            f"{stats['page_rate']:.2f} p/s"
        )
        links_total = max(1, stats["links_gathered"])
        links_done = stats["links_verified"]
        self.query_one("#bar-verify", ProgressBar).update(total=links_total, progress=links_done)
        self.query_one("#verify-label", Static).update(
            f"{links_done:,} / {stats['links_gathered']:,} links  |  "
            f"{stats['links_pending']:,} left  |  ETA {format_seconds(stats['verify_eta'])}  |  "
            f"{stats['verify_rate']:.2f} l/s"
        )

    def _status_text(self, stats: dict) -> tuple[str, str]:
        if stats.get("phase") in {"done", "stopped", "error", "idle"}:
            phase = stats.get("phase", "idle")
        elif stats.get("paused"):
            phase = "paused"
        elif stats.get("crawl_done") and not stats.get("verify_done"):
            phase = "verifying"
        elif stats.get("phase") == "discovering":
            phase = "discovering"
        else:
            phase = "crawling"
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        spinner = frames[self._spinner_index % len(frames)] if phase in {"crawling", "verifying", "discovering"} else "●"
        return phase, spinner

    def _update_topbar(self, stats: dict | None = None) -> None:
        stats = stats or self._stats_snapshot
        palette = self._palette
        phase, spinner = self._status_text(stats) if stats else ("idle", "●")
        dot_color = palette["success"] if phase == "crawling" else (
            palette["secondary"] if phase in {"verifying", "discovering", "paused"} else
            palette["error"] if phase == "error" else palette["muted"]
        )
        left = Text()
        left.append(" DOMAIN LINK FINDER ", style=f"bold {palette['bg']} on {palette['primary']}")
        left.append("  ")
        left.append(f"{spinner} {phase.upper()}", style=f"bold {dot_color}")
        if stats:
            left.append("  ")
            left.append(f"pages {stats['pages_scanned']:,}/{stats['pages_gathered']:,}",
                        style=palette["muted"])
            left.append("  ")
            left.append(f"links {stats['links_gathered']:,}", style=palette["muted"])
            left.append("  ")
            left.append(f"hosts {stats['hosts']:,}", style=palette["muted"])
        else:
            left.append("  ready", style=palette["muted"])
        self.query_one("#topbar-left", Static).update(left)
        right = Text()
        if stats:
            right.append(format_elapsed(stats["elapsed"]), style=f"bold {palette['text']}")
            right.append("  ")
            right.append(phase.upper(), style=dot_color)
        else:
            right.append("00:00", style=palette["muted"])
            right.append("  IDLE", style=palette["muted"])
        self.query_one("#topbar-right", Static).update(right)
        side = Text()
        side.append(f"{phase.upper()}\n", style=f"bold {dot_color}")
        if stats:
            side.append(f"{stats['pages_pending']:,} pages pending\n", style=palette["muted"])
            side.append(f"{stats['links_pending']:,} links pending", style=palette["muted"])
        else:
            side.append("no active run", style=palette["muted"])
        self.query_one("#side-status", Static).update(side)

    def _update_footer(self, stats: dict | None = None) -> None:
        palette = self._palette
        stats = stats or self._stats_snapshot
        line = Text()
        line.append(" F1 Home  F2 Dash  F3 URLs  F4 Links  F5 Logs  F6 Settings  F9 Export  ^Q Quit ",
                    style=palette["muted"])
        if stats:
            line.append("│ ", style=palette["panel"])
            line.append(f"scanned {stats['pages_scanned']:,}/{stats['pages_gathered']:,}  ",
                        style=palette["text"])
            line.append(f"verified {stats['links_verified']:,}/{stats['links_gathered']:,}  ",
                        style=palette["secondary"])
            line.append(f"output {Path(stats['output_dir']).name}", style=palette["muted"])
        self.query_one("#footerbar", Static).update(line)

    def _update_button_states(self, stats: dict) -> None:
        running = stats["phase"] not in {"done", "stopped", "error", "idle"}
        pause = self.query_one("#btn-pause", Button)
        resume = self.query_one("#btn-resume-run", Button)
        stop = self.query_one("#btn-stop", Button)
        pause.disabled = not running or stats["paused"]
        resume.disabled = not running or not stats["paused"]
        stop.disabled = not running
        pause.label = "Paused" if stats["paused"] else "Pause"

    def _pulse(self) -> None:
        if not self._ui_ready():
            return
        self._pulse_on = not self._pulse_on
        self._spinner_index += 1
        self._update_topbar()

    def _slow_tick(self) -> None:
        if not self._ui_ready():
            return
        if self._view == "links":
            self._refresh_hosts()
        if self._view == "dash":
            self._refresh_recent()

    def _refresh_recent(self) -> None:
        if self.engine is None:
            return
        links = self.engine.recent_links(8)
        errors = self.engine.recent_errors(6)
        signature = tuple((item["url"], item["status"]) for item in links)
        if signature != self._recent_signature:
            self._recent_signature = signature
            table = self.query_one("#recent-table", DataTable)
            table.clear()
            for index, item in enumerate(links):
                table.add_row(
                    Text(item["host"], style=self._palette["secondary"]),
                    Text(item["filename"][:40], style=self._palette["text"]),
                    self._status_badge(item["status"], item.get("status")),
                    key=f"recent-{index}-{item['url'][:40]}",
                )
        error_signature = tuple((item["time"], item["message"]) for item in errors)
        if error_signature != self._issues_signature:
            self._issues_signature = error_signature
            body = Text()
            if not errors:
                body.append("No issues yet. Live log is available in the Logs view.",
                            style=self._palette["muted"])
            for item in errors:
                color = self._palette["error"] if item["level"] == "error" else self._palette["warning"]
                body.append(f"{item['time']} ", style=self._palette["muted"])
                body.append(f"{item['level'].upper()} ", style=color)
                body.append(item["message"][:110] + "\n", style=self._palette["text"])
            self.query_one("#issues-body", Static).update(body)

    def _status_badge(self, status: str, fallback: str = "") -> Text:
        palette = self._palette
        color = palette.get(STATUS_STYLES.get(status, "muted"), palette["muted"])
        return Text(f" {status.upper()} ", style=f"bold {palette['bg']} on {color}")

    def _page_status(self, page: dict) -> Text:
        return self._status_badge(page["status"])

    def _link_status(self, link: dict) -> Text:
        return self._status_badge(link["status"])

    def _refresh_urls(self, force: bool = False) -> None:
        if self.engine is None:
            return
        now = time.monotonic()
        self._last_url_refresh = now
        status = str(self.query_one("#url-status", Select).value or "all")
        search = self.query_one("#url-search", Input).value
        offset = self._url_page * self._page_size
        rows, total = self.engine.page_rows(status, search, offset, self._page_size)
        self._url_rows_cache = rows
        table = self.query_one("#urls-table", DataTable)
        keys = [row["url"] for row in rows]
        if keys != self._url_table_keys or len(table.rows) != len(keys):
            table.clear()
            for index, row in enumerate(rows):
                table.add_row(
                    str(offset + index + 1),
                    Text(row["url"], style=self._palette["secondary"], no_wrap=True, overflow="ellipsis"),
                    row["source"],
                    Text("✓", style=self._palette["success"]),
                    self._page_status(row),
                    str(row.get("http") or "-"),
                    str(row.get("links_found", 0)),
                    f"{row.get('elapsed_ms') or '-'}ms",
                    key=row["url"],
                )
            self._url_table_keys = keys
        else:
            for row in rows:
                try:
                    table.update_cell(row["url"], "status", self._page_status(row))
                    table.update_cell(row["url"], "http", str(row.get("http") or "-"))
                    table.update_cell(row["url"], "links", str(row.get("links_found", 0)))
                except Exception:
                    pass
        pages = max(1, math.ceil(total / self._page_size))
        if self._url_page >= pages:
            self._url_page = max(0, pages - 1)
        self.query_one("#url-page-label", Static).update(
            f"Page {self._url_page + 1:,} / {pages:,}  ·  {total:,} pages"
        )
        if self._selected_page_url:
            page = next((item for item in rows if item["url"] == self._selected_page_url), None)
            if page:
                self._update_page_detail(page)

    def _refresh_links(self, force: bool = False) -> None:
        if self.engine is None:
            return
        self._last_link_refresh = time.monotonic()
        status = str(self.query_one("#link-status", Select).value or "all")
        search = self.query_one("#link-search", Input).value
        offset = self._link_page * self._page_size
        rows, total = self.engine.link_rows(
            status, search, self._host_filter, offset, self._page_size,
        )
        self._link_rows_cache = rows
        table = self.query_one("#links-table", DataTable)
        keys = [row["url"] for row in rows]
        if keys != self._link_table_keys or len(table.rows) != len(keys):
            table.clear()
            for index, row in enumerate(rows):
                table.add_row(
                    str(offset + index + 1),
                    Text(row["host"], style=self._palette["secondary"], no_wrap=True, overflow="ellipsis"),
                    Text(row["filename"], style=self._palette["text"], no_wrap=True, overflow="ellipsis"),
                    self._link_status(row),
                    str(row.get("http") or "-"),
                    self._size_text(row),
                    Text(row.get("final_host") or "-", style=self._palette["muted"]),
                    key=row["url"],
                )
            self._link_table_keys = keys
        else:
            for row in rows:
                try:
                    table.update_cell(row["url"], "status", self._link_status(row))
                    table.update_cell(row["url"], "http", str(row.get("http") or "-"))
                    table.update_cell(row["url"], "size", self._size_text(row))
                    table.update_cell(row["url"], "final", Text(row.get("final_host") or "-",
                                                                style=self._palette["muted"]))
                except Exception:
                    pass
        pages = max(1, math.ceil(total / self._page_size))
        if self._link_page >= pages:
            self._link_page = max(0, pages - 1)
        self.query_one("#link-page-label", Static).update(
            f"Page {self._link_page + 1:,} / {pages:,}  ·  {total:,} links"
        )
        if self._selected_link_url:
            link = next((item for item in rows if item["url"] == self._selected_link_url), None)
            if link:
                self._update_link_detail(link)

    @staticmethod
    def _size_text(row: dict) -> str:
        size = row.get("size")
        if not size:
            return "-"
        try:
            value = float(size)
        except (TypeError, ValueError):
            return "-"
        return f"{value / 1024 / 1024:.1f} MB" if value > 1024 * 1024 else f"{value / 1024:.1f} KB"

    def _update_page_detail(self, page: dict) -> None:
        palette = self._palette
        body = Text()
        body.append("PAGE DETAIL\n", style=f"bold {palette['primary']}")
        body.append("URL       ", style=palette["muted"])
        body.append(page["url"] + "\n", style=palette["text"])
        body.append("Status    ", style=palette["muted"])
        body.append(page["status"].upper() + "\n", style=palette["secondary"])
        body.append("Source    ", style=palette["muted"])
        body.append(page.get("source", "") + "\n", style=palette["text"])
        body.append("HTTP      ", style=palette["muted"])
        body.append(f"{page.get('http') or '-'}\n", style=palette["text"])
        body.append("Links     ", style=palette["muted"])
        body.append(f"{page.get('links_found', 0):,}\n", style=palette["text"])
        body.append("Elapsed   ", style=palette["muted"])
        body.append(f"{page.get('elapsed_ms') or '-'} ms\n", style=palette["text"])
        if page.get("error"):
            body.append("Error     ", style=palette["muted"])
            body.append(page["error"] + "\n", style=palette["error"])
        matches = self.engine.links_for_page(page["url"], limit=6) if self.engine else []
        if matches:
            body.append("\nMATCHES ON PAGE\n", style=f"bold {palette['primary']}")
            for match in matches:
                body.append("• ", style=palette["success"])
                body.append(match["filename"][:70] + "\n", style=palette["text"])
        self.query_one("#url-detail-body", Static).update(body)
        for button_id in ("#url-copy", "#url-open", "#url-rescan"):
            self.query_one(button_id, Button).disabled = False

    def _update_link_detail(self, link: dict) -> None:
        palette = self._palette
        body = Text()
        body.append("LINK DETAIL\n", style=f"bold {palette['primary']}")
        body.append("URL       ", style=palette["muted"])
        body.append(link["url"] + "\n", style=palette["text"])
        body.append("Host      ", style=palette["muted"])
        body.append(link["host"] + "\n", style=palette["secondary"])
        body.append("File      ", style=palette["muted"])
        body.append(link["filename"] + "\n", style=palette["text"])
        body.append("Status    ", style=palette["muted"])
        body.append(f"{link['status'].upper()}  HTTP {link.get('http') or '-'}\n",
                    style=palette["secondary"])
        if link.get("final_url"):
            body.append("Final     ", style=palette["muted"])
            body.append(link["final_url"] + "\n", style=palette["text"])
        body.append("Type      ", style=palette["muted"])
        body.append((link.get("content_type") or "-") + "\n", style=palette["text"])
        body.append("Size      ", style=palette["muted"])
        body.append(self._size_text(link) + "\n", style=palette["text"])
        body.append("TLS       ", style=palette["muted"])
        ssl_value = link.get("ssl_verified")
        body.append(("verified" if ssl_value else "not verified" if ssl_value is False else "-") + "\n",
                    style=palette["success"] if ssl_value else palette["warning"])
        body.append("Source    ", style=palette["muted"])
        body.append(link.get("source_page", "") + "\n", style=palette["text"])
        if link.get("error"):
            body.append("Error     ", style=palette["muted"])
            body.append(link["error"] + "\n", style=palette["error"])
        self.query_one("#link-detail-body", Static).update(body)
        for button_id in ("#link-copy", "#link-open", "#link-verify", "#link-source"):
            self.query_one(button_id, Button).disabled = False

    def _refresh_hosts(self) -> None:
        if self.engine is None or self._view != "links":
            return
        hosts = self.engine.top_hosts(6)
        signature = tuple((item["host"], item["count"]) for item in hosts)
        if signature == self._host_signature:
            return
        self._host_signature = signature
        container = self.query_one("#host-chips", Horizontal)
        container.remove_children()
        for index, item in enumerate(hosts):
            classes = "chip host-chip"
            if self._host_filter and item["host"] == self._host_filter:
                classes += " active"
            container.mount(Button(
                f"{item['count']} · {item['host'][:24]}",
                id=f"host-chip-{index}", classes=classes,
            ))

    def _log_matches(self, entry: dict) -> bool:
        level = str(self.query_one("#log-level", Select).value or "all")
        ranks = {"debug": 10, "info": 20, "warn": 30, "error": 40}
        if level != "all" and ranks.get(entry["level"], 0) < ranks.get(level, 0):
            return False
        search = self.query_one("#log-search", Input).value.lower().strip()
        if search and search not in entry["message"].lower():
            return False
        return True

    def _write_log(self, entry: dict) -> None:
        palette = self._palette
        colors = {
            "debug": palette["muted"], "info": palette["secondary"],
            "warn": palette["warning"], "error": palette["error"],
        }
        line = Text()
        line.append(f"{entry['time']} ", style=palette["muted"])
        line.append(f"{entry['level'].upper():<5} ", style=f"bold {colors.get(entry['level'], palette['text'])}")
        line.append(entry["message"], style=palette["text"])
        log = self.query_one("#logs", RichLog)
        log.write(line)

    def _rebuild_logs(self) -> None:
        log = self.query_one("#logs", RichLog)
        log.clear()
        for entry in list(self._log_cache)[-2000:]:
            if self._log_matches(entry):
                self._write_log(entry)

    @on(Button.Pressed)
    def _button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id.startswith("nav-"):
            self._switch_view(button_id[4:])
        elif button_id == "btn-start":
            self.start_crawl()
        elif button_id == "btn-resume":
            self.resume_last()
        elif button_id == "btn-demo":
            self.start_demo()
        elif button_id == "btn-pause":
            if self.engine:
                self.engine.pause()
        elif button_id == "btn-resume-run":
            if self.engine:
                self.engine.resume()
        elif button_id == "btn-stop":
            if self.engine:
                self.engine.stop()
                self.notify("Stop requested. Current requests are finishing.", severity="warning")
        elif button_id in {"btn-export", "btn-export-links"}:
            self.action_export_csv()
        elif button_id == "btn-open-output":
            if self.engine:
                os.startfile(self.engine.output_dir)
        elif button_id == "url-prev":
            self._url_page = max(0, self._url_page - 1)
            self._refresh_urls(force=True)
        elif button_id == "url-next":
            self._url_page += 1
            self._refresh_urls(force=True)
        elif button_id == "link-prev":
            self._link_page = max(0, self._link_page - 1)
            self._refresh_links(force=True)
        elif button_id == "link-next":
            self._link_page += 1
            self._refresh_links(force=True)
        elif button_id == "url-copy" and self._selected_page_url:
            self._copy(self._selected_page_url)
        elif button_id == "url-open" and self._selected_page_url:
            webbrowser.open(self._selected_page_url)
        elif button_id == "url-rescan" and self._selected_page_url and self.engine:
            self.engine.scan_page_now(self._selected_page_url)
            self.notify("Page re-queued for scanning", severity="information")
        elif button_id == "link-copy" and self._selected_link_url:
            self._copy(self._selected_link_url)
        elif button_id == "link-open" and self._selected_link_url:
            webbrowser.open(self._selected_link_url)
        elif button_id == "link-verify" and self._selected_link_url and self.engine:
            self.engine.verify_links([self._selected_link_url])
            self.notify("Link queued for verification", severity="information")
        elif button_id == "link-source" and self._selected_link_url and self.engine:
            link = self.engine.get_link(self._selected_link_url)
            if link and link.get("source_page"):
                webbrowser.open(link["source_page"])
        elif button_id == "btn-verify-filtered":
            self._verify_filtered(None)
        elif button_id == "btn-verify-failed":
            self._verify_filtered("failed")
        elif button_id == "btn-copy-filtered":
            self._copy_filtered()
        elif button_id == "host-clear":
            self._host_filter = ""
            self.query_one("#link-search", Input).value = ""
            self._link_page = 0
            self._refresh_links(force=True)
        elif button_id.startswith("host-chip-"):
            self._apply_host_chip(button_id)
        elif button_id == "log-clear":
            self.query_one("#logs", RichLog).clear()
        elif button_id == "log-open" and self.engine:
            os.startfile(Path(self.engine.output_paths()["log"]).parent)
        elif button_id == "btn-save-settings":
            self._save_settings()
        elif button_id == "btn-help":
            self.push_screen(HelpScreen())
        elif button_id == "help-close":
            self.pop_screen()

    def _apply_host_chip(self, button_id: str) -> None:
        if self.engine is None:
            return
        try:
            index = int(button_id.rsplit("-", 1)[1])
            hosts = self.engine.top_hosts(6)
            host = hosts[index]["host"]
        except (ValueError, IndexError):
            return
        self._host_filter = host
        self.query_one("#link-search", Input).value = ""
        self._link_page = 0
        self._switch_view("links")
        self._refresh_links(force=True)
        self.notify(f"Filtering host {host}", severity="information")

    def _verify_filtered(self, status_override: str | None) -> None:
        if self.engine is None:
            return
        status = status_override or str(self.query_one("#link-status", Select).value or "all")
        search = self.query_one("#link-search", Input).value
        rows, total = self.engine.link_rows(status, search, self._host_filter, 0, 100000)
        urls = [row["url"] for row in rows]
        if not urls:
            self.notify("No links match the current filter", severity="warning")
            return
        count = self.engine.verify_links(urls)
        self.notify(f"Queued {count:,} link(s) for verification", severity="information")

    def _copy_filtered(self) -> None:
        if self.engine is None:
            return
        status = str(self.query_one("#link-status", Select).value or "all")
        search = self.query_one("#link-search", Input).value
        rows, _ = self.engine.link_rows(status, search, self._host_filter, 0, 100000)
        if not rows:
            self.notify("No links match the current filter", severity="warning")
            return
        self._copy("\n".join(row["url"] for row in rows))
        self.notify(f"Copied {len(rows):,} link(s) to the clipboard", severity="information")

    def _copy(self, value: str) -> None:
        try:
            self.copy_to_clipboard(value)
            self.notify("Copied to clipboard", severity="information")
        except Exception:
            try:
                import subprocess
                subprocess.run("clip", input=value.encode("utf-16le"), check=False)
                self.notify("Copied to clipboard", severity="information")
            except Exception:
                self.notify("Could not access the clipboard", severity="error")

    def _int_field(self, selector: str, default: int, minimum: int = 0) -> int:
        try:
            value = int(self.query_one(selector, Input).value or default)
        except ValueError:
            value = default
        return max(minimum, value)

    def _float_field(self, selector: str, default: float, minimum: float = 0.0) -> float:
        try:
            value = float(self.query_one(selector, Input).value or default)
        except ValueError:
            value = default
        return max(minimum, value)

    def _save_settings(self) -> None:
        page_size = min(500, self._int_field("#inp-page-size", 50, minimum=10))
        self._page_size = page_size
        theme_name = str(self.query_one("#sel-theme", Select).value or "neon-lime")
        animations = str(self.query_one("#sel-anim", Select).value or "full")
        self._theme_name = theme_name
        self._animations = animations
        self._palette = PALETTES.get(theme_name, PALETTES["neon-lime"])
        self._apply_theme(theme_name)
        base = self.initial_config
        config = Config(
            start_url=self.query_one("#inp-url", Input).value.strip() or base.start_url,
            patterns=[part.strip() for part in self.query_one("#inp-patterns", Input).value.split(",") if part.strip()] or base.patterns,
            max_pages=self._int_field("#inp-max-pages", 0),
            max_verify=self._int_field("#inp-max-verify", 0),
            verify_mode=str(self.query_one("#sel-verify-mode", Select).value or "all"),
            crawl_workers=self._int_field("#inp-workers", 8, minimum=1),
            crawl_delay=self._float_field("#inp-delay", 0.2),
            use_sitemap=bool(self.query_one("#sw-sitemap", Switch).value),
            respect_robots=bool(self.query_one("#sw-robots", Switch).value),
            extra_seeds=[part.strip() for part in self.query_one("#inp-seeds", Input).value.split(",") if part.strip()],
            user_agent=self.query_one("#inp-ua", Input).value.strip() or base.user_agent,
            cookie=self.query_one("#inp-cookie", Input).value.strip(),
            proxy=self.query_one("#inp-proxy", Input).value.strip(),
            per_host_limit=self._int_field("#inp-per-host", 1, minimum=1),
            timeout=self._float_field("#inp-timeout", 20.0, minimum=1.0),
            retries=self._int_field("#inp-retries", 2),
        )
        payload = config.to_dict()
        payload["ui"] = {
            "theme": theme_name,
            "animations": animations,
            "page_size": page_size,
        }
        CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        self.initial_config = config
        self.query_one("#settings-hint", Static).update(
            f"Saved to {CONFIG_PATH}  |  theme {theme_name}  |  animations {animations}  |  "
            f"{page_size} rows/page"
        )
        self._refresh_urls(force=True)
        self._refresh_links(force=True)
        self._update_topbar()
        self.notify("Settings saved", severity="information")

    @on(DataTable.RowSelected, "#urls-table")
    def _url_row_selected(self, event: DataTable.RowSelected) -> None:
        self._selected_page_url = str(event.row_key.value)
        page = next((item for item in self._url_rows_cache
                     if item["url"] == self._selected_page_url), None)
        if page is None and self.engine:
            rows, _ = self.engine.page_rows("all", self._selected_page_url, 0, 1)
            page = rows[0] if rows else None
        if page:
            self._update_page_detail(page)

    @on(DataTable.RowSelected, "#links-table")
    def _link_row_selected(self, event: DataTable.RowSelected) -> None:
        self._selected_link_url = str(event.row_key.value)
        link = next((item for item in self._link_rows_cache
                     if item["url"] == self._selected_link_url), None)
        if link is None and self.engine:
            link = self.engine.get_link(self._selected_link_url)
        if link:
            self._update_link_detail(link)

    @on(Input.Changed, "#url-search")
    def _url_search_changed(self) -> None:
        self._url_page = 0
        self._refresh_urls(force=True)

    @on(Select.Changed, "#url-status")
    def _url_status_changed(self) -> None:
        self._url_page = 0
        self._refresh_urls(force=True)

    @on(Input.Changed, "#link-search")
    def _link_search_changed(self) -> None:
        self._link_page = 0
        self._refresh_links(force=True)

    @on(Select.Changed, "#link-status")
    def _link_status_changed(self) -> None:
        self._link_page = 0
        self._refresh_links(force=True)

    @on(Select.Changed, "#log-level")
    def _log_level_changed(self) -> None:
        self._rebuild_logs()

    @on(Input.Changed, "#log-search")
    def _log_search_changed(self) -> None:
        self._rebuild_logs()

    @on(Checkbox.Changed, "#log-autoscroll")
    def _log_autoscroll_changed(self, event: Checkbox.Changed) -> None:
        log = self.query_one("#logs", RichLog)
        log.auto_scroll = bool(event.value)

    @on(StatCard.Clicked)
    def _card_clicked(self, event: StatCard.Clicked) -> None:
        card = next((item for item in CARD_CONFIG if item[0] == event.card_key), None)
        if card is None:
            return
        _, _, _, filter_value, target_view = card
        self._switch_view(target_view, filter_value=filter_value)
