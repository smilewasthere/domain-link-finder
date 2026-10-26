from __future__ import annotations

import asyncio

from textual.widgets import DataTable, Digits, Input, Static

from dlf.engine import Config, Engine
from dlf.tui import DLFApp, HelpScreen


def test_tui_views_and_demo_run(tmp_path) -> None:
    async def scenario() -> None:
        app = DLFApp(initial_config=Config())
        async with app.run_test(size=(150, 48)) as pilot:
            await pilot.pause(0.5)
            assert "DOMAIN" in str(app.query_one("#brand", Static).content)

            for key in ("f2", "f3", "f4", "f5", "f6", "f1"):
                await pilot.press(key)
                await pilot.pause(0.25)

            app.engine = Engine(Config(
                start_url="https://demo.local/",
                patterns=["*.abrtech.top"],
                demo=True,
                demo_pages=12,
                demo_links_per_page=4,
                demo_delay=0,
                output_dir=str(tmp_path / "tui_run"),
            ))
            app._begin_engine("test run")
            await pilot.pause(1.5)
            stats = app.engine.stats()
            assert stats["phase"] == "done"
            assert stats["links_gathered"] == 48

            await pilot.press("f2")
            await pilot.pause(1.4)
            assert app.query_one("#digits-pages", Digits).value == "12"

            await pilot.press("f4")
            await pilot.pause(1.4)
            table = app.query_one("#links-table", DataTable)
            assert table.row_count == 48
            assert app.query_one("#host-chips").children

            table.focus()
            table.move_cursor(row=0)
            await pilot.press("enter")
            await pilot.pause(0.3)
            detail = app.query_one("#link-detail-body", Static).content
            assert "LINK DETAIL" in str(detail)

            await pilot.press("f5")
            await pilot.pause(0.3)
            assert app.query_one("#logs").lines

            await pilot.press("f6")
            await pilot.pause(0.2)
            await pilot.click("#btn-help")
            await pilot.pause(0.3)
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("escape")
            await pilot.pause(0.2)

            app.engine.close()

    asyncio.run(scenario())

def test_tui_settings_persist(tmp_path, monkeypatch) -> None:
    import json
    from textual.widgets import Input, Select

    import dlf.tui as tui

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(tui, "CONFIG_PATH", config_path)

    async def scenario() -> None:
        app = DLFApp(initial_config=Config())
        async with app.run_test(size=(150, 48)) as pilot:
            await pilot.pause(0.5)
            await pilot.press("f6")
            await pilot.pause(0.3)
            app.query_one("#sel-theme", Select).value = "cyber-blue"
            app.query_one("#sel-anim", Select).value = "basic"
            app.query_one("#inp-page-size", Input).value = "25"
            await pilot.pause(0.2)
            await pilot.click("#btn-save-settings")
            await pilot.pause(0.4)

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["ui"] == {"theme": "cyber-blue", "animations": "basic", "page_size": 25}
        assert tui.load_ui_settings() == data["ui"]

        loaded = DLFApp(initial_config=Config(), ui_settings=tui.load_ui_settings())
        async with loaded.run_test(size=(150, 48)) as pilot:
            await pilot.pause(0.4)
            assert loaded._theme_name == "cyber-blue"
            assert loaded._animations == "basic"
            assert loaded._page_size == 25
            assert loaded.query_one("#sel-theme", Select).value == "cyber-blue"
            assert loaded.query_one("#inp-page-size", Input).value == "25"

    asyncio.run(scenario())

def test_tui_pattern_chips_and_extension_modal(tmp_path, monkeypatch) -> None:
    import json
    from textual.widgets import Input, Select

    import dlf.tui as tui

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(tui, "CONFIG_PATH", config_path)

    async def scenario() -> None:
        app = DLFApp(initial_config=Config(patterns=["*.one.example"]))
        async with app.run_test(size=(150, 48)) as pilot:
            await pilot.pause(0.4)
            editor = app.query_one("#patterns-editor", tui.PatternChipEditor)
            assert editor.patterns() == ["*.one.example"]

            pattern_input = app.query_one("#pattern-input", Input)
            pattern_input.focus()
            pattern_input.value = "*.two.example"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert editor.patterns() == ["*.one.example", "*.two.example"]

            await pilot.click("#btn-extensions")
            await pilot.pause(0.3)
            assert isinstance(app.screen, tui.ExtensionScreen)
            app.screen.query_one("#ext-custom", Input).value = "zip, .MKV"
            await pilot.click("#ext-apply")
            await pilot.pause(0.4)
            assert app._extensions == ["zip", "mkv"]
            assert "2 types" in str(app.query_one("#btn-extensions").label)

            app.query_one("#sel-match-mode", Select).value = "extensions"
            await pilot.pause(0.2)
            await pilot.press("f6")
            await pilot.pause(0.3)
            await pilot.click("#btn-save-settings")
            await pilot.pause(0.4)

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["extensions"] == ["zip", "mkv"]
        assert data["match_mode"] == "extensions"

    asyncio.run(scenario())


def test_extension_modal_checkboxes_are_independent() -> None:
    async def scenario() -> None:
        app = DLFApp(initial_config=Config(extensions=["mkv", "zip"]))
        async with app.run_test(size=(150, 48)) as pilot:
            await pilot.pause(0.4)
            await pilot.click("#btn-extensions")
            await pilot.pause(0.3)
            screen = app.screen
            assert screen._checks["mkv"].value
            assert screen._checks["zip"].value
            assert screen.query_one("#ext-custom", Input).value == ""
            screen._checks["mkv"].value = False
            await pilot.click("#ext-apply")
            await pilot.pause(0.3)
            assert app._extensions == ["zip"]

    asyncio.run(scenario())


def test_tui_live_view_and_f7(tmp_path) -> None:
    async def scenario() -> None:
        app = DLFApp(initial_config=Config())
        async with app.run_test(size=(150, 48)) as pilot:
            await pilot.pause(0.4)
            app.engine = Engine(Config(
                start_url="https://demo.local/",
                patterns=["*.abrtech.top"],
                demo=True,
                demo_pages=6,
                demo_links_per_page=4,
                demo_delay=0,
                output_dir=str(tmp_path / "live_run"),
            ))
            app._begin_engine("live test")
            await pilot.pause(1.2)
            await pilot.press("f7")
            await pilot.pause(0.8)
            assert app._view == "live"
            feed = app.query_one("#live-feed")
            assert len(feed.children) > 0
            assert "LIVE" in str(app.query_one("#live-count").content)
            app.engine.close()

    asyncio.run(scenario())
