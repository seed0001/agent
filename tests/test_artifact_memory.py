from pathlib import Path

from src import artifact_memory


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(artifact_memory, "ARTIFACTS_PATH", tmp_path / "artifacts.json")


def test_record_artifact_tracks_verified_file(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    target = tmp_path / "andrew's projects" / "ideas" / "My_Ideas.txt"
    target.parent.mkdir(parents=True)
    target.write_text("ideas", encoding="utf-8")

    artifact = artifact_memory.record_artifact(str(target), summary="Idea list")
    loaded = artifact_memory.get_artifact("My_Ideas")

    assert artifact.exists is True
    assert artifact.size_bytes == 5
    assert artifact.category == "ideas"
    assert loaded is not None
    assert loaded.path == str(target.resolve())
    assert "Idea list" in artifact_memory.format_artifact(loaded)


def test_list_artifacts_filters_category(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    schedule = tmp_path / "schedule.txt"
    journal = tmp_path / "journal.txt"
    schedule.write_text("schedule", encoding="utf-8")
    journal.write_text("journal", encoding="utf-8")

    artifact_memory.record_artifact(str(schedule), category="schedule")
    artifact_memory.record_artifact(str(journal), category="journal")

    assert [a.category for a in artifact_memory.list_artifacts("schedule")] == ["schedule"]
    assert len(artifact_memory.list_artifacts()) == 2


def test_search_artifacts_matches_title_summary_and_path(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    target = tmp_path / "proactive_outreach_prompt.txt"
    target.write_text("prompt", encoding="utf-8")
    artifact_memory.record_artifact(str(target), summary="Proactive outreach build prompt")

    hits = artifact_memory.search_artifacts("outreach")

    assert len(hits) == 1
    assert hits[0].title == "proactive_outreach_prompt.txt"
