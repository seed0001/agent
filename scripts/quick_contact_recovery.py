from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

BASE = Path(r"C:\Users\aztre\Desktop\Andrew-Core-Foundation")
OLD = Path(r"C:\Users\aztre\Desktop\agent")
OUT_DIR = BASE / "data" / "contact_recovery"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SOURCES = [
    BASE / "data" / "profiles" / "default" / "memory.db",
    BASE / "data" / "profiles" / "default" / "episodic_cache.jsonl",
    BASE / "data" / "profiles" / "default" / "contacts.json",
    BASE / "data" / "profiles" / "default" / "artifacts.json",
    BASE / "logs" / "agent.log",
    OLD / "logs" / "agent.log",
    OLD / "README.md",
    OLD / "project_roadmap.md",
    OLD / "Documents" / "01_Who_I_Am_Novas_Introduction.md",
]

REL_TERMS = [
    "family", "friend", "friends", "best friend", "good friend", "contact",
    "wife", "girlfriend", "partner", "mom", "mother", "dad", "father",
    "brother", "sister", "son", "daughter", "kid", "kids", "children",
    "Brandon", "Chance", "Sarah", "Leora", "Nova", "Good Vibes", "Discord",
]
NAME_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})?\b")
STOP = set("""
Andrew Travis Discord Good Vibes OpenAI Ollama Gemma Grok Creator User Assistant System Memory Profile Project Roadmap Windows Python PowerShell GitHub README Tool Tools Channel Category Role Roles Text Voice Start Community Help Lab Current Recent Core The This That When What Where Error File Path True False None JSON SQLite Table Logs Agent Model Function Command Desktop Foundation Andrew Core Good Vibes
""".split())


def snippet(text: str, idx: int, radius: int = 260) -> str:
    a = max(0, idx - radius)
    b = min(len(text), idx + radius)
    return " ".join(text[a:b].split())


def scan_text(label: str, text: str, term_hits: dict, name_counts: Counter):
    low = text.lower()
    for term in REL_TERMS:
        start = 0
        count = 0
        t = term.lower()
        while count < 8:
            idx = low.find(t, start)
            if idx == -1:
                break
            term_hits[term].append({"source": label, "snippet": snippet(text, idx)})
            start = idx + len(t)
            count += 1
    for name in NAME_RE.findall(text):
        parts = name.split()
        if any(p in STOP for p in parts):
            continue
        if name in STOP or len(name) < 3:
            continue
        name_counts[name] += 1


def read_source(path: Path, term_hits: dict, name_counts: Counter):
    if not path.exists():
        return
    try:
        if path.suffix.lower() == ".db":
            con = sqlite3.connect(str(path))
            cur = con.cursor()
            tables = [r[0] for r in cur.execute("select name from sqlite_master where type='table'").fetchall()]
            chunks = []
            for table in tables:
                try:
                    cols = [r[1] for r in cur.execute(f"pragma table_info({table})").fetchall()]
                    text_cols = [c for c in cols if c.lower() in {"content", "value", "message", "text", "summary", "name", "display_name", "notes", "key"}]
                    if not text_cols:
                        continue
                    q = "select " + ",".join(text_cols) + f" from {table} limit 10000"
                    for row in cur.execute(q).fetchall():
                        chunks.append(" | ".join(str(x) for x in row if x is not None))
                except Exception:
                    pass
            scan_text(str(path), "\n".join(chunks), term_hits, name_counts)
        else:
            size = path.stat().st_size
            if size > 10_000_000:
                return
            scan_text(str(path), path.read_text(errors="ignore"), term_hits, name_counts)
    except Exception as e:
        term_hits["errors"].append({"source": str(path), "snippet": repr(e)})


def main():
    term_hits = defaultdict(list)
    name_counts = Counter()
    for src in SOURCES:
        read_source(src, term_hits, name_counts)

    contacts_path = BASE / "data" / "profiles" / "default" / "contacts.json"
    contacts = {}
    if contacts_path.exists():
        try:
            contacts = json.loads(contacts_path.read_text(errors="ignore"))
        except Exception as e:
            contacts = {"error": str(e)}

    confirmed = []
    if "web-brandon" in contacts:
        confirmed.append({"name": "Brandon", "relationship": "stored contact, tier best_friend", "source": "contacts.json"})
    if "550782786013757442" in contacts:
        confirmed.append({"name": "Travis / Creator Discord record", "relationship": "creator/owner Discord contact", "source": "contacts.json"})

    likely = []
    if term_hits.get("Chance"):
        likely.append({"name": "Chance", "relationship": "care-related reference appears in memory/log search, but exact relationship not confirmed", "source_count": len(term_hits["Chance"])})
    if term_hits.get("Sarah"):
        likely.append({"name": "Sarah", "relationship": "planned companion persona, not necessarily a real-life contact", "source_count": len(term_hits["Sarah"])})
    if term_hits.get("Leora"):
        likely.append({"name": "Leora", "relationship": "appears in knowledge/project material; likely project/persona/DJ reference, not confirmed personal contact", "source_count": len(term_hits["Leora"])})
    if term_hits.get("Nova"):
        likely.append({"name": "Nova", "relationship": "appears in identity document title/content; not confirmed personal contact", "source_count": len(term_hits["Nova"])})

    report = {
        "generated_at": datetime.now().isoformat(),
        "sources": [str(p) for p in SOURCES],
        "confirmed_contacts": confirmed,
        "likely_or_candidate_mentions": likely,
        "top_names": name_counts.most_common(80),
        "relationship_term_hits": {k: v[:12] for k, v in term_hits.items()},
    }

    raw_path = OUT_DIR / "quick_contact_recovery_raw.json"
    md_path = OUT_DIR / "quick_contact_recovery_report.md"
    raw_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    lines = ["# Quick Contact Recovery Report", "", f"Generated: {report['generated_at']}", ""]
    lines += ["## Confirmed", ""]
    for c in confirmed:
        lines.append(f"- **{c['name']}** — {c['relationship']} ({c['source']})")
    if not confirmed:
        lines.append("- None found.")
    lines += ["", "## Candidate / not yet safe to store as contacts", ""]
    for c in likely:
        lines.append(f"- **{c['name']}** — {c['relationship']} [{c['source_count']} hits]")
    if not likely:
        lines.append("- None found beyond confirmed contacts.")
    lines += ["", "## Top proper-name candidates", ""]
    for name, count in name_counts.most_common(40):
        lines.append(f"- {name}: {count}")
    lines += ["", "## Useful relationship snippets", ""]
    for term in ["family", "friend", "friends", "best friend", "Brandon", "Chance", "wife", "mom", "dad", "brother", "sister", "son", "daughter", "Good Vibes", "Discord"]:
        hits = term_hits.get(term, [])[:5]
        if hits:
            lines.append(f"### {term}")
            for h in hits:
                lines.append(f"- `{h['source']}`: {h['snippet']}")
            lines.append("")
    lines.append(f"Raw JSON: `{raw_path}`")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(md_path)
    print(raw_path)

if __name__ == "__main__":
    main()
