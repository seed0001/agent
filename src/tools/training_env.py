"""Subprocess environment helpers for training jobs launched by Andrew."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


GPU_VISIBILITY_ENV_VARS = (
    "CUDA_VISIBLE_DEVICES",
    "HIP_VISIBLE_DEVICES",
    "ROCR_VISIBLE_DEVICES",
    "GPU_DEVICE_ORDINAL",
)
DEFAULT_TRAIN_GPU_INDEX = "1"

TRAINING_MARKERS = (
    "train_core_lora",
    "train_soul.py",
    "run_full_soul_training.py",
    "lora",
    "soul_training",
)


def is_training_invocation(command: str | Iterable[str]) -> bool:
    if isinstance(command, str):
        text = command
    else:
        text = " ".join(str(part) for part in command)
    lowered = text.replace("\\", "/").lower()
    return any(marker in lowered for marker in TRAINING_MARKERS)


def env_for_child(command: str | Iterable[str], base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Build child env, selecting the discrete GPU for training commands."""
    env = dict(base_env or os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if not is_training_invocation(command):
        return env
    if env.get("ANDREW_TRAIN_DEVICE", "").strip().lower() == "cpu":
        return env
    gpu_index = env.get("ANDREW_TRAIN_GPU_INDEX", DEFAULT_TRAIN_GPU_INDEX).strip()
    if gpu_index.lower() in {"", "auto"}:
        gpu_index = DEFAULT_TRAIN_GPU_INDEX
    if gpu_index.lower() == "all":
        for name in GPU_VISIBILITY_ENV_VARS:
            if env.get(name) in {"-1", "", "none", "None"}:
                env.pop(name, None)
    else:
        for name in GPU_VISIBILITY_ENV_VARS:
            env[name] = gpu_index
        env["ANDREW_TRAIN_GPU_INDEX"] = gpu_index
    env["ANDREW_TRAINING_CHILD"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def script_command(script_path: str | Path, args: Iterable[str]) -> list[str]:
    return [str(script_path), *[str(arg) for arg in args]]
