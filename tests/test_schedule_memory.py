from src import schedule_memory


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(schedule_memory, "SCHEDULES_PATH", tmp_path / "schedules.json")


def test_remember_and_get_schedule_by_date(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    saved = schedule_memory.remember_schedule(
        title="Travis Morning Schedule",
        schedule_date="2026-05-08",
        items=[
            {"text": "Take morning medication"},
            {"text": "Let Chance out", "time": "morning"},
        ],
        notes="Reconstructed from chat.",
    )

    loaded = schedule_memory.get_schedule("2026-05-08")

    assert saved.id == "2026-05-08-travis-morning-schedule"
    assert loaded is not None
    assert loaded.title == "Travis Morning Schedule"
    assert [item.text for item in loaded.items] == [
        "Take morning medication",
        "Let Chance out",
    ]
    assert loaded.notes == "Reconstructed from chat."


def test_list_schedules_hides_archived_by_default(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    schedule_memory.remember_schedule(
        title="Active",
        schedule_date="2026-05-08",
        items=["Do active thing"],
    )
    schedule_memory.remember_schedule(
        title="Archived",
        schedule_date="2026-05-07",
        items=["Old thing"],
        status="archived",
    )

    assert [s.title for s in schedule_memory.list_schedules()] == ["Active"]
    assert {s.title for s in schedule_memory.list_schedules(include_archived=True)} == {
        "Active",
        "Archived",
    }


def test_format_for_context_includes_durable_schedule(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    schedule_memory.remember_schedule(
        title="Travis Morning Schedule",
        schedule_date="2026-05-08",
        items=["Take meds", "Feed Chance"],
        file_path=r"C:\schedule.txt",
    )

    block = schedule_memory.format_for_context()

    assert "## Schedules / Plans" in block
    assert "Travis Morning Schedule" in block
    assert "Take meds" in block
    assert r"C:\schedule.txt" in block
