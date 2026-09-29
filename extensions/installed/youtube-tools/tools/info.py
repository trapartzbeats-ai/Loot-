"""YouTube info tool — extract video metadata."""
import json
import re
import urllib.request


async def run(url: str = "", **kwargs) -> dict:
    """Get info about a YouTube video."""
    if not url:
        return {"error": "url required"}

    # Extract video ID
    match = re.search(r'(?:v=|youtu\.be/|embed/)([a-zA-Z0-9_-]{11})', url)
    if not match:
        return {"error": "Could not extract video ID from URL"}

    video_id = match.group(1)

    try:
        # Fetch the watch page
        req = urllib.request.Request(
            f"https://www.youtube.com/watch?v={video_id}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode()

        # Extract title
        title_match = re.search(r'"title":"([^"]+)"', html)
        title = title_match.group(1) if title_match else "Unknown"

        # Extract duration
        duration_match = re.search(r'"lengthSeconds":"(\d+)"', html)
        duration = int(duration_match.group(1)) if duration_match else 0

        # Extract views
        views_match = re.search(r'"viewCount":"(\d+)"', html)
        views = int(views_match.group(1)) if views_match else 0

        # Extract description
        desc_match = re.search(r'"shortDescription":"([^"]+)"', html)
        description = desc_match.group(1) if desc_match else ""

        # Extract channel
        channel_match = re.search(r'"author":"([^"]+)"', html)
        channel = channel_match.group(1) if channel_match else "Unknown"

        return {
            "video_id": video_id,
            "title": title,
            "channel": channel,
            "duration_seconds": duration,
            "duration_formatted": f"{duration // 60}:{duration % 60:02d}",
            "view_count": views,
            "description": description[:500],
            "url": f"https://www.youtube.com/watch?v={video_id}",
        }
    except Exception as e:
        return {"error": str(e)}
