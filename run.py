from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from rich.console import Console

from dlf.engine import Config, Engine

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
CONSOLE = Console(highlight=False)


def load_config() -> Config:
    if CONFIG_PATH.exists():
        try:
            return Config.from_dict(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return Config()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="domain-link-finder",
        description="Crawl a site for URLs matching host patterns and verify them.",
    )
    parser.add_argument("--cli", action="store_true", help="Run headless in the terminal")
    parser.add_argument("-u", "--url", help="Site URL to crawl")
    parser.add_argument("-m", "--match", action="append", metavar="PATTERN",
                        help="Host or URL glob, or re:REGEX (repeatable)")
    parser.add_argument("--seed", action="append", metavar="URL",
                        help="Extra page to scan (repeatable)")
    parser.add_argument("--max-pages", type=int, help="Maximum pages to scan (0 = all)")
    parser.add_argument("--max-verify", type=int, help="Maximum links to verify (0 = all)")
    parser.add_argument("--verify", choices=("all", "hosts", "none"), dest="verify_mode",
                        help="Verification strategy")
    parser.add_argument("--workers", type=int, help="Crawl worker threads")
    parser.add_argument("--verify-workers", type=int, help="Verification worker threads")
    parser.add_argument("--delay", type=float, help="Seconds between page requests")
    parser.add_argument("--timeout", type=float, help="Per-request timeout in seconds")
    parser.add_argument("--retries", type=int, help="Request retries")
    parser.add_argument("--per-host", type=int, help="Concurrent verifications per host")
    parser.add_argument("--no-sitemap", action="store_true", help="Skip sitemap discovery")
    parser.add_argument("--ignore-robots", action="store_true", help="Do not consult robots.txt")
    parser.add_argument("--output", metavar="DIR", help="Output directory (default: output/run_<time>)")
    parser.add_argument("--resume", nargs="?", const="latest", metavar="DIR",
                        help="Resume a run directory, or the latest run")
    parser.add_argument("--demo", action="store_true", help="Use synthetic demo data")
    parser.add_argument("--demo-pages", type=int, default=600, help="Demo pages to generate")
    parser.add_argument("--demo-links", type=int, default=7, help="Demo links per page")
    parser.add_argument("--auto-start", action="store_true",
                        help="Start crawling as soon as the TUI opens")
    parser.add_argument("--list-runs", action="store_true", help="List saved runs and exit")
    parser.add_argument("--version", action="version", version="domain-link-finder 1.0")
    return parser


def apply_overrides(config: Config, args: argparse.Namespace) -> Config:
    if args.url:
        config.start_url = args.url
    if args.match:
        config.patterns = list(args.match)
    if args.seed:
        config.extra_seeds = list(dict.fromkeys([*config.extra_seeds, *args.seed]))
    if args.max_pages is not None:
        config.max_pages = max(0, args.max_pages)
    if args.max_verify is not None:
        config.max_verify = max(0, args.max_verify)
    if args.verify_mode:
        config.verify_mode = args.verify_mode
    if args.workers is not None:
        config.crawl_workers = max(1, args.workers)
    if args.verify_workers is not None:
        config.verify_workers = max(1, args.verify_workers)
    if args.delay is not None:
        config.crawl_delay = max(0.0, args.delay)
    if args.timeout is not None:
        config.timeout = max(1.0, args.timeout)
    if args.retries is not None:
        config.retries = max(0, args.retries)
    if args.per_host is not None:
        config.per_host_limit = max(1, args.per_host)
    if args.no_sitemap:
        config.use_sitemap = False
    if args.ignore_robots:
        config.respect_robots = False
    if args.output:
        config.output_dir = str(Path(args.output).expanduser().resolve())
    if args.demo:
        config.demo = True
        config.demo_pages = max(1, args.demo_pages)
        config.demo_links_per_page = max(1, args.demo_links)
    return config


def resume_path(value: str | None) -> Path | None:
    if not value:
        return None
    if value == "latest":
        latest = Engine.find_latest_run(PROJECT_ROOT / "output")
        if latest is None:
            raise SystemExit("No previous run found in output/")
        return latest
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise SystemExit(f"Run directory not found: {path}")
    return path


