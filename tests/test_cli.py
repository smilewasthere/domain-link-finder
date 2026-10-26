from __future__ import annotations

import pytest

from dlf.engine import Config
from run import apply_overrides, build_parser


def test_cli_extension_overrides() -> None:
    args = build_parser().parse_args(["-e", ".MKV", "-e", "zip,rar", "--match-mode", "extensions"])
    config = apply_overrides(Config(), args)
    assert config.extensions == ["mkv", "zip", "rar"]
    assert config.match_mode == "extensions"


def test_cli_rejects_unknown_match_mode() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--match-mode", "nope"])
