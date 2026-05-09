"""
Background research script: web search + compile findings.
Run via spawn_subagent for transformer/model research.
Output: data/research_output/transformer_research_latest.md
"""
import asyncio
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from config.settings import RESEARCH_OUTPUT_DIR
from src.tools.search import search_web


def _topic_slug(topic: str) -> str:
    words = re.findall(r"[a-z0-9]+", (topic or "").lower())
    words = [w for w in words if w not in {"research"}]
    if not words:
        return "research"
    return "_".join(words[:6]).strip("_") or "research"


async def run_research(topic: str = "transformer architectures for fine-tuning") -> str:
    """Search web, compile findings, return report and output path."""
    sections = []
    sections.append(f"# Research: {topic}\n")
    sections.append(f"Generated: {datetime.now().isoformat()}\n")

    queries = [
        f"best {topic} 2024 2025",
        f"Hugging Face transformer models for NLP fine-tuning",
        f"base models for task prioritization user intent prediction",
    ]

    for q in queries:
        sections.append(f"\n## Query: {q}\n")
        try:
            results = await search_web(q, max_results=5)
            sections.append(results)
        except Exception as e:
            sections.append(f"Error: {e}")

    report = "\n".join(sections)

    RESEARCH_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = _topic_slug(topic)
    path = RESEARCH_OUTPUT_DIR / f"{slug}_{timestamp}.md"
    latest = RESEARCH_OUTPUT_DIR / f"{slug}_latest.md"

    path.write_text(report, encoding="utf-8")
    latest.write_text(report, encoding="utf-8")

    # Compatibility alias for older docs/prompts that still reference this path.
    if "transformer" in (topic or "").lower():
        legacy_latest = RESEARCH_OUTPUT_DIR / "transformer_research_latest.md"
        legacy_latest.write_text(report, encoding="utf-8")

    return str(latest)


def main():
    topic = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "transformer architectures for fine-tuning"
    path = asyncio.run(run_research(topic))
    print(f"Research complete. Output: {path}")


if __name__ == "__main__":
    main()
