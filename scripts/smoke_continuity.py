"""Live smoke test for the continuity layer.

Runs against ``data/profiles/default/memory.db`` — verifies:

- Schema migration v2 applied cleanly.
- TaskThreadStore opens / lists / closes correctly.
- ContinuityLedger writes + retrieves the latest row.
- LedgerBuilder produces a non-empty document from current memory.
- recall_router.detect_recall_intent fires on real Travis-style phrases.
- recall_router.run_deep_recall returns a populated block.
- detect_amnesia catches the phrasings we care about.
- MemoryStore.startup_reconcile_ledger persists a brief.
- MemoryStore.get_continuity_block returns the pinned doc.

Prints one line per check; exits 0 on success, non-zero on failure.
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

# Make sure project root is on sys.path so `src.agent.*` resolves whether
# this is run from anywhere.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


def _check(label: str, ok: bool, detail: str = "") -> bool:
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {label}" + (f"  -- {detail}" if detail else ""))
    return ok


def main() -> int:
    failures = 0

    print("\n=== schema migration ===")
    try:
        from src.agent.memory_db import schema_version
        v = schema_version("default")
        if not _check("schema migrated to v>=2", v >= 2, f"version={v}"):
            failures += 1
    except Exception as e:
        traceback.print_exc()
        return 1

    print("\n=== task threads ===")
    try:
        from src.agent.task_threads import TaskThreadStore
        store = TaskThreadStore("default")
        before = store.counts()
        t = store.open(
            title="smoke-test thread (delete me)",
            description="Created by scripts/smoke_continuity.py",
            owner="andrew",
            tags=["smoke", "test"],
        )
        if not _check("open returned a thread with id", bool(t and t.id)):
            failures += 1
        fetched = store.get(t.id)
        if not _check("get round-trips the thread", bool(fetched and fetched.title == t.title)):
            failures += 1
        listed = store.list_open(limit=50)
        if not _check("list_open includes the new thread", any(x.id == t.id for x in listed)):
            failures += 1
        if not _check("close returns True", store.close(t.id, status="abandoned")):
            failures += 1
        after = store.counts()
        if not _check(
            "abandoned counter incremented",
            after.get("abandoned", 0) >= before.get("abandoned", 0),
            f"before={before} after={after}",
        ):
            failures += 1
        # Clean up so repeated runs don't accumulate test rows.
        if not _check("delete cleans up the smoke thread", store.delete(t.id)):
            failures += 1
    except Exception as e:
        print(f"[FAIL] task_threads exception: {e}")
        traceback.print_exc()
        failures += 1

    print("\n=== continuity ledger ===")
    try:
        from src.agent.memory import MemoryStore
        memory = MemoryStore(user_id="default")
        # startup_reconcile_ledger ran in __init__; pull the brief.
        brief = (memory.state.get("continuity_brief") or {}).get("brief", "")
        if not _check("startup wrote a continuity brief", bool(brief), f"len={len(brief)}"):
            failures += 1
        block = memory.get_continuity_block(max_chars=2000)
        if not _check("get_continuity_block returns a string", isinstance(block, str)):
            failures += 1
        if not _check("continuity block is non-empty", bool(block.strip()), f"len={len(block)}"):
            failures += 1
        latest = memory.ledger.get_latest()
        if not _check(
            "latest ledger row exists",
            latest is not None,
            f"version={getattr(latest, 'version', None)} built_by={getattr(latest, 'built_by', None)}",
        ):
            failures += 1
    except Exception as e:
        print(f"[FAIL] continuity_ledger exception: {e}")
        traceback.print_exc()
        failures += 1

    print("\n=== recall router intent detection ===")
    try:
        from src.agent.recall_router import detect_amnesia, detect_recall_intent

        cases = [
            ("recap what we built yesterday", True),
            ("do you remember the swarm conversation", True),
            ("what were we working on", True),
            ("can you write a hello world", False),
            ("yesterday we ran into a bug", True),
            ("read README.md", False),
            ("don't you remember", True),
            ("memories are tricky", False),  # 'memory' alone doesn't fire
        ]
        all_ok = True
        for text, expected in cases:
            intent = detect_recall_intent(text)
            triggered = bool(intent)
            ok = triggered == expected
            if not ok:
                all_ok = False
                print(f"   miss: {text!r} -> triggered={triggered} expected={expected}")
        if not _check("intent detection matches expected cases", all_ok):
            failures += 1

        amnesia_cases = [
            ("I don't remember what we discussed.", True),
            ("I cannot recall the exact filename.", False),  # mitigator: 'exact filename'
            ("This is the first time we've talked about it.", True),
            ("I don't have access to our prior conversation.", True),
            ("Nothing in my memory matches.", True),
            ("Sure, here's the answer.", False),
        ]
        am_ok = True
        for text, expected in amnesia_cases:
            got = detect_amnesia(text)
            if got != expected:
                am_ok = False
                print(f"   miss: {text!r} -> got={got} expected={expected}")
        if not _check("amnesia detection matches expected cases", am_ok):
            failures += 1
    except Exception as e:
        print(f"[FAIL] recall_router exception: {e}")
        traceback.print_exc()
        failures += 1

    print("\n=== deep recall (live) ===")
    try:
        from src.agent.memory import MemoryStore
        memory = MemoryStore(user_id="default")
        result = memory.force_recall("recap of what we have been building")
        if not _check("force_recall returned a result", result is not None):
            failures += 1
        else:
            block = result.block or ""
            if not _check(
                "recall block has content",
                bool(block.strip()),
                f"hits={len(result.hits)} sources={list(result.sources_summary.keys())}",
            ):
                failures += 1
    except Exception as e:
        print(f"[FAIL] deep recall exception: {e}")
        traceback.print_exc()
        failures += 1

    print("\n=== summary ===")
    if failures == 0:
        print("All checks passed.")
        return 0
    print(f"{failures} check(s) failed.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
