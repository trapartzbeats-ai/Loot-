"""Authenticated page-media discovery and bounded download pipeline."""

from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlsplit

from config import DATA_DIR


DOWNLOAD_DIR = DATA_DIR / "media_downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


async def discover(page, limit: int = 100) -> Dict[str, Any]:
    limit = max(1, min(int(limit), 500))
    items: List[Dict[str, Any]] = []
    seen = set()
    for index, frame in enumerate(page.frames):
        try:
            found = await frame.evaluate("""() => {
                const out=[];
                for (const el of document.querySelectorAll('video,audio,source,img,a[href]')) {
                    const raw=el.currentSrc||el.src||el.href||'';
                    if (!raw) continue;
                    const tag=el.tagName.toLowerCase();
                    const media = tag !== 'a' || /\\.(?:mp4|webm|mov|mp3|wav|m4a|ogg|flac|m3u8)(?:$|[?#])/i.test(raw);
                    if (media) out.push({url:raw, tag, type:el.type||'', title:el.title||el.alt||el.getAttribute('aria-label')||''});
                }
                return out;
            }""")
            for item in found:
                if item["url"] in seen:
                    continue
                seen.add(item["url"])
                item["frame"] = index
                item["kind"] = "stream" if ".m3u8" in item["url"].lower() else "image" if item["tag"] == "img" else "media"
                items.append(item)
                if len(items) >= limit:
                    break
        except Exception:
            continue
        if len(items) >= limit:
            break
    return {"count": len(items), "items": items, "truncated": len(items) >= limit}


async def download(context, url: str, filename: str = "", max_mb: int = 100) -> Dict[str, Any]:
    if url.startswith("blob:"):
        return {"downloaded": False, "error": "blob URLs require capture from the owning page", "url": url}
    max_bytes = max(1, min(int(max_mb), 2048)) * 1024 * 1024
    response = await context.request.get(url, timeout=60000)
    if not response.ok:
        return {"downloaded": False, "url": url, "status": response.status, "error": response.status_text}
    body = await response.body()
    if len(body) > max_bytes:
        return {"downloaded": False, "url": url, "size_bytes": len(body), "error": f"media exceeds {max_mb} MB limit"}
    content_type = (response.headers.get("content-type") or "").split(";", 1)[0]
    suffix = Path(urlsplit(url).path).suffix or mimetypes.guess_extension(content_type) or ".bin"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).stem if filename else Path(urlsplit(url).path).stem)[:100]
    stem = stem or hashlib.sha256(url.encode()).hexdigest()[:16]
    safe_suffix = re.sub(r"[^A-Za-z0-9.]", "", Path(filename).suffix if filename else suffix) or ".bin"
    path = DOWNLOAD_DIR / f"{stem}{safe_suffix}"
    path.write_bytes(body)
    return {"downloaded": True, "url": url, "path": str(path), "size_bytes": len(body), "content_type": content_type}
