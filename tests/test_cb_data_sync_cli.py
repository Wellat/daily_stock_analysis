# -*- coding: utf-8 -*-
"""Convertible-bond data sync CLI tests."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "sync_cb_data.py"


@pytest.fixture(scope="module")
def cli_module():
    spec = importlib.util.spec_from_file_location("sync_cb_data_cli", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_parser_defaults_to_basic(cli_module) -> None:
    args = cli_module.build_parser().parse_args([])
    assert args.basic is False
    assert args.ohlc is False
    assert args.all is False
    assert args.include_delisted is False
    assert args.start_date is None
    # 不带任何模式参数时，默认仅同步基础数据
    assert args.basic or (not args.ohlc and not args.all)


def test_parser_ohlc_options(cli_module) -> None:
    args = cli_module.build_parser().parse_args(
        [
            "--ohlc",
            "--include-delisted",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-01-31",
            "--bond",
            "113709",
        ]
    )
    assert args.ohlc is True
    assert args.include_delisted is True
    assert args.start_date == date(2026, 1, 1)
    assert args.end_date == date(2026, 1, 31)
    assert args.bond == "113709"


def test_parser_symbols(cli_module) -> None:
    args = cli_module.build_parser().parse_args(["--symbols", "113709,123001"])
    assert cli_module._parse_symbols(args.symbols) == ["113709", "123001"]


def test_parser_rejects_invalid_date(cli_module) -> None:
    with pytest.raises(SystemExit):
        cli_module.build_parser().parse_args(["--start-date", "2026/01/01"])


def test_dry_run_prints_mapping(cli_module, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    class _FakeProvider:
        def __init__(self, **kwargs):
            pass

        def fetch_list(self, *, include_delisted=False):
            return [{"bondId": "113709", "bondName": "振26转债", "status": "active"}]

        def normalize_list_row(self, record):
            return {
                "bond_code": "113709",
                "bond_name": "振26转债",
                "market": "cn",
                "status": "正常",
                "terms": {"provider": "opencli"},
            }

        def fetch_detail(self, code):
            return {"bond_code": code, "delisted": "false"}

        def normalize_detail(self, record):
            return {
                "basic": {"bond_code": record["bond_code"]},
                "meta": {},
                "status": "正常",
                "terms": {"bond_code": record["bond_code"]},
                "events": [],
            }

    monkeypatch.setattr(cli_module, "OpencliConvertibleBondProvider", _FakeProvider)
    cli_module.dry_run(cli_module.build_parser().parse_args(["--basic", "--dry-run"]))
    out = capsys.readouterr().out
    assert "113709" in out
    assert "cb-detail" in out
