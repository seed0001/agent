from pathlib import Path

from src.agent.core import _guard_unverified_file_claims


def test_file_claim_guard_corrects_missing_path():
    content = (
        r"I've created the file at "
        r"C:\Users\aztre\Desktop\agent\andrew's projects\File_Creation_Issue_Report.txt."
    )

    guarded = _guard_unverified_file_claims(content, [])

    assert "Correction:" in guarded
    assert "cannot verify the following path exists" in guarded
    assert "File_Creation_Issue_Report.txt" in guarded


def test_file_claim_guard_corrects_no_same_turn_write_even_if_file_exists(tmp_path):
    target = tmp_path / "report.txt"
    target.write_text("ok", encoding="utf-8")
    content = f"I've saved the report document at {target}."

    guarded = _guard_unverified_file_claims(content, [])

    assert "Correction:" in guarded
    assert "did not create or verify it in this turn" in guarded


def test_file_claim_guard_allows_verified_write(tmp_path):
    target = tmp_path / "report.txt"
    target.write_text("ok", encoding="utf-8")
    content = f"I've saved the report document at {target}."
    result = f"Written and verified: {Path(target).resolve()} (2 bytes)"

    guarded = _guard_unverified_file_claims(content, [{"name": "write_file", "result": result}])

    assert guarded == content


def test_file_claim_guard_ignores_non_file_claims():
    content = "I created a plan in my head, but I have not saved anything."

    guarded = _guard_unverified_file_claims(content, [])

    assert guarded == content
