#!/usr/bin/env python3
"""
Training Logger for Andrew Hybrid Core Model.

Captures every core decision as a structured training record
for later fine-tuning.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core_model.schemas import CoreInput, CoreOutput


class TrainingLogger:
    def __init__(self, log_dir: str = "data/core_training/episodes"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log_turn(
        self,
        user_message: str,
        core_input: CoreInput,
        core_output: CoreOutput,
        tool_result: str | None = None,
        correction: str | None = None
    ) -> str:
        """
        Save a single turn as a training record.
        Returns the path of the saved file.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        record_id = f"core-{timestamp.replace(':', '').replace('-', '')[:15]}"

        record = {
            "id": record_id,
            "created_at": timestamp,
            "source": "live_turn",
            "input": core_input.model_dump(),
            "target_core_output": core_output.model_dump(),
            "tool_result": tool_result,
            "correction": correction,
            "notes": correction or "auto-captured"
        }

        # Save as JSONL (one record per line)
        filename = self.log_dir / f"{record_id}.jsonl"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return str(filename)


# Global instance for easy use
training_logger = TrainingLogger()