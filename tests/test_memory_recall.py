from src import artifact_memory, memory_recall, schedule_memory


def test_search_memory_finds_schedule_and_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule_memory, "SCHEDULES_PATH", tmp_path / "schedules.json")
    monkeypatch.setattr(artifact_memory, "ARTIFACTS_PATH", tmp_path / "artifacts.json")
    monkeypatch.setattr(memory_recall, "db_path", lambda user_id="default": tmp_path / "missing.db")
    monkeypatch.setattr(memory_recall.contacts, "get_all_contacts", lambda: [])

    schedule_memory.remember_schedule(
        title="Travis Morning Schedule",
        schedule_date="2026-05-08",
        items=["Take medication"],
    )
    path = tmp_path / "My_Ideas.txt"
    path.write_text("ideas", encoding="utf-8")
    artifact_memory.record_artifact(str(path), summary="Prediction engine ideas")

    result = memory_recall.search_memory("medication prediction")

    assert "[schedule] Travis Morning Schedule" in result
    assert "[artifact] My_Ideas.txt" in result
