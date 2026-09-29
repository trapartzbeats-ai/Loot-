"""
ZORO VISION — self-hosted vision service MCP server.

The agent's eyes. Turns images into structured, queryable data — the same
philosophy as LOOT (self-hosted, zero API keys, no SaaS).

Fast deterministic tools (pure PIL/numpy, no models, instant):
  get_metadata  — dimensions, format, mode, EXIF basics
  get_colors    — dominant color palette (k-means-ish via quantization)
  get_layout    — spatial regions: left/center/right thirds, edges, brightness map
  crop          — crop a region, return it as a new file (or just info)
  compare       — perceptual hash similarity between two images

Model-backed tools (local Ollama VLM — babyzoro-vision-v1 preferred (Zoro-trained, pinned), qwen3-vl:4b, moondream):
  describe      — VLM semantic understanding of an image
  read_text     — OCR via the VLM (works for readable text; VLM-based)

Architecture (per the vision-service research paste):
  Agent → ZORO VISION → { Understand: VLM | Measure: colors/layout | Extract: OCR }
  The brain (agent LLM) doesn't need to be multimodal — vision is a service.

Register in Hermes config.yaml:
    mcp_servers:
      vision:
        command: "C:\\Python314\\python.exe"
        args: ["D:/Projects/Hermes/vision/server.py"]

Runtime: FastMCP 3.4.x, Pillow, numpy, and optional local Florence/SmolVLM/Ollama daemons.
Ollama: babyzoro-vision-v1 (primary), qwen3-vl:4b, moondream at http://localhost:11434
"""
from __future__ import annotations

import base64
import io
import importlib.util
import json
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from fastmcp import FastMCP
from PIL import Image

mcp = FastMCP("vision")

# ── Config ──
OLLAMA_URL = "http://127.0.0.1:11434"  # 127.0.0.1 not localhost — host-header routing 404s 'localhost' on this box
VLM_MODELS = ["babyzoro-vision-v1", "qwen3-vl:4b", "moondream"]  # baby zoro first (trained, pinned)
OCR_MODEL = "qwen3-vl:4b"  # qwen reads text far better than moondream
AUTO_MAX_DIM = 1280  # 'auto' resolution downscales the long edge to this (fast main path)

# ── OpenRouter cloud vision fast lane (Ro provisioned 2026-08-12) ──
# DEFAULT is the FREE tier (3s, $0) — Ro's call 2026-08-13: batch image testing,
# free unlimited looks. Paid sub-2s lane (amazon/nova-lite-v1) stays available
# via ZORO_VISION_MODEL env override for speed-critical reads.
_OR_KEY_FILE = Path(os.path.expanduser("~")) / ".config" / "hermes" / "openrouter_key"
_or_key_cache: Optional[str] = None


def _or_key() -> str:
    """OpenRouter key: env first, then the hands-server convention file
    (~/.config/hermes/openrouter_key) so MCP processes launched without the
    env var still get the cloud lane instead of silently skipping it."""
    global _or_key_cache
    if _or_key_cache is not None:
        return _or_key_cache
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        try:
            key = _OR_KEY_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            key = ""
    _or_key_cache = key
    return key
OR_FAST_MODEL = os.environ.get("ZORO_VISION_MODEL", "minimax/minimax-m3:free")
# Free-model slugs churn and rate-limit independently; try these after the primary.
OR_FALLBACK_MODELS = [
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]
OR_PAID_MODEL = "amazon/nova-lite-v1"
WORK_DIR = Path(r"D:\Projects\Hermes\vision\work")  # crops/processed images land here
MAX_DIM = 1600  # downscale images above this before VLM/analysis

# ── Helpers ──

def _work_dir() -> Path:
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    return WORK_DIR


def _load_image(path_or_url: str) -> Image.Image:
    """Load an image from a local path or http(s) URL."""
    s = path_or_url.strip()
    if s.startswith(("http://", "https://")):
        with urllib.request.urlopen(s, timeout=30) as resp:
            data = resp.read()
        return Image.open(io.BytesIO(data))
    p = Path(s)
    if not p.exists():
        raise ValueError(f"Image not found: {s}")
    return Image.open(p)


def _normalize(img: Image.Image) -> Image.Image:
    """Convert to RGB and downscale if huge."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > MAX_DIM:
        scale = MAX_DIM / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def _to_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _prepare(img: Image.Image, resolution: str = "auto") -> Image.Image:
    """VLM image prep: convert to RGB; downscale unless resolution=='full' (or a numeric target)."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    if resolution == "full":
        return img
    target = AUTO_MAX_DIM
    if isinstance(resolution, str) and resolution.isdigit():
        target = int(resolution)
    w, h = img.size
    if max(w, h) > target:
        scale = target / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def _vlm_call(model: str, prompt: str, image: Image.Image, timeout: int = 180, resolution: str = "auto") -> str:
    """Call a local Ollama VLM with an image. Returns text response."""
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "images": [_to_b64(_prepare(image, resolution))],
        "stream": False,
        "options": {"temperature": 0.2},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    text = data.get("response", "").strip()
    if not text:  # thinking-type models put the answer in 'thinking' (babyzoro-vision-v1)
        text = data.get("thinking", "").strip()
    return text


def _or_vlm_call(prompt: str, image: Image.Image, model: str = OR_FAST_MODEL, timeout: int = 60) -> str:
    """Call OpenRouter cloud vision (fast lane, ~2s). Returns text or raises."""
    img = _normalize(image)
    if max(img.size) > 1024:
        img.thumbnail((1024, 1024))
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode()
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}],
        "max_tokens": 512,
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {_or_key()}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (zoro-vision)",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    choice = (data.get("choices") or [{}])[0]
    content = ((choice.get("message") or {}).get("content")) or ""
    return str(content).strip()


