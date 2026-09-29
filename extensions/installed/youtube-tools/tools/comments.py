"""YouTube comments tool — extract top comments."""
import json
import re
import urllib.request


async def run(url: str = "", max_comments: int = 10, **kwargs) -> dict:
    """Extract top comments from a YouTube video."""
    if not url:
        return {"error": "url required"}

    match = re.search(r'(?:v=|youtu\.be/|embed/)([a-zA-Z0-9_-]{11})', url)
    if not match:
        return {"error": "Could not extract video ID"}

    video_id = match.group(1)

    try:
        # Fetch the page and extract initial data
        req = urllib.request.Request(
            f"https://www.youtube.com/watch?v={video_id}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode()

        # Extract comments from ytInitialData
        match = re.search(r'var ytInitialData = ({.+?});', html)
        if not match:
            return {"error": "Could not find comment data"}

        data = json.loads(match.group(1))

        comments = []
        try:
            contents = data["contents"]["twoColumnWatchNextResults"]["results"]["results"]["contents"]
            for section in contents:
                if "itemSectionRenderer" in section:
                    for item in section["itemSectionRenderer"].get("contents", []):
                        if "commentsEntryPointHeaderRenderer" in item:
                            continue
                        if "commentThreadRenderer" in item:
                            comment = item["commentThreadRenderer"]["comment"]["commentRenderer"]
                            text_runs = comment.get("contentText", {}).get("runs", [])
                            text = "".join(r.get("text", "") for r in text_runs)
                            author = comment.get("authorText", {}).get("simpleText", "Unknown")
                            likes = int(comment.get("voteCount", {}).get("simpleText", "0").replace(",", ""))
                            comments.append({"author": author, "text": text[:300], "likes": likes})
                            if len(comments) >= max_comments:
                                break
        except (KeyError, TypeError):
            pass

        return {
            "video_id": video_id,
            "comments_found": len(comments),
            "comments": comments,
        }
    except Exception as e:
        return {"error": str(e)}
