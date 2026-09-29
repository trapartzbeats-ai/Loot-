"""Page extract tool — extract specific data from a page."""
import re
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run(url: str = "", extract_type: str = "all", **kwargs) -> dict:
    """Extract specific data from a page. extract_type: emails, phones, prices, dates, links, all."""
    if not url:
        return {"error": "url required"}

    import scraper
    try:
        page_data = scraper.fetch_url(url, max_chars=10000)
        text = page_data.get("text", "")
    except Exception as e:
        return {"error": f"Failed to fetch page: {e}"}

    results = {"url": url, "extract_type": extract_type}

    if extract_type in ("emails", "all"):
        emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
        results["emails"] = list(set(emails))

    if extract_type in ("phones", "all"):
        phones = re.findall(r'[\+]?[(]?[0-9]{1,3}[)]?[-\s\.]?[0-9]{3}[-\s\.]?[0-9]{4,6}', text)
        results["phones"] = list(set(phones))

    if extract_type in ("prices", "all"):
        prices = re.findall(r'[\$€£¥]\s*\d+[\d,.]*|\d+[\d,.]*\s*[\$€£¥]', text)
        results["prices"] = list(set(prices))

    if extract_type in ("dates", "all"):
        dates = re.findall(r'\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}|\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2}', text)
        results["dates"] = list(set(dates))

    if extract_type in ("links", "all"):
        try:
            links_result = scraper.extract_links(url)
            results["links"] = links_result.get("links", [])[:50]
        except Exception:
            results["links"] = []

    return results
