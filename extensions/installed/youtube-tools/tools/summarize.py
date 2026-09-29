"""YouTube summarize tool — summarize video from transcript."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run(url: str = "", transcript: str = "", **kwargs) -> dict:
    """Summarize a YouTube video. Either provide url (fetches transcript) or transcript text."""
    if not url and not transcript:
        return {"error": "url or transcript required"}

    # If URL provided, fetch transcript first
    if url and not transcript:
        from tools.transcript import run as get_transcript
        result = await get_transcript(url=url)
        if "error" in result:
            return result
        transcript = result.get("transcript", "")

    if not transcript:
        return {"error": "No transcript available"}

    # Simple extractive summary (first + last + key sentences)
    sentences = [s.strip() for s in transcript.replace(". ", ".").split(".") if len(s.strip()) > 20]

    if len(sentences) <= 3:
        summary = ". ".join(sentences)
    else:
        # Take first 2, last 1, and some from middle
        key_sentences = sentences[:2] + sentences[len(sentences)//2:len(sentences)//2+1] + sentences[-1:]
        summary = ". ".join(key_sentences)

    word_count = len(transcript.split())

    return {
        "word_count": word_count,
        "summary": summary[:2000],
        "key_points": sentences[:5] if sentences else [],
    }