def _vlm_chain(prompt: str, image: Image.Image, models: Optional[List[str]] = None, resolution: str = "auto") -> Dict[str, Any]:
    """Cloud fast lane (OpenRouter ~2s) -> SmolVLM2 daemon -> babyzoro-vision-v1 -> qwen3-vl -> moondream."""
    # Fast lane: OpenRouter vision when key configured and no explicit local model requested.
    # (read_text passes models=[OCR_MODEL] so OCR stays local/Florence; describe uses the cloud.)
    if _or_key() and models is None:
        for or_model in [OR_FAST_MODEL] + OR_FALLBACK_MODELS:
            try:
                text = _or_vlm_call(prompt, image, model=or_model)
                if text:
                    return {"model": f"OpenRouter {or_model}", "text": text}
            except Exception:
                continue

    # Fast tier: resident SmolVLM2 daemon on 127.0.0.1:3211 — ~10s vs 90s.
    # Use a unique file so concurrent MCP clients do not overwrite one another.
    tmp_path = None
    try:
        import urllib.request as _ur
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png", dir=_work_dir()) as tmp:
            tmp_path = Path(tmp.name)
        _prepare(image, resolution).save(tmp_path, format="PNG")
        body = json.dumps({"image": str(tmp_path), "prompt": prompt}).encode()
        req = _ur.Request(
            "http://127.0.0.1:3211/describe",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with _ur.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        if data.get("text"):
            return {"model": "SmolVLM2-256M (fast)", "text": data["text"]}
    except Exception:
        pass
    finally:
        if tmp_path:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    last_err = ""
    local_tried = False
    for model in models or VLM_MODELS:
        local_tried = True
        try:
            return {"model": model, "text": _vlm_call(model, prompt, image, resolution=resolution)}
        except Exception as e:
            last_err = str(e)[:200]
    # Cloud last resort: pinned-model chains (e.g. read_text's OCR) never saw
    # the fast lane, so give them one OpenRouter shot before giving up.
    if local_tried and _or_key():
        for or_model in [OR_FAST_MODEL] + OR_FALLBACK_MODELS:
            try:
                text = _or_vlm_call(prompt, image, model=or_model)
                if text:
                    return {"model": f"OpenRouter {or_model} (fallback)", "text": text}
            except Exception as e:
                last_err = str(e)[:200]
    return {"model": None, "text": "", "error": last_err}


# ── Fast deterministic tools ──

@mcp.tool(
    name="screenshot",
    description=(
        "Capture a screenshot of the screen (full desktop, a specific window by "
        "title, or a pixel region) and save it. Returns the saved path + size so "
        "other vision tools (describe, get_colors, get_layout, read_text) can "
        "analyze it. Mode: 'full' | 'window' | 'region'. For window, pass target "
        "(partial title match). For region, pass x, y, width, height. "
        "Delegates to the canonical Zoro screenshot.py (D:\\Projects\\Zoro\\scripts), "
        "saves to ~/.screenshots/."
    ),
)
def screenshot(
    mode: str = "full",
    target: str = "",
    x: int = 0,
    y: int = 0,
    width: int = 0,
    height: int = 0,
    out_name: str = "",
) -> Dict[str, Any]:
    """Capture through the import-safe canonical Zoro screenshot module."""
    script = r"D:\Projects\Zoro\scripts\screenshot.py"
    if not os.path.exists(script):
        script = os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", "scripts", "screenshot.py")

    if not os.path.exists(script):
        raise ValueError("Canonical screenshot.py was not found")
    spec = importlib.util.spec_from_file_location("zoro_canonical_screenshot", script)
    if spec is None or spec.loader is None:
        raise ValueError("Could not load canonical screenshot module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if mode == "window":
        if not target:
            raise ValueError("mode='window' requires target (window title, partial match)")
        region = None
    elif mode == "region":
        if width <= 0 or height <= 0:
            raise ValueError("mode='region' requires width and height > 0")
        region = [x, y, x + width, y + height]
    else:
        region = None
    path = module.capture(mode=mode, target=target, region=region, out_name=out_name)
    if not os.path.exists(path):
        raise ValueError(f"Screenshot reported {path} but file not found")
    img = Image.open(path)
    return {
        "saved_to": path,
        "width": img.width,
        "height": img.height,
        "mode": mode,
        "analyze_with": ["describe", "get_colors", "get_layout", "read_text", "get_metadata"],
    }


@mcp.tool(
    name="get_metadata",
    description="Return image dimensions, format, mode, file size, and basic EXIF (orientation, datetime).",
)
def get_metadata(image: str) -> Dict[str, Any]:
    """Image metadata: size, format, mode, EXIF basics."""
    img = _load_image(image)
    exif = {}
    try:
        raw = img.getexif()
        if raw:
            for tag in (274, 306, 36867):  # Orientation, DateTime, DateTimeOriginal
                if tag in raw:
                    from PIL.ExifTags import TAGS
                    exif[TAGS.get(tag, str(tag))] = str(raw[tag])
    except Exception:
        pass
    size = 0
    if not image.startswith(("http://", "https://")):
        try:
            size = Path(image).stat().st_size
        except Exception:
            pass
    return {
        "width": img.width,
        "height": img.height,
        "format": img.format,
        "mode": img.mode,
        "file_size_bytes": size,
        "exif": exif,
        "aspect_ratio": round(img.width / img.height, 4) if img.height else None,
    }


@mcp.tool(
    name="get_colors",
    description=(
        "Extract the dominant color palette from an image. Returns top N colors as "
        "hex + RGB + approximate percentage coverage. Optionally a named description "
        "of each color."
    ),
)
def get_colors(image: str, count: int = 8) -> Dict[str, Any]:
    """Dominant colors via quantization (PIL) — fast, no model."""
    count = max(1, min(int(count), 256))
    img = _normalize(_load_image(image))
    small = img.resize((64, 64), Image.BILINEAR)
    quantized = small.quantize(colors=count, method=Image.MEDIANCUT)
    palette = quantized.getpalette()
    counts = sorted(quantized.getcolors(), reverse=True)
    total = sum(c for c, _ in counts)
    colors = []
    for c, idx in counts:
        r, g, b = palette[idx * 3: idx * 3 + 3]
        colors.append({
            "hex": f"#{r:02x}{g:02x}{b:02x}",
            "rgb": [r, g, b],
            "percent": round(c / total * 100, 1),
        })
    return {"count": len(colors), "colors": colors}


@mcp.tool(
    name="get_layout",
    description=(
        "Analyze image spatial layout without a model: brightness per region "
        "(left/center/right, top/middle/bottom thirds), edge density, and simple "
        "composition hints (bright areas, dark areas, centered subject heuristic)."
    ),
)
def get_layout(image: str) -> Dict[str, Any]:
    """Spatial layout: region brightness, edge density, composition hints."""
    img = _normalize(_load_image(image))
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    h, w = gray.shape

    # 3x3 region brightness (0-255)
    grid = {}
    for ri, rname in enumerate(["top", "middle", "bottom"]):
        for ci, cname in enumerate(["left", "center", "right"]):
            region = gray[h * ri // 3:h * (ri + 1) // 3, w * ci // 3:w * (ci + 1) // 3]
            grid[f"{rname}_{cname}"] = round(float(region.mean()), 1)

    # Edge density via simple gradient
    gx = np.abs(np.diff(gray, axis=1)).mean()
    gy = np.abs(np.diff(gray, axis=0)).mean()
    edge_density = round(float((gx + gy) / 2), 2)

    # Contrast: stddev of luma
    contrast = round(float(gray.std()), 1)

    # Composition hints
    thirds = [grid["top_left"], grid["top_center"], grid["top_right"],
              grid["middle_left"], grid["middle_center"], grid["middle_right"],
              grid["bottom_left"], grid["bottom_center"], grid["bottom_right"]]
    brightest = max(thirds, key=lambda v: v)
    darkest = min(thirds, key=lambda v: v)
    # find brightest region name
    names = ["top_left", "top_center", "top_right",
             "middle_left", "middle_center", "middle_right",
             "bottom_left", "bottom_center", "bottom_right"]
    hints = []
    if brightest == grid["middle_center"] and contrast > 40:
        hints.append("centered bright subject")
    if grid["bottom_left"] < 40 and grid["bottom_right"] < 40:
        hints.append("dark bottom edge (vignette/silhouette)")
    if grid["top_center"] > 150:
        hints.append("bright top (sky/light source)")

    return {
        "size": [w, h],
        "regions_3x3": grid,
        "edge_density": edge_density,
        "contrast_stddev": contrast,
        "mean_luma": round(float(gray.mean()), 1),
        "brightest_region": names[thirds.index(brightest)],
        "darkest_region": names[thirds.index(darkest)],
        "hints": hints,
    }


@mcp.tool(
    name="crop",
    description=(
        "Crop an image to a region (x, y, width, height in pixels, 0-based from "
        "top-left) and save the result to a work dir. Returns the saved path + size. "
        "Optionally pass out_name for a stable filename."
    ),
)
def crop(image: str, x: int, y: int, width: int, height: int, out_name: str = "") -> Dict[str, Any]:
    """Crop a region and save it."""
    img = _load_image(image)
    w, h = img.size
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be greater than zero")
    x2 = min(x + width, w)
    y2 = min(y + height, h)
    if x < 0 or y < 0 or x >= w or y >= h:
        raise ValueError(f"Invalid crop region ({x},{y},{width},{height}) for {w}x{h} image")
    region = img.crop((x, y, x2, y2))
    safe_name = "".join(char if char.isalnum() or char in "._-" else "_" for char in Path(out_name).name)[:120]
    name = safe_name or f"crop_{x}_{y}_{width}x{height}.png"
    if not Path(name).suffix:
        name += ".png"
    path = _work_dir() / name
    region.save(path)
    return {"saved_to": str(path), "width": region.width, "height": region.height, "region": [x, y, x2, y2]}


@mcp.tool(
    name="compare",
    description=(
        "Compare two images using perceptual hash (dHash). Returns similarity 0-1 "
        "(1 = identical), Hamming distance, and whether they're 'same' (>= threshold)."
    ),
)
def compare(image_a: str, image_b: str, threshold: float = 0.85) -> Dict[str, Any]:
    """Perceptual hash similarity between two images (no model)."""
    def dhash(img: Image.Image, hash_size: int = 16) -> int:
        img = img.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
        arr = np.asarray(img, dtype=np.int32)
        diff = arr[:, 1:] > arr[:, :-1]
        bits = diff.flatten()
        return sum(1 << i for i, b in enumerate(bits) if b)

    h1 = dhash(_normalize(_load_image(image_a)))
    h2 = dhash(_normalize(_load_image(image_b)))
    hamming = bin(h1 ^ h2).count("1")
    bits = 16 * 16
    similarity = 1.0 - hamming / bits
    return {
        "similarity": round(similarity, 4),
        "hamming_distance": hamming,
        "bit_count": bits,
        "same": similarity >= threshold,
    }


@mcp.tool(
    name="read_text",
    description=(
        "OCR an image with the local VLM (Florence-2 fast lane first, then VLM). "
        "Returns the detected text. Prefer this on UI screenshots, covers, text-heavy "
        "images. resolution='auto' (default, MAIN): downscales to 1280px for speed. "
        "resolution='full': sends the original pixels (slower on big images, better for "
        "tiny text)."
    ),
)
def read_text(image: str, resolution: str = "auto") -> Dict[str, Any]:
    """OCR. Florence-2 daemon first (fast, ~1-3s), local VLM fallback (slow)."""
    # Fast path: Florence-2 OCR daemon
    try:
        import urllib.request
        body = json.dumps({"image": image}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:3212/ocr", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        txt = data.get("result", "")
        if txt:
            return {
                "text": txt,
                "model": "Florence-2-base (fast)",
                "time_s": data.get("time_s"),
                "has_text": bool(txt.strip()),
            }
    except Exception:
        pass

    # Slow path: VLM chain
    img = _load_image(image)
    result = _vlm_chain(
        "Transcribe ALL text visible in this image. Output exactly the text, "
        "nothing else. If no text, say 'NO TEXT'.",
        img,
        models=[OCR_MODEL],
        resolution=resolution,
    )
    if result.get("error"):
        result = _vlm_chain(
            "Transcribe ALL text visible in this image. Output exactly the text, nothing else.",
            img,
            resolution=resolution,
        )
    text = result.get("text", "")
    return {
        "text": text,
        "model": result.get("model"),
        "has_text": text.strip() and text.strip().upper() != "NO TEXT",
    }


@mcp.tool(
    name="describe",
    description=(
        "Semantic understanding of an image via local VLM. Returns a natural-language "
        "description. Pass a prompt for a targeted question (e.g. 'What UI elements are "
        "in this screenshot?'). Models tried in order: babyzoro-vision-v1, qwen3-vl:4b, "
        "moondream. resolution='auto' (default, MAIN): downscales to 1280px for speed. "
        "resolution='full': sends original pixels (best for fine detail/OCR-heavy art, "
        "slower)."
    ),
)
def describe(image: str, prompt: str = "Describe this image in detail.", resolution: str = "auto") -> Dict[str, Any]:
    """VLM semantic understanding."""
    img = _load_image(image)
    result = _vlm_chain(prompt, img, resolution=resolution)
    return {
        "description": result.get("text", ""),
        "model": result.get("model"),
        "error": result.get("error"),
    }


@mcp.tool(
    name="palette_ui",
    app=True,
    description="Visual color palette UI for an image: rendered swatches + hex codes in an interactive grid (FastMCP v3 Apps).",
)
def palette_ui(image: str):
    """Render dominant colors as a visual palette UI."""
    from prefab_ui.app import PrefabApp
    from prefab_ui.components import Column, Grid, Text, Div

    img = _normalize(_load_image(image))
    small = img.resize((64, 64), Image.BILINEAR)
    quantized = small.quantize(colors=6, method=Image.MEDIANCUT)
    palette = quantized.getpalette()
    counts = sorted(quantized.getcolors(), reverse=True)
    total = sum(c for c, _ in counts)
    colors = []
    for c, idx in counts:
        r, g, b = palette[idx * 3 : idx * 3 + 3]
        colors.append({"hex": f"#{r:02x}{g:02x}{b:02x}", "percent": round(c / total * 100, 1)})

    with PrefabApp() as app:
        with Column(gap=3, css_class="p-4"):
            Text("Dominant Colors", size="lg", weight="bold")
            with Grid(columns=3, gap=3):
                for c in colors:
                    hexv, pct = c["hex"], c["percent"]
                    with Div(style={"background": hexv, "height": "64px", "border-radius": "8px"}):
                        pass
                    Text(f"{hexv}  {pct}%", size="sm")
    return app


# ═══ Video watching tools (merged from Video Eyes, 2026-08-14) ═══
# Frame/scene/audio extraction so vision can "watch" video files too.

import subprocess as _subprocess
import re as _re
from datetime import timedelta as _timedelta

VIDEO_SCREENSHOTS_DIR = os.path.join(
    os.environ.get("USERPROFILE", r"C:\Users\User"), ".screenshots"
)
os.makedirs(VIDEO_SCREENSHOTS_DIR, exist_ok=True)
VIDEO_SESSIONS_FILE = os.path.join(VIDEO_SCREENSHOTS_DIR, "vision_video_sessions.json")
_video_sessions: Dict[str, Any] = {}
_video_sessions_loaded = False
VIDEO_CONTACT_COLS = 4


def _ffmpeg() -> str:
    candidates = [
        "ffmpeg",
        "/c/Users/User/AppData/Local/Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-8.1.1-full_build/bin/ffmpeg.exe",
        os.path.join(
            os.environ.get("USERPROFILE", r"C:\Users\User"),
            "AppData", "Local", "Microsoft", "WinGet", "Packages",
            "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe",
            "ffmpeg-8.1.1-full_build", "bin", "ffmpeg.exe",
        ),
    ]
    for c in candidates:
        try:
            r = _subprocess.run([c, "-version"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return c
        except (FileNotFoundError, _subprocess.TimeoutExpired):
            continue
    return "ffmpeg"


def _ffprobe() -> str:
    ff = _ffmpeg()
    if os.sep in ff or "/" in ff:
        base = os.path.dirname(ff)
        for name in ("ffprobe.exe", "ffprobe"):
            p = os.path.join(base, name)
            if os.path.exists(p):
                return p
    return "ffprobe"


def _video_metadata(path: str) -> Dict[str, Any]:
    cmd = [
        _ffprobe(), "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    try:
        result = _subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return {"error": f"ffprobe failed: {result.stderr.strip()}"}
        data = json.loads(result.stdout)
        video_stream = None
        audio_stream = None
        subtitle_streams = []
        for stream in data.get("streams", []):
            t = stream.get("codec_type")
            if t == "video" and video_stream is None:
                video_stream = stream
            elif t == "audio" and audio_stream is None:
                audio_stream = stream
            elif t == "subtitle":
                subtitle_streams.append({
                    "index": stream.get("index"),
                    "codec": stream.get("codec_name", "unknown"),
                    "language": stream.get("tags", {}).get("language", "unknown"),
                    "title": stream.get("tags", {}).get("title", ""),
                })
        fmt = data.get("format", {})
        try:
            duration = float(fmt.get("duration", "0"))
        except (ValueError, TypeError):
            duration = 0.0
        metadata = {
            "filename": os.path.basename(path),
            "path": path,
            "duration": duration,
            "duration_str": str(_timedelta(seconds=int(duration))) if duration else "unknown",
            "size": fmt.get("size", "unknown"),
        }
        if video_stream:
            metadata["video"] = {
                "codec": video_stream.get("codec_name", "unknown"),
                "width": video_stream.get("width", 0),
                "height": video_stream.get("height", 0),
                "fps": video_stream.get("r_frame_rate", "0/1"),
            }
        if audio_stream:
            metadata["audio"] = {
                "codec": audio_stream.get("codec_name", "unknown"),
                "channels": audio_stream.get("channels", 0),
            }
        if subtitle_streams:
            metadata["subtitles"] = subtitle_streams
        return metadata
    except _subprocess.TimeoutExpired:
        return {"error": "ffprobe timed out"}
    except Exception as e:
        return {"error": str(e)}


def _video_extract_frame(path: str, timestamp: float, output_path: str, precise: bool = False) -> str | None:
    ffmpeg_bin = _ffmpeg()
    if precise:
        cmd = [ffmpeg_bin, "-y", "-i", path, "-ss", str(timestamp), "-vframes", "1", "-q:v", "2", output_path]
    else:
        cmd = [ffmpeg_bin, "-y", "-ss", str(timestamp), "-i", path, "-vframes", "1", "-q:v", "2", output_path]
    try:
        r = _subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return output_path if r.returncode == 0 and os.path.exists(output_path) else None
    except _subprocess.TimeoutExpired:
        return None


def _video_session(sid: str = "default") -> Dict[str, Any]:
    global _video_sessions_loaded
    if not _video_sessions_loaded:
        _video_sessions_loaded = True
        try:
            if os.path.exists(VIDEO_SESSIONS_FILE):
                with open(VIDEO_SESSIONS_FILE, "r", encoding="utf-8") as f:
                    _video_sessions.update(json.load(f))
        except Exception:
            pass
    if sid not in _video_sessions:
        _video_sessions[sid] = {"path": None, "cursor": 0.0, "metadata": {}, "duration": 0.0}
    return _video_sessions[sid]


def _video_save_session() -> None:
    try:
        with open(VIDEO_SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(_video_sessions, f, indent=2, default=str)
    except Exception:
        pass


@mcp.tool(
    name="video_open",
    description=(
        "Load a video file and extract its metadata (duration, resolution, fps, "
        "codecs, subtitle streams). Use this first to set up video watching. "
        "Merged from Video Eyes — makes vision able to watch video files."
    ),
)
def video_open(path: str) -> Dict[str, Any]:
    """Open a video and get metadata."""
    if not os.path.exists(path):
        return {"error": f"File not found: {path}"}
    metadata = _video_metadata(path)
    if "error" in metadata:
        return metadata
    session = _video_session()
    session["path"] = path
    session["cursor"] = 0.0
    session["metadata"] = metadata
    session["duration"] = metadata.get("duration", 0.0)
    _video_save_session()
    return {"success": True, "loaded": os.path.basename(path), **metadata}


@mcp.tool(
    name="video_frame",
    description=(
        "Extract a single frame from the loaded video at a timestamp (seconds). "
        "precise=True uses frame-accurate seeking (slower). Saves to ~/.screenshots/ "
        "and returns the path for vision analysis."
    ),
)
def video_frame(timestamp: float, output_name: str = "", precise: bool = False) -> Dict[str, Any]:
    """Extract a single frame at a timestamp."""
    session = _video_session()
    if not session["path"]:
        return {"error": "No video loaded. Call video_open first."}
    duration = session["duration"]
    if duration > 0 and timestamp > duration:
        return {"error": f"Timestamp {timestamp}s exceeds duration {duration:.1f}s"}
    ts = max(0.0, timestamp)
    name = output_name or f"frame_{int(ts)}_{Path(session['path']).stem}"
    out = os.path.join(VIDEO_SCREENSHOTS_DIR, f"{name}.png")
    result = _video_extract_frame(session["path"], ts, out, precise)
    if result:
        session["cursor"] = ts
        _video_save_session()
        return {
            "success": True,
            "frame_path": out,
            "timestamp": ts,
            "timestamp_str": str(_timedelta(seconds=int(ts))),
            "progress": f"{ts / duration * 100:.1f}%" if duration > 0 else "unknown",
        }
    return {"error": "Failed to extract frame"}


@mcp.tool(
    name="video_extract",
    description=(
        "Extract frames at regular intervals from the loaded video — a visual "
        "overview. Each frame is saved to ~/.screenshots/<stem>_batch/ for "
        "vision analysis."
    ),
)
def video_extract(interval: float = 5.0, start: Optional[float] = None, end: Optional[float] = None, limit: int = 20) -> Dict[str, Any]:
    """Extract frames at regular intervals."""
    session = _video_session()
    if not session["path"]:
        return {"error": "No video loaded. Call video_open first."}
    duration = session["duration"]
    s = start if start is not None else session["cursor"]
    e = end if end is not None else duration
    s, e = max(0.0, s), (e if 0 < e <= duration else duration)
    timestamps = []
    t = s
    while t <= e and len(timestamps) < limit:
        timestamps.append(t)
        t += interval
    if not timestamps:
        return {"error": "No frames to extract (bad range)."}
    out_dir = os.path.join(VIDEO_SCREENSHOTS_DIR, f"{Path(session['path']).stem}_batch")
    os.makedirs(out_dir, exist_ok=True)
    frames = []
    for ts in timestamps:
        out = os.path.join(out_dir, f"frame_{int(ts)}.png")
        if _video_extract_frame(session["path"], ts, out):
            frames.append({"timestamp": ts, "timestamp_str": str(_timedelta(seconds=int(ts))), "frame_path": out})
    session["cursor"] = timestamps[-1]
    _video_save_session()
    return {"success": True, "count": len(frames), "output_dir": out_dir, "frames": frames}


@mcp.tool(
    name="video_scenes",
    description=(
        "Extract frames at DETECTED SHOT BOUNDARIES (scene changes) — smarter "
        "than fixed intervals for understanding a video's structure. threshold "
        "0.0-1.0, lower = more sensitive (more scenes)."
    ),
)
def video_scenes(threshold: float = 0.4, limit: int = 24, start: Optional[float] = None, end: Optional[float] = None) -> Dict[str, Any]:
    """Extract frames at detected scene changes."""
    session = _video_session()
    if not session["path"]:
        return {"error": "No video loaded. Call video_open first."}
    vid = session["path"]
    duration = session["duration"]
    s = max(0.0, start if start is not None else 0.0)
    e = end if end is not None else duration
    e = e if 0 < e <= duration else duration
    cmd = [
        _ffmpeg(), "-v", "quiet", "-y",
        "-ss", str(s), "-i", vid, "-t", str(max(0.0, e - s)),
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    try:
        result = _subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except _subprocess.TimeoutExpired:
        return {"error": "scene detection timed out"}
    times = []
    for line in result.stderr.splitlines():
        m = _re.search(r"pts_time:([0-9.]+)", line)
        if m:
            times.append(float(m.group(1)))
    if not times:
        times = [0.0]
    times = sorted(set(round(t, 2) for t in times))
    times = [t for t in times if s <= t <= e][:limit]
    out_dir = os.path.join(VIDEO_SCREENSHOTS_DIR, f"{Path(vid).stem}_scenes")
    os.makedirs(out_dir, exist_ok=True)
    frames = []
    for i, ts in enumerate(times):
        out = os.path.join(out_dir, f"scene_{i:02d}_{int(ts)}.png")
        if _video_extract_frame(vid, ts, out):
            frames.append({"scene": i + 1, "timestamp": ts, "timestamp_str": str(_timedelta(seconds=int(ts))), "frame_path": out})
    session["cursor"] = times[-1] if times else session["cursor"]
    _video_save_session()
    return {"success": True, "scenes_detected": len(times), "frames_extracted": len(frames), "frames": frames}


@mcp.tool(
    name="video_contact_sheet",
    description=(
        "Build a CONTACT SHEET — one grid image with N evenly-spaced frames. "
        "The most efficient way to see a whole video: one image, one vision "
        "analysis call, full structure. Perfect for reels/promos."
    ),
)
def video_contact_sheet(frames: int = 12, width: int = 1280, start: Optional[float] = None, end: Optional[float] = None) -> Dict[str, Any]:
    """Build a contact sheet grid image."""
    session = _video_session()
    if not session["path"]:
        return {"error": "No video loaded. Call video_open first."}
    vid = session["path"]
    duration = session["duration"]
    frames = max(1, min(40, frames))
    s = max(0.0, start if start is not None else 0.0)
    e = end if end is not None else duration
    e = e if 0 < e <= duration else duration
    span = max(0.1, e - s)
    timestamps = [s + span * (i / max(1, frames - 1)) for i in range(frames)]
    cols = VIDEO_CONTACT_COLS
    rows = (frames + cols - 1) // cols
    select_expr = "+".join(f"gte(t,{ts - 0.1})*lte(t,{ts + 0.1})" for ts in timestamps)
    out = os.path.join(VIDEO_SCREENSHOTS_DIR, f"{Path(vid).stem}_contact.png")
    cmd = [
        _ffmpeg(), "-y", "-i", vid,
        "-vf", f"select='{select_expr}',setpts=N/FRAME_RATE/TB,tile={cols}x{rows}:padding=4:margin=4",
        "-frames:v", "1", "-q:v", "2", out,
    ]
    try:
        r = _subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and os.path.exists(out):
            session["cursor"] = timestamps[-1]
            _video_save_session()
            return {"success": True, "contact_sheet_path": out, "frames": frames, "grid": f"{cols}x{rows}"}
        return {"error": f"contact sheet failed: {r.stderr.strip()[-300:]}"}
    except _subprocess.TimeoutExpired:
        return {"error": "contact sheet timed out"}


@mcp.tool(
    name="video_audio",
    description=(
        "Extract the AUDIO track from the loaded video at a time range — ready "
        "for transcription (faster-whisper / LyricAlign / meeting-ear). Returns "
        "a WAV path."
    ),
)
def video_audio(start: float = 0.0, end: Optional[float] = None, output_name: str = "", format: str = "wav") -> Dict[str, Any]:
    """Extract audio at a time range."""
    session = _video_session()
    if not session["path"]:
        return {"error": "No video loaded. Call video_open first."}
    vid = session["path"]
    duration = session["duration"]
    e = end if end is not None else duration
    e = e if 0 < e <= duration else duration
    if e <= start:
        return {"error": "end must be after start"}
    ext = format if format in ("wav", "mp3", "m4a", "flac") else "wav"
    name = output_name or f"{Path(vid).stem}_audio"
    out = os.path.join(VIDEO_SCREENSHOTS_DIR, f"{name}.{ext}")
    cmd = [_ffmpeg(), "-y", "-ss", str(start), "-i", vid, "-t", str(e - start), "-vn"]
    if ext == "wav":
        cmd += ["-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2"]
    else:
        cmd += ["-q:a", "2"]
    cmd.append(out)
    try:
        r = _subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and os.path.exists(out):
            return {"success": True, "audio_path": out, "duration_secs": round(e - start, 2), "size_bytes": os.path.getsize(out), "format": ext}
        return {"error": f"audio extract failed: {r.stderr.strip()[-300:]}"}
    except _subprocess.TimeoutExpired:
        return {"error": "audio extract timed out"}


@mcp.tool(
    name="video_cut",
    description=(
        "Extract a short VIDEO+AUDIO clip from the loaded video (fast remux, "
        "no re-encode). Useful for isolating a moment."
    ),
)
def video_cut(start: float, end: Optional[float] = None, duration: Optional[float] = None, output_name: str = "") -> Dict[str, Any]:
    """Extract a video+audio clip."""
    session = _video_session()
    if not session["path"]:
        return {"error": "No video loaded. Call video_open first."}
    vid = session["path"]
    vid_dur = session["duration"]
    s = max(0.0, start)
    e = end if end is not None else (s + duration if duration else vid_dur)
    if e <= s:
        return {"error": "end must be after start"}
    if e > vid_dur:
        e = vid_dur
    name = output_name or f"{Path(vid).stem}_cut"
    out = os.path.join(VIDEO_SCREENSHOTS_DIR, f"{name}.mp4")
    cmd = [_ffmpeg(), "-y", "-ss", str(s), "-i", vid, "-t", str(e - s), "-c", "copy", "-avoid_negative_ts", "make_zero", out]
    try:
        r = _subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and os.path.exists(out):
            return {"success": True, "clip_path": out, "duration_secs": round(e - s, 2), "size_bytes": os.path.getsize(out)}
        return {"error": f"cut failed: {r.stderr.strip()[-300:]}"}
    except _subprocess.TimeoutExpired:
        return {"error": "cut timed out"}


@mcp.tool(
    name="video_subtitles",
    description=(
        "Extract embedded SUBTITLE streams from the loaded video (if any) as "
        ".srt files. Gets dialogue/text without watching."
    ),
)
def video_subtitles() -> Dict[str, Any]:
    """Extract embedded subtitle streams."""
    session = _video_session()
    if not session["path"]:
        return {"error": "No video loaded. Call video_open first."}
    subs = session.get("metadata", {}).get("subtitles", [])
    if not subs:
        return {"error": "No embedded subtitle streams found."}
    out = []
    for s in subs:
        idx = s["index"]
        lang = s.get("language", "und")
        path_out = os.path.join(VIDEO_SCREENSHOTS_DIR, f"{Path(session['path']).stem}_{lang}_{idx}.srt")
        cmd = [_ffmpeg(), "-y", "-i", session["path"], "-map", f"0:{idx}", "-c:s", "srt", path_out]
        try:
            r = _subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and os.path.exists(path_out):
                out.append({"language": lang, "index": idx, "srt_path": path_out})
        except _subprocess.TimeoutExpired:
            continue
    if not out:
        return {"error": "subtitle extraction failed for all streams"}
    return {"success": True, "subtitles": out}


@mcp.tool(
    name="video_status",
    description=(
        "Show the loaded video + cursor position for the current video session."
    ),
)
def video_status() -> Dict[str, Any]:
    """Show video session status."""
    session = _video_session()
    if not session["path"]:
        return {"error": "No video loaded."}
    return {
        "video": session.get("metadata", {}).get("filename", Path(session["path"]).name),
        "duration": session.get("duration", 0.0),
        "cursor": session.get("cursor", 0.0),
        "progress": f"{session['cursor'] / session['duration'] * 100:.1f}%" if session.get("duration") else "unknown",
    }


@mcp.tool(
    name="video_sessions",
    description=(
        "Manage video sessions — list, switch, clear. Sessions persist across "
        "restarts so cursors survive."
    ),
)
def video_sessions(action: str = "list", session_id: str = "") -> Dict[str, Any]:
    """Manage video sessions."""
    if action == "list":
        return {"sessions": [{"session": sid, "video": os.path.basename(s.get("path")) if s.get("path") else None,
                              "cursor": s.get("cursor", 0.0)} for sid, s in _video_sessions.items()]}
    if action == "switch" and session_id:
        _video_session(session_id)
        return {"success": True, "session": session_id}
    if action == "clear" and session_id:
        _video_sessions.pop(session_id, None)
        _video_save_session()
        return {"success": True, "cleared": session_id}
    if action == "clear_all":
        _video_sessions.clear()
        _video_save_session()
        return {"success": True, "cleared_all": True}
    return {"error": f"unknown action {action}"}


@mcp.resource("video://meta/{path}")
def video_meta_resource(path: str) -> str:
    """Addressable video metadata: video://meta/<abs path> (JSON)."""
    full = path if os.path.isabs(path) else os.path.join(VIDEO_SCREENSHOTS_DIR, path)
    if not os.path.exists(full):
        return json.dumps({"path": full, "error": "File not found"})
    meta = _video_metadata(full)
    if "error" in meta:
        return json.dumps({"path": full, "error": meta["error"]})
    return json.dumps({"path": full, **meta}, indent=2)


def main() -> None:
    # FastMCP v3 stdio is the Hermes default. HTTP is loopback unless --host is explicit.
    import sys as _sys
    _http = "--http" in _sys.argv
    _port = 8000
    _host = "127.0.0.1"
    if "--port" in _sys.argv:
        try:
            _port = int(_sys.argv[_sys.argv.index("--port") + 1])
        except Exception:
            pass
    if "--host" in _sys.argv:
        try:
            _host = _sys.argv[_sys.argv.index("--host") + 1]
        except Exception:
            pass
    if _http:
        mcp.run(transport="http", host=_host, port=_port, show_banner=False)
    else:
        mcp.run(show_banner=False)


# ═══ FastMCP v3 application-platform features ═══


@mcp.resource("vision://image/{path}")
def image_resource(path: str) -> str:
    """Addressable image metadata: vision://image/<relative or abs path>.

    Returns dimensions, format, mode, and a data URI for the downscaled
    preview. Lets agents reference images as data sources.
    """
    import os as _os
    from PIL import Image as _PIL
    import base64 as _b64

    full = path if _os.path.isabs(path) else _os.path.join(str(WORK_DIR), path)
    if not _os.path.exists(full):
        return json.dumps({"path": full, "error": "File not found"})
    img = _PIL.open(full)
    small = img.copy()
    small.thumbnail((512, 512))
    buf = __import__("io").BytesIO()
    small.save(buf, format="PNG")
    data_uri = f"data:image/png;base64,{_b64.b64encode(buf.getvalue()).decode()}"
    return json.dumps({
        "path": full,
        "width": img.width,
        "height": img.height,
        "format": img.format,
        "mode": img.mode,
        "preview": data_uri,
    })


@mcp.prompt
def analyze_logo(path: str) -> str:
    """Reusable vision template: brand/logo analysis."""
    return (
        f"Analyze the image at {path} as a brand asset:\n"
        "1. Dominant colors (use get_colors)\n"
        "2. Layout and composition (use get_layout)\n"
        "3. Text/OCR present (use read_text)\n"
        "4. Overall impression and potential use cases"
    )


@mcp.prompt
def compare_screenshots(path_a: str, path_b: str) -> str:
    """Reusable vision template: compare two screenshots/images."""
    return (
        f"Compare these two images: {path_a} and {path_b}\n"
        "1. Similarity score (use compare)\n"
        "2. Key visual differences\n"
        "3. Which is cleaner/more polished and why"
    )


@mcp.tool(
    name="image_inspector",
    app=True,
    description=(
        "Open the interactive Image Inspector: renders the image with an overlay "
        "of detected regions (brightness zones) plus color swatches and layout "
        "data side by side (FastMCP v3 Apps)."
    ),
)
def image_inspector(path: str):
    """Interactive image inspector: annotated overlay + palette + layout."""
    from prefab_ui.app import PrefabApp
    from prefab_ui.components import Column, Div, Grid, Text

    meta = get_metadata(path)
    layout = get_layout(path)
    colors = get_colors(path, count=6)
    img = Image.open(path) if __import__("os").path.exists(path) else None
    w, h = (img.width, img.height) if img else (0, 0)
    scale = 400 / max(w, h) if w and h else 1
    dw, dh = int(w * scale), int(h * scale)
    preview = _to_b64(_normalize(img)) if img else ""
    zone_names = [
        "top_left", "top_center", "top_right", "middle_left", "middle_center", "middle_right",
        "bottom_left", "bottom_center", "bottom_right",
    ]
    region_values = layout.get("regions_3x3", {})
    zones = [
        {"x": (index % 3) * 100 / 3, "y": (index // 3) * 100 / 3,
         "w": 100 / 3, "h": 100 / 3, "brightness": region_values.get(name, 0)}
        for index, name in enumerate(zone_names)
    ]

    with PrefabApp() as app:
        with Column(gap=3, css_class="p-4"):
            Text(f"Image Inspector — {meta.get('width')}x{meta.get('height')}", size="lg", weight="bold")
            # annotated overlay: dark div representing the image + region overlay grid
            with Div(style={"position": "relative", "width": f"{dw}px", "height": f"{dh}px",
                            "background": f"#222 url(data:image/png;base64,{preview}) center/contain no-repeat",
                            "border-radius": "8px", "overflow": "hidden"}):
                # 3x3 brightness zones as translucent boxes
                for z in zones[:9]:
                    rx = z.get("x", 0) / 100 * dw
                    ry = z.get("y", 0) / 100 * dh
                    rw = z.get("w", 33) / 100 * dw
                    rh = z.get("h", 33) / 100 * dh
                    bright = z.get("brightness", 0)
                    with Div(style={
                        "position": "absolute", "left": f"{rx}px", "top": f"{ry}px",
                        "width": f"{rw}px", "height": f"{rh}px",
                        "background": f"rgba(255,255,255,{min(bright / 255 * 0.3, 0.3):.2f})",
                        "border": "1px solid #54e878", "box-sizing": "border-box",
                    }):
                        pass
            # palette swatches
            with Grid(columns=6, gap=2):
                for c in colors.get("colors", []):
                    with Div(style={"background": c["hex"], "height": "24px", "border-radius": "4px"}):
                        pass
            Text(f"Layout: {', '.join(layout.get('hints', [])) or 'no strong heuristic'}  |  {meta.get('width')}x{meta.get('height')} {meta.get('format')}", size="sm")
    return app


if __name__ == "__main__":
    main()
