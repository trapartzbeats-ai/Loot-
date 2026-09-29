---
name: "zoro-vision-mcp"
description: "Use the local Zoro Vision FastMCP v3 service for screenshots, metadata, palettes, layout measurement, crops, image comparison, OCR, semantic description, and interactive image Apps."
---

# Zoro Vision MCP v3

Use `D:/Projects/Hermes/vision/server.py` when an agent needs deterministic
image measurement or local model-backed understanding without a hosted API.

## Tools

- `screenshot`: full desktop, window-title, or pixel-region capture through the
  canonical `D:/Projects/Zoro/scripts/screenshot.py` module.
- `get_metadata`: dimensions, format, mode, file size, aspect ratio, EXIF.
- `get_colors`: bounded dominant palette extraction.
- `get_layout`: 3x3 brightness, edges, contrast, and composition hints.
- `crop`: validated crop saved under the Vision work directory.
- `compare`: 256-bit perceptual dHash comparison.
- `read_text`: Florence-2 OCR first, local VLM fallback.
- `describe`: SmolVLM fast tier, then local Ollama VLM fallback.
- `palette_ui`: FastMCP v3 interactive palette App.
- `image_inspector`: FastMCP v3 image preview, zone overlay, palette, and hints.

## Selection rules

- Prefer deterministic tools before a VLM when pixels can answer the question.
- Use `read_text` for transcription and `describe` for semantic questions.
- Capture once, then pass the returned path to the other tools.
- Crop a focused region before OCR/description when the source is large.

## CLI

```powershell
C:\Python314\python.exe cli.py list-tools
C:\Python314\python.exe cli.py call-tool screenshot --mode region --x 0 --y 0 --width 640 --height 480
C:\Python314\python.exe cli.py call-tool read_text --image D:\path\image.png
```

## Verification

```powershell
C:\Python314\python.exe -m compileall -q .
C:\Python314\python.exe test_vision_mcp.py
hermes mcp test vision
```

The acceptance test uses the exact Hermes stdio command and validates all ten
tools, the image resource, both prompts, OCR, local semantic description, and
the delegated screenshot path.
