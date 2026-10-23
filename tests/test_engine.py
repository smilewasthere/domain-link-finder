from __future__ import annotations

import csv
import json

from dlf.engine import (
    Config,
    Engine,
    compile_patterns,
    extract_urls,
    match_pattern,
    parse_sitemap_xml,
)


def test_pattern_matching() -> None:
    patterns = compile_patterns(["*.abrtech.top", "https://cdn.example.com/files/*", "re:film-\\d+"])
    assert match_pattern("https://abc.abrtech.top/movie.mkv", patterns) == "*.abrtech.top"
    assert match_pattern("https://CDN.ABRTECH.TOP/movie.mkv", patterns) == "*.abrtech.top"
    assert match_pattern("https://cdn.example.com/files/a.mkv", patterns).startswith("https://cdn")
    assert match_pattern("https://other.example.com/film-12.mkv", patterns) == "re:film-\\d+"
    assert match_pattern("https://other.example.com/nope.mkv", patterns) == ""


def test_parse_sitemap_index_and_urlset() -> None:
    index = b"""<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.com/post-sitemap1.xml</loc></sitemap>
      <sitemap><loc>https://example.com/post-sitemap2.xml</loc></sitemap>
    </sitemapindex>"""
    kind, locations = parse_sitemap_xml(index)
    assert kind == "index"
    assert locations == [
        "https://example.com/post-sitemap1.xml",
        "https://example.com/post-sitemap2.xml",
    ]

    urlset = b"""<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/a/</loc></url>
      <url><loc>https://example.com/b/</loc></url>
    </urlset>"""
    kind, locations = parse_sitemap_xml(urlset)
    assert kind == "urlset"
    assert locations == ["https://example.com/a/", "https://example.com/b/"]

    assert parse_sitemap_xml(b"not xml") == ("", [])


def test_extract_urls_deduplicates_and_resolves_relative() -> None:
    html = """
    <html><body>
      <a href="/post/one/">One</a>
      <a href="https://abc.abrtech.top/file.mkv">Download</a>
      <video src="https://abc.abrtech.top/file.mkv"></video>
      <a href="https://abc.abrtech.top/file.mkv">Duplicate</a>
      <a href="#fragment">Skip me</a>
      <a href="mailto:test@example.com">Mail</a>
    </body></html>
    """
    links = extract_urls(html, "https://www.example.com/page/")
    urls = [item.url for item in links]
    assert "https://www.example.com/post/one/" in urls
    assert "https://abc.abrtech.top/file.mkv" in urls
    assert urls.count("https://abc.abrtech.top/file.mkv") == 1
    assert not any("fragment" in url or url.startswith("mailto:") for url in urls)


def test_demo_run_persists_and_exports(tmp_path) -> None:
    config = Config(
        start_url="https://demo.local/",
        patterns=["*.abrtech.top"],
        demo=True,
        demo_pages=10,
        demo_links_per_page=5,
        demo_delay=0,
        output_dir=str(tmp_path / "run_demo"),
    )
    engine = Engine(config)
    engine.start()
    engine.wait(15)
    stats = engine.stats()

    assert stats["phase"] == "done"
    assert stats["pages_scanned"] == 10
    assert stats["links_gathered"] == 50
    assert stats["links_verified"] == 50
    assert stats["hosts"] == 10
    assert (engine.output_dir / "state.json").exists()
    assert (engine.output_dir / "pages.jsonl").exists()
    assert (engine.output_dir / "links.jsonl").exists()
    assert (engine.output_dir / "run.log").exists()

    result = engine.export_csv()
    assert result["link_count"] == 50
    assert result["page_count"] == 10
    with (engine.output_dir / "links.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 50
    assert {row["status"] for row in rows} <= {"ok", "blocked", "missing", "error"}
    engine.close()


def test_resume_loads_previous_run(tmp_path) -> None:
    output = tmp_path / "run_resume"
    config = Config(
        start_url="https://demo.local/",
        patterns=["*.abrtech.top"],
        demo=True,
        demo_pages=6,
        demo_links_per_page=3,
        demo_delay=0,
        output_dir=str(output),
    )
    engine = Engine(config)
    engine.start()
    engine.wait(15)
    engine.close()

    resumed = Engine.from_resume(output)
    stats = resumed.stats()
    assert stats["pages_gathered"] == 6
    assert stats["links_gathered"] == 18
    assert stats["links_verified"] == 18
    assert stats["phase"] == "idle"
    assert resumed.config.demo is True
    assert resumed.links
    assert resumed.page_rows("all", "", 0, 100)[1] == 6
    assert resumed.link_rows("all", "", "", 0, 100)[1] == 18

    state = json.loads((output / "state.json").read_text(encoding="utf-8"))
    assert state["stats"]["links"] == 18
    resumed.close()


def test_max_verify_marks_extra_links_unverified(tmp_path) -> None:
    config = Config(
        start_url="https://demo.local/",
        patterns=["*.abrtech.top"],
        demo=True,
        demo_pages=4,
        demo_links_per_page=5,
        demo_delay=0,
        output_dir=str(tmp_path / "run_limit"),
    )
    engine = Engine(config)
    links = []
    for index in range(8):
        url = f"https://host{index}.abrtech.top/file.mkv"
        links.append(engine._upsert_link(url, "https://demo.local/p/", "Download", "*.abrtech.top", "a", "href"))
    engine.config.max_verify = 3
    for link in links:
        engine._schedule_verify(link)

    statuses = [link["status"] for link in links]
    assert statuses.count("queued") == 3
    assert statuses.count("unverified") == 5
    engine.close()


def test_find_latest_run(tmp_path) -> None:
    assert Engine.find_latest_run(tmp_path) is None
    older = tmp_path / "run_20260101_000000"
    newer = tmp_path / "run_20260102_000000"
    older.mkdir()
    newer.mkdir()
    (tmp_path / "not_a_run").mkdir()
    import os

    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_800_000_000, 1_800_000_000))
    latest = Engine.find_latest_run(tmp_path)
    assert latest is not None
    assert latest.name == "run_20260102_000000"
