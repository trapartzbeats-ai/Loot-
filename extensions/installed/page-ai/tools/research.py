"""Page research tool — research a topic using page content + web search."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run(topic: str = "", url: str = "", **kwargs) -> dict:
    """Research a topic using page content and web search."""
    if not topic and not url:
        return {"error": "topic or url required"}

    results = {"topic": topic, "sources": []}

    # If URL provided, fetch its content
    if url:
        import scraper
        try:
            page_data = scraper.fetch_url(url, max_chars=5000)
            results["sources"].append({"type": "page", "url": url, "title": page_data.get("title"), "content": page_data.get("text", "")[:2000]})
        except Exception as e:
            results["sources"].append({"type": "page", "url": url, "error": str(e)})

    # Web search
    if topic:
        import scraper
        try:
            search_result = scraper.web_search(topic, limit=5)
            results["sources"].append({"type": "search", "query": topic, "results": search_result.get("results", [])})
        except Exception as e:
            results["sources"].append({"type": "search", "query": topic, "error": str(e)})

    # Compile findings
    findings = []
    for source in results["sources"]:
        if source.get("type") == "page":
            findings.append(f"From {source.get('url', '')}: {source.get('content', '')[:300]}")
        elif source.get("type") == "search":
            for r in source.get("results", [])[:3]:
                findings.append(f"Search result: {r.get('title', '')} - {r.get('snippet', '')[:200]}")

    results["findings"] = findings
    results["summary"] = "\n".join(findings[:5])

    return results
