"""Page translate tool — extract and translate page content."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run(url: str = "", target_lang: str = "es", **kwargs) -> dict:
    """Extract page content and provide translation info."""
    if not url:
        return {"error": "url required"}

    import scraper
    try:
        page_data = scraper.fetch_url(url, max_chars=5000)
        text = page_data.get("text", "")
        title = page_data.get("title", "")
    except Exception as e:
        return {"error": f"Failed to fetch page: {e}"}

    # Simple word-by-word "translation" placeholder
    # In production, this would call a translation API
    # For now, extract key terms and note the target language

    words = text.split()
    unique_words = list(set(w.lower() for w in words if len(w) > 4))[:50]

    return {
        "title": title,
        "url": url,
        "target_language": target_lang,
        "original_text_length": len(text),
        "extracted_summary": text[:2000],
        "key_terms": unique_words,
        "translation_note": f"Content extracted for translation to {target_lang}. Use with a translation service.",
    }
