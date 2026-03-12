"""Web search via DuckDuckGo (free, no API key). Uses ddgs package."""
import asyncio
from ddgs import DDGS


def _search_sync(query: str, max_results: int) -> list[dict]:
    """Run search in thread."""
    results = list(DDGS().text(query, max_results=max_results))
    return [
        {
            "title": r.get("title", ""),
            "href": r.get("href", ""),
            "body": r.get("body", ""),
        }
        for r in results
    ]


async def search_web(query: str, max_results: int = 8) -> str:
    """Search the web and return formatted results."""
    try:
        results = await asyncio.to_thread(_search_sync, query, max_results)
    except Exception as e:
        return f"Error: {e}"

    if not results:
        return "No results found."

    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "No title")
        href = r.get("href", "")
        body = (r.get("body") or "")[:200]
        lines.append(f"{i}. {title}\n   {href}\n   {body}")
    return "\n\n".join(lines)
