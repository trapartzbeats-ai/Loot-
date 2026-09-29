"""YouTube transcript tool — extract subtitles."""
import json
import re
import urllib.request


async def run(url: str = "", **kwargs) -> dict:
    """Extract transcript from a YouTube video."""
    if not url:
        return {"error": "url required"}

    match = re.search(r'(?:v=|youtu\.be/|embed/)([a-zA-Z0-9_-]{11})', url)
    if not match:
        return {"error": "Could not extract video ID"}

    video_id = match.group(1)

    try:
        # Fetch timedtext API
        req = urllib.request.Request(
            f"https://www.youtube.com/api/timedtext?lang=en&v={video_id}",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml = resp.read().decode()

        # Parse transcript lines
        texts = re.findall(r'<text start="([\d.]+)" dur="([\d.]+)">([^<]+)</text>', xml)
        if not texts:
            # Try alternative format
            texts = re.findall(r'<text[^>]*start="([\d.]+)"[^>]*>([^<]+)</text>', xml)

        transcript = []
        for start, dur, text in texts:
            transcript.append({
                "start": float(start),
                "duration": float(dur),
                "text": text.replace("\\n", "\n").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            })

        full_text = " ".join(t["text"] for t in transcript)

        return {
            "video_id": video_id,
            "language": "en",
            "segments": len(transcript),
            "duration_total": sum(t["duration"] for t in transcript),
            "transcript": full_text[:5000],
            "segments_detail": transcript[:50],
        }
    except Exception as e:
        return {"error": str(e)}
