from __future__ import annotations

import asyncio
import json

from src import outreach


def test_queue_outreach_persists_attachment_paths(monkeypatch, tmp_path):
    out_path = tmp_path / "outreach.jsonl"
    dedup_path = tmp_path / "outreach_dedup.json"
    monkeypatch.setattr(outreach, "OUTREACH_PATH", out_path)
    monkeypatch.setattr(outreach, "OUTREACH_DEDUP_PATH", dedup_path)
    monkeypatch.setattr(outreach, "_outreach_queue", asyncio.Queue())

    attachment = tmp_path / "sample_report.txt"
    attachment.write_text("hello", encoding="utf-8")

    msg = outreach.queue_outreach(
        "discord",
        "Here is the report.",
        target_channel_id="123456789",
        attachment_paths=[str(attachment)],
        is_direct=True,
    )

    assert "1 attachment(s)" in msg
    queued = outreach.get_outreach_queue().get_nowait()
    assert queued.attachment_paths == [str(attachment)]
    assert queued.target_channel_id == "123456789"

    raw = out_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(raw) == 1
    payload = json.loads(raw[0])
    assert payload["attachment_paths"] == [str(attachment)]
    assert payload["is_direct"] is True


def test_queue_outreach_defaults_to_no_attachments(monkeypatch, tmp_path):
    out_path = tmp_path / "outreach.jsonl"
    dedup_path = tmp_path / "outreach_dedup.json"
    monkeypatch.setattr(outreach, "OUTREACH_PATH", out_path)
    monkeypatch.setattr(outreach, "OUTREACH_DEDUP_PATH", dedup_path)
    monkeypatch.setattr(outreach, "_outreach_queue", asyncio.Queue())

    outreach.queue_outreach("discord", "plain message", target_user_id="42")
    queued = outreach.get_outreach_queue().get_nowait()

    assert queued.attachment_paths == []
    payload = json.loads(out_path.read_text(encoding="utf-8").strip())
    assert payload["attachment_paths"] == []


def test_queue_outreach_suppresses_duplicate_within_window(monkeypatch, tmp_path):
    out_path = tmp_path / "outreach.jsonl"
    dedup_path = tmp_path / "outreach_dedup.json"
    monkeypatch.setattr(outreach, "OUTREACH_PATH", out_path)
    monkeypatch.setattr(outreach, "OUTREACH_DEDUP_PATH", dedup_path)
    monkeypatch.setattr(outreach, "_outreach_queue", asyncio.Queue())

    first = outreach.queue_outreach(
        "discord",
        "good morning",
        target_user_id="42",
        source="proactive",
        trigger_key="morning_greeting",
    )
    second = outreach.queue_outreach(
        "discord",
        "good morning",
        target_user_id="42",
        source="direct",
        trigger_key="morning_greeting",
    )

    assert first.startswith("Message queued for discord")
    assert second.startswith("Duplicate suppressed")
    queued = outreach.get_outreach_queue().get_nowait()
    assert queued.content == "good morning"
    assert outreach.get_outreach_queue().empty()


def test_queue_outreach_allows_repeat_after_window(monkeypatch, tmp_path):
    out_path = tmp_path / "outreach.jsonl"
    dedup_path = tmp_path / "outreach_dedup.json"
    monkeypatch.setattr(outreach, "OUTREACH_PATH", out_path)
    monkeypatch.setattr(outreach, "OUTREACH_DEDUP_PATH", dedup_path)
    monkeypatch.setattr(outreach, "_outreach_queue", asyncio.Queue())

    first = outreach.queue_outreach(
        "discord",
        "status update",
        target_user_id="42",
        dedup_window_seconds=1,
    )
    assert first.startswith("Message queued for discord")
    # Force stale timestamp in dedup store to simulate time passage.
    state = {"manual_key": "2000-01-01T00:00:00"}
    state.update(outreach._load_dedup_state())
    outreach._save_dedup_state(state)
    # Overwrite all values to old date so the same message can pass.
    outreach._save_dedup_state({k: "2000-01-01T00:00:00" for k in outreach._load_dedup_state()})

    second = outreach.queue_outreach(
        "discord",
        "status update",
        target_user_id="42",
        dedup_window_seconds=1,
    )
    assert second.startswith("Message queued for discord")
