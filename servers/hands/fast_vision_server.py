"""
FAST VISION daemon — tiny VLM resident in VRAM for instant image understanding.

The fast tier of the vision stack. Holds SmolVLM2-256M in memory so calls are
seconds, not 90s (qwen3-vl is the slow deep tier). Both HANDS and ZORO VISION
route quick describe/verify/OCR through this daemon, falling back to the big
model only when needed.

Endpoints:
  GET  /health                 -> {status, model, device, loaded}
  POST /describe               -> {text}        body: {image: <path or url>, prompt?}
  POST /read_text              -> {text}        body: {image: <path or url>}
  POST /verify                 -> {result}      body: {image, condition}

Run:  C:/Python314/python.exe fast_vision_server.py
"""
from __future__ import annotations

import io
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

import torch
from PIL import Image

MODEL_ID = os.environ.get("FAST_VISION_MODEL", "HuggingFaceTB/SmolVLM2-256M-Instruct")
PORT = int(os.environ.get("FAST_VISION_PORT", "3211"))
MAX_DIM = 1024

_model = None
_proc = None
_device = "cuda" if torch.cuda.is_available() else "cpu"


def load_model():
    global _model, _proc
    from transformers import AutoModelForImageTextToText, AutoProcessor
    _proc = AutoProcessor.from_pretrained(MODEL_ID)
    _model = AutoModelForImageTextToText.from_pretrained(
        MODEL_ID, torch_dtype=torch.float16 if _device == "cuda" else torch.float32
    ).to(_device)
    _model.eval()
    return _model, _proc


def _open_image(src: str) -> Image.Image:
    if src.startswith(("http://", "https://")):
        with urllib.request.urlopen(src, timeout=30) as resp:
            data = resp.read()
        img = Image.open(io.BytesIO(data))
    else:
        img = Image.open(src)
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > MAX_DIM:
        s = MAX_DIM / max(w, h)
        img = img.resize((int(w * s), int(h * s)), Image.LANCZOS)
    return img


def _generate(image: Image.Image, prompt: str) -> str:
    if _model is None:
        raise RuntimeError("model not loaded")
    messages = [
        {"role": "user", "content": [
            {"type": "image", "image": image},
            {"type": "text", "text": prompt},
        ]},
    ]
    inputs = _proc.apply_chat_template(
        messages, add_generation_prompt=True, tokenize=True,
        return_dict=True, return_tensors="pt",
    ).to(_device)
    with torch.inference_mode():
        out = _model.generate(
            **inputs,
            max_new_tokens=200,
            do_sample=False,
            temperature=None,
            top_p=None,
        )
    text = _proc.decode(out[0], skip_special_tokens=True)
    # strip the prompt echo (apply_chat_template includes it)
    if prompt in text:
        text = text.split(prompt, 1)[-1]
    return text.strip()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj: Dict[str, Any], code: int = 200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send({
                "status": "ok",
                "model": MODEL_ID,
                "device": _device,
                "loaded": _model is not None,
            })
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
        except Exception as e:
            self._send({"error": f"bad request: {e}"}, 400)
            return

        try:
            if self.path == "/describe":
                img = _open_image(body["image"])
                prompt = body.get("prompt", "Describe this image in 1-2 sentences.")
                text = _generate(img, prompt)
                self._send({"text": text, "model": MODEL_ID})
            elif self.path == "/read_text":
                img = _open_image(body["image"])
                text = _generate(img, "Transcribe all visible text exactly. If none, say NO TEXT.")
                self._send({"text": text, "model": MODEL_ID})
            elif self.path == "/verify":
                img = _open_image(body["image"])
                condition = body.get("condition", "Is the expected state visible?")
                text = _generate(
                    img,
                    f"Question: {condition} Answer with one word: yes or no.",
                )
                yes = text.strip().lower().startswith("yes")
                self._send({"result": "yes" if yes else "no", "raw": text, "model": MODEL_ID})
            else:
                self._send({"error": "not found"}, 404)
        except Exception as e:
            self._send({"error": str(e)}, 500)


def main():
    print(f"Loading {MODEL_ID} on {_device}...", flush=True)
    load_model()
    print(f"Loaded. Serving on http://127.0.0.1:{PORT}", flush=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
