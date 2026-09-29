"""Page explain tool — summarize what a page is about."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run(url: str = "", **kwargs) -> dict:
    """Explain what a page is about."""
    if not url:
        return {"error": "url required"}

    import scraper
    try:
        page_data = scraper.fetch_url(url, max_chars=8000)
        text = page_data.get("text", "")
        title = page_data.get("title", "")
    except Exception as e:
        return {"error": f"Failed to fetch page: {e}"}

    if not text:
        return {"error": "No text content found"}

    # Extract key sentences
    sentences = [s.strip() for s in text.replace(". ", ".").split(".") if len(s.strip()) > 30]

    # Build explanation
    word_count = len(text.split())
    key_points = sentences[:5] if sentences else []

    return {
        "title": title,
        "url": url,
        "word_count": word_count,
        "explanation": f"This page '{title}' contains approximately {word_count} words.",
        "main_topics": key_points,
        "summary": ". ".join(key_points[:3]) if key_points else text[:500],
    }
