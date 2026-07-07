# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ExcelParser._should_use_process_pool, the env-gated routing decision
that decides whether Excel→Markdown conversion + layout planning run in a
ProcessPoolExecutor child process instead of the main process's event loop.

A real ProcessPoolExecutor is not exercised here: spawning a child process per
test is slow and orthogonal to this decision, and the worker itself
(_build_excel_layout_in_process) is a thin, side-effect-free function that only
touches its own arguments. This covers the routing decision that gates it.
"""

from pathlib import Path

import pytest

from openviking.parse.parsers.excel import ExcelParser


class TestShouldUseProcessPool:
    def _parser(self) -> ExcelParser:
        return ExcelParser()

    def _make_file(self, tmp_path: Path, suffix: str = ".xlsx", size: int = 10) -> Path:
        path = tmp_path / f"sheet{suffix}"
        path.write_bytes(b"x" * size)
        return path

    def test_disabled_by_default(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("OPENVIKING_EXCEL_PARSE_PROCESS_POOL", raising=False)
        path = self._make_file(tmp_path)
        assert self._parser()._should_use_process_pool(path, {}) is False

    def test_xls_never_uses_process_pool(self, tmp_path: Path, monkeypatch):
        # Legacy .xls goes through xlrd, not the openpyxl path the worker assumes.
        monkeypatch.setenv("OPENVIKING_EXCEL_PARSE_PROCESS_POOL", "1")
        monkeypatch.setenv("OPENVIKING_EXCEL_PARSE_PROCESS_MIN_CHARS", "0")
        path = self._make_file(tmp_path, suffix=".xls")
        assert self._parser()._should_use_process_pool(path, {}) is False

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"enable_link_rewrite": True},
            {"base_dir": Path(".")},
            {"allowed_media_dirs": [Path(".")]},
        ],
    )
    def test_link_or_media_rewrite_kwargs_disable_process_pool(self, tmp_path, monkeypatch, kwargs):
        # The worker never touches VikingFS/base_dir-relative media, so any parse
        # that needs link/media rewriting must stay in-process.
        monkeypatch.setenv("OPENVIKING_EXCEL_PARSE_PROCESS_POOL", "1")
        monkeypatch.setenv("OPENVIKING_EXCEL_PARSE_PROCESS_MIN_CHARS", "0")
        path = self._make_file(tmp_path)
        assert self._parser()._should_use_process_pool(path, kwargs) is False

    def test_min_chars_zero_always_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENVIKING_EXCEL_PARSE_PROCESS_POOL", "1")
        monkeypatch.setenv("OPENVIKING_EXCEL_PARSE_PROCESS_MIN_CHARS", "0")
        path = self._make_file(tmp_path, size=1)
        assert self._parser()._should_use_process_pool(path, {}) is True

    def test_below_min_chars_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENVIKING_EXCEL_PARSE_PROCESS_POOL", "1")
        monkeypatch.setenv("OPENVIKING_EXCEL_PARSE_PROCESS_MIN_CHARS", "1000")
        path = self._make_file(tmp_path, size=10)
        assert self._parser()._should_use_process_pool(path, {}) is False

    def test_at_or_above_min_chars_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENVIKING_EXCEL_PARSE_PROCESS_POOL", "1")
        monkeypatch.setenv("OPENVIKING_EXCEL_PARSE_PROCESS_MIN_CHARS", "10")
        path = self._make_file(tmp_path, size=10)
        assert self._parser()._should_use_process_pool(path, {}) is True
