from __future__ import annotations

import base64
import hashlib
import io
import json
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image

from config import OLLAMA_URL, SMOLVLM_URL, FLORENCE_URL, VLM_MODELS, OCR_MODEL, MAX_IMAGE_DIM, DATA_DIR


def _florence_post(endpoint: str, payload: dict, timeout: int = 60) -> Optional[Dict[str, Any]]:
    """Call the Florence-2 daemon (fast OCR/detect/grounding tier). None on failure."""
    try:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{FLORENCE_URL}/{endpoint}", data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _florence_ocr(image: str) -> Optional[Dict[str, Any]]:
    """Fast OCR via Florence-2 daemon (~5-25s CPU). None on failure."""
    return _florence_post("ocr", {"image": image})


def _load_image(image: str) -> Image.Image:
    s = image.strip()
    if s.startswith(("http://", "https://")):
        with urllib.request.urlopen(s, timeout=30) as resp:
            data = resp.read()
        return Image.open(io.BytesIO(data))
    if s.startswith("data:image"):
        b64 = s.split(",", 1)[1]
        return Image.open(io.BytesIO(base64.b64decode(b64)))
    if len(s) < 500 and Path(s).exists():
        return Image.open(s)
    return Image.open(io.BytesIO(base64.b64decode(s)))


def _normalize(img: Image.Image) -> Image.Image:
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > MAX_IMAGE_DIM:
        scale = MAX_IMAGE_DIM / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def _to_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _vlm_call(model: str, prompt: str, image: Image.Image, timeout: int = 180) -> str:
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "images": [_to_b64(_normalize(image))],
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
    return data.get("response", "").strip()


def _vlm_chain(prompt: str, image: Image.Image, models: Optional[List[str]] = None) -> Dict[str, Any]:
    try:
        tmp = DATA_DIR / "_fast_tier_input.png"
        _normalize(image).save(tmp, format="PNG")
        body = json.dumps({"image": str(tmp), "prompt": prompt}).encode()
        req = urllib.request.Request(f"{SMOLVLM_URL}/describe", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        if data.get("text"):
            return {"model": "SmolVLM2-256M (fast)", "text": data["text"]}
    except Exception:
        pass
    last_err = ""
    for model in models or VLM_MODELS:
        try:
            return {"model": model, "text": _vlm_call(model, prompt, image)}
        except Exception as e:
            last_err = str(e)[:200]
    return {"model": None, "text": "", "error": last_err}


def describe(image: str) -> Dict[str, Any]:
    img = _load_image(image)
    return _vlm_chain("Describe this image in detail.", img)


def read_text(image: str) -> Dict[str, Any]:
    """OCR. Florence-2 daemon first (fast ~5-25s), VLM chain fallback (slow)."""
    fast = _florence_ocr(image)
    if fast and fast.get("result"):
        return {"text": fast["result"], "model": "Florence-2-base (fast)", "time_s": fast.get("time_s")}
    img = _load_image(image)
    result = _vlm_chain("Read and transcribe all text in this image.", img)
    return {"text": result.get("text", ""), "model": result.get("model")}


def detect_objects(image: str) -> Dict[str, Any]:
    """Object detection via Florence-2 daemon. Returns [{bbox, label}]."""
    fast = _florence_post("detect", {"image": image})
    if fast is not None:
        return {"boxes": fast.get("boxes", []), "model": "Florence-2-base (fast)", "time_s": fast.get("time_s")}
    return {"boxes": [], "model": None, "error": "Florence daemon unavailable"}


def find_on_screen(image: str, what: str) -> Dict[str, Any]:
    """Visual grounding via Florence-2 daemon: find 'what' in the image -> boxes."""
    if not what:
        return {"boxes": [], "error": "grounding needs a 'what' prompt"}
    fast = _florence_post("grounding", {"image": image, "prompt": what})
    if fast is not None:
        return {"boxes": fast.get("boxes", []), "model": "Florence-2-base (fast)", "time_s": fast.get("time_s")}
    return {"boxes": [], "model": None, "error": "Florence daemon unavailable"}


def get_colors(image: str, count: int = 8) -> Dict[str, Any]:
    img = _load_image(image)
    img = img.convert("RGB").resize((100, 100))
    arr = np.array(img).reshape(-1, 3).astype(np.float32)
    from sklearn.cluster import MiniBatchKMeans
    kmeans = MiniBatchKMeans(n_clusters=count, random_state=42, n_init=3)
    kmeans.fit(arr)
    colors = []
    labels, counts = np.unique(kmeans.labels_, return_counts=True)
    total = counts.sum()
    for idx in labels:
        rgb = kmeans.cluster_centers_[idx].astype(int).tolist()
        hex_color = "#{:02x}{:02x}{:02x}".format(*rgb)
        pct = round(counts[list(labels).index(idx)] / total * 100, 1)
        colors.append({"hex": hex_color, "rgb": rgb, "percent": pct})
    return {"colors": sorted(colors, key=lambda c: c["percent"], reverse=True)}


def get_layout(image: str) -> Dict[str, Any]:
    img = _load_image(image).convert("L").resize((300, 300))
    arr = np.array(img)
    h, w = arr.shape
    grid = []
    for row in range(3):
        for col in range(3):
            region = arr[row*h//3:(row+1)*h//3, col*w//3:(col+1)*w//3]
            grid.append(float(region.mean()))
    return {"brightness_grid": [grid[i*3:(i+1)*3] for i in range(3)], "mean_brightness": float(arr.mean()), "contrast": float(arr.std())}


def compare_images(a: str, b: str) -> Dict[str, Any]:
    img_a = _load_image(a).convert("L").resize((16, 16))
    img_b = _load_image(b).convert("L").resize((16, 16))
    arr_a = np.array(img_a, dtype=np.float32)
    arr_b = np.array(img_b, dtype=np.float32)
    hash_a = tuple((arr_a > arr_a.mean()).flatten().astype(int))
    hash_b = tuple((arr_b > arr_b.mean()).flatten().astype(int))
    distance = sum(1 for x, y in zip(hash_a, hash_b) if x != y)
    similarity = 1 - (distance / len(hash_a))
    return {"similarity": round(similarity, 4), "distance": distance, "identical": distance == 0}


def image_metadata(image: str) -> Dict[str, Any]:
    img = _load_image(image)
    return {"width": img.width, "height": img.height, "format": img.format, "mode": img.mode}
