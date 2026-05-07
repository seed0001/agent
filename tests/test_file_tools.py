"""Tests for the file tools after the write_file/read_file/list_dir overhaul.

Covers the bug Andrew reported: claiming a file is saved while either reporting
the wrong path or returning success on a write that didn't fully land.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from src.tools import system as systools


def _run(coro):
    """Tiny sync wrapper so each test reads cleanly."""
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


@pytest.fixture
def tmp_root() -> Path:
    d = Path(tempfile.mkdtemp(prefix="agent_filetools_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ---------- write_file: success path -----------------------------------


def test_write_file_returns_absolute_path_and_byte_count(tmp_root: Path) -> None:
    target = tmp_root / "schedule.txt"
    out = _run(systools.write_file(str(target), "hello world"))
    assert out.startswith("Written and verified: ")
    assert str(target.resolve()) in out
    assert "(11 bytes)" in out
    assert target.read_text(encoding="utf-8") == "hello world"


def test_write_file_creates_missing_parent_dirs(tmp_root: Path) -> None:
    target = tmp_root / "deeply" / "nested" / "thing" / "file.txt"
    out = _run(systools.write_file(str(target), "ok"))
    assert "Written and verified" in out
    assert target.exists()


def test_write_file_overwrites_existing(tmp_root: Path) -> None:
    target = tmp_root / "f.txt"
    _run(systools.write_file(str(target), "first"))
    out = _run(systools.write_file(str(target), "second-longer"))
    assert "Written and verified" in out
    assert target.read_text() == "second-longer"


def test_write_file_empty_content_is_valid(tmp_root: Path) -> None:
    target = tmp_root / "empty.txt"
    out = _run(systools.write_file(str(target), ""))
    assert "Written and verified" in out
    assert "(0 bytes)" in out
    assert target.exists()
    assert target.read_text() == ""


def test_write_file_handles_unicode(tmp_root: Path) -> None:
    target = tmp_root / "unicode.txt"
    body = "café — résumé — Δ"
    out = _run(systools.write_file(str(target), body))
    assert "Written and verified" in out
    expected_bytes = len(body.encode("utf-8"))
    assert f"({expected_bytes} bytes)" in out
    assert target.read_text(encoding="utf-8") == body


def test_write_file_resolves_relative_paths_to_absolute(tmp_root: Path, monkeypatch) -> None:
    """The exact bug Andrew reported. He passes a relative name; we must
    resolve and report the absolute one so callers can quote it back."""
    monkeypatch.chdir(tmp_root)
    out = _run(systools.write_file("just_a_name.txt", "x"))
    assert str(tmp_root.resolve()) in out
    assert (tmp_root / "just_a_name.txt").exists()


# ---------- write_file: input validation -------------------------------


def test_write_file_rejects_none_content(tmp_root: Path) -> None:
    out = _run(systools.write_file(str(tmp_root / "x.txt"), None))  # type: ignore[arg-type]
    assert out.startswith("Error:")
    assert "None" in out


def test_write_file_rejects_non_string_content(tmp_root: Path) -> None:
    out = _run(systools.write_file(str(tmp_root / "x.txt"), 12345))  # type: ignore[arg-type]
    assert out.startswith("Error:")
    assert "str" in out


def test_write_file_refuses_to_overwrite_directory(tmp_root: Path) -> None:
    a_dir = tmp_root / "iam_a_dir"
    a_dir.mkdir()
    out = _run(systools.write_file(str(a_dir), "oops"))
    assert out.startswith("Error:")
    assert "directory" in out.lower()


# ---------- write_file: atomicity --------------------------------------


def test_write_file_does_not_leave_temp_file_on_success(tmp_root: Path) -> None:
    target = tmp_root / "atomic.txt"
    _run(systools.write_file(str(target), "atomic content"))
    leftovers = [p for p in tmp_root.iterdir() if p.name.startswith(".") and p.suffix == ".tmp"]
    assert leftovers == []


def test_write_file_atomic_replace_keeps_old_file_on_failure(tmp_root: Path, monkeypatch) -> None:
    """If the temp-file write blows up, the destination must stay intact."""
    target = tmp_root / "preserved.txt"
    target.write_text("original", encoding="utf-8")

    real_replace = os.replace

    def boom(_src, _dst):
        raise PermissionError("simulated")

    monkeypatch.setattr(os, "replace", boom)
    out = _run(systools.write_file(str(target), "would-be-new"))
    monkeypatch.setattr(os, "replace", real_replace)

    assert out.startswith("Error:")
    assert target.read_text() == "original"
    leftovers = [p for p in tmp_root.iterdir() if p.name.startswith(".") and p.suffix == ".tmp"]
    assert leftovers == []  # cleanup ran


# ---------- read_file --------------------------------------------------


def test_read_file_returns_content(tmp_root: Path) -> None:
    target = tmp_root / "r.txt"
    target.write_text("data", encoding="utf-8")
    out = _run(systools.read_file(str(target)))
    assert out == "data"


def test_read_file_missing_includes_absolute_path(tmp_root: Path) -> None:
    out = _run(systools.read_file(str(tmp_root / "ghost.txt")))
    assert "Error" in out
    assert str(tmp_root.resolve()) in out
    assert "ghost.txt" in out


def test_read_file_missing_relative_shows_where_we_looked(tmp_root: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_root)
    out = _run(systools.read_file("nope.txt"))
    assert "Error" in out
    assert str(tmp_root.resolve()) in out  # must echo the absolute path


def test_read_file_directory_returns_clear_error(tmp_root: Path) -> None:
    out = _run(systools.read_file(str(tmp_root)))
    assert "Not a file" in out


# ---------- list_dir ---------------------------------------------------


def test_list_dir_lists_entries_with_absolute_header(tmp_root: Path) -> None:
    (tmp_root / "a.txt").write_text("x")
    (tmp_root / "sub").mkdir()
    out = _run(systools.list_dir(str(tmp_root)))
    first_line = out.splitlines()[0]
    assert first_line == f"# {tmp_root.resolve()}"
    assert "a.txt" in out
    assert "sub/" in out


def test_list_dir_missing_includes_absolute_path(tmp_root: Path) -> None:
    out = _run(systools.list_dir(str(tmp_root / "ghost-dir")))
    assert "Error" in out
    assert str((tmp_root / "ghost-dir").resolve()) in out


def test_list_dir_empty_returns_empty_marker(tmp_root: Path) -> None:
    out = _run(systools.list_dir(str(tmp_root)))
    assert "(empty)" in out


# ---------- verify_file_exists ------------------------------------------


def test_verify_file_exists_reports_exists_and_size(tmp_root: Path) -> None:
    target = tmp_root / "v.txt"
    target.write_text("12345", encoding="utf-8")
    out = _run(systools.verify_file_exists(str(target)))
    assert out.startswith("EXISTS:")
    assert "(5 bytes)" in out
    assert str(target.resolve()) in out


def test_verify_file_exists_reports_directory(tmp_root: Path) -> None:
    out = _run(systools.verify_file_exists(str(tmp_root)))
    assert out.startswith("DIRECTORY:")


def test_verify_file_exists_reports_missing(tmp_root: Path) -> None:
    out = _run(systools.verify_file_exists(str(tmp_root / "ghost.txt")))
    assert out.startswith("NOT FOUND:")


# ---------- end-to-end: write then verify -------------------------------


def test_write_then_verify_roundtrip(tmp_root: Path) -> None:
    """The flow Andrew should follow: write, then verify before claiming saved."""
    target = tmp_root / "schedule.txt"
    body = "Morning schedule\n- 7:00 wake up\n- 7:05 meds"
    write_out = _run(systools.write_file(str(target), body))
    verify_out = _run(systools.verify_file_exists(str(target)))
    assert "Written and verified" in write_out
    assert "EXISTS" in verify_out
    expected = len(body.encode("utf-8"))
    assert f"({expected} bytes)" in write_out
    assert f"({expected} bytes)" in verify_out