def config_for_resume(path: Path) -> Config:
    state_path = path / "state.json"
    config = load_config()
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            config = Config.from_dict(state.get("config", {}))
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    config.output_dir = str(path)
    return config


def list_runs() -> None:
    root = PROJECT_ROOT / "output"
    if not root.exists():
        CONSOLE.print("[yellow]No output directory yet.[/yellow]")
        return
    runs = sorted((path for path in root.glob("run_*") if path.is_dir()),
                  key=lambda path: path.stat().st_mtime, reverse=True)
    if not runs:
        CONSOLE.print("[yellow]No saved runs.[/yellow]")
        return
    for path in runs:
        stats: dict = {}
        try:
            state = json.loads((path / "state.json").read_text(encoding="utf-8"))
            stats = state.get("stats", {})
        except (OSError, json.JSONDecodeError):
            pass
        CONSOLE.print(
            f"[cyan]{path.name}[/cyan]  pages {stats.get('pages', 0):,}  "
            f"links {stats.get('links', 0):,}  {path}"
        )


def print_logs(engine: Engine, sequence: int) -> int:
    for entry in engine.logs_since(sequence):
        level = entry["level"]
        color = {"debug": "dim", "info": "cyan", "warn": "yellow", "error": "red"}.get(level, "white")
        CONSOLE.print(f"[dim]{entry['time']}[/dim] [{color}]{level.upper():<5}[/{color}] {entry['message']}",
                      soft_wrap=True)
        sequence = entry["seq"]
    return sequence


def run_cli(engine: Engine) -> int:
    engine.start()
    sequence = 0
    last_report = 0.0
    try:
        while True:
            sequence = print_logs(engine, sequence)
            stats = engine.stats()
            now = time.monotonic()
            if now - last_report >= 5.0 and stats["phase"] not in {"done", "stopped", "error"}:
                last_report = now
                CONSOLE.print(
                    f"[bold]pages[/bold] {stats['pages_scanned']:,}/{stats['pages_gathered']:,}  "
                    f"[bold]links[/bold] {stats['links_gathered']:,}  "
                    f"[bold]verified[/bold] {stats['links_verified']:,} "
                    f"({stats['links_ok']:,} ok / {stats['links_bad']:,} issues)  "
                    f"[bold]hosts[/bold] {stats['hosts']:,}"
                )
            if stats["phase"] in {"done", "stopped", "error"}:
                break
            time.sleep(0.3)
    except KeyboardInterrupt:
        CONSOLE.print("\n[yellow]Stopping…[/yellow]")
        engine.stop()
    engine.wait(60)
    sequence = print_logs(engine, sequence)
    stats = engine.stats()
    paths = engine.output_paths()
    CONSOLE.print()
    CONSOLE.print(f"[bold]Run {stats['phase']}[/bold]  elapsed {int(stats['elapsed'])}s")
    CONSOLE.print(
        f"Pages: {stats['pages_scanned']:,} scanned / {stats['pages_gathered']:,} gathered  |  "
        f"Links: {stats['links_gathered']:,} gathered  |  "
        f"Verified: {stats['links_verified']:,} ({stats['links_ok']:,} ok, {stats['links_bad']:,} issues)"
    )
    CONSOLE.print(f"Output: {paths['dir']}")
    CONSOLE.print(f"Links CSV: {paths['links']}")
    engine.close()
    return 0 if stats["phase"] != "error" else 1


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    args = build_parser().parse_args(argv)
    if args.list_runs:
        list_runs()
        return 0

    resume = resume_path(args.resume)
    config = config_for_resume(resume) if resume else load_config()
    config = apply_overrides(config, args)

    if args.cli:
        engine = Engine(config, resume_dir=resume) if resume else Engine(config)
        return run_cli(engine)

    from dlf.tui import DLFApp, load_ui_settings

    app = DLFApp(
        initial_config=config,
        resume_dir=resume,
        demo=args.demo,
        auto_start=args.auto_start,
        ui_settings=load_ui_settings(),
    )
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
