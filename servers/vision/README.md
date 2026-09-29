# Zoro Vision MCP v3

Local image analysis for Hermes: deterministic pixel measurement, desktop
capture, OCR, semantic description, and FastMCP Apps. No hosted API key is
required.

## Runtime

- FastMCP 3.4.6
- 10 tools, one parameterized image resource, two prompts
- Florence-2 daemon at `127.0.0.1:3212` for fast OCR
- SmolVLM daemon at `127.0.0.1:3211`, then Ollama fallback for descriptions
- canonical screenshot implementation at `D:/Projects/Zoro/scripts/screenshot.py`

## Tool surface

| Tool | Purpose |
|---|---|
| `screenshot` | Capture full screen, window, or region |
| `get_metadata` | Dimensions, format, EXIF, aspect ratio |
| `get_colors` | Dominant palette |
| `get_layout` | 3x3 brightness, contrast, edges, hints |
| `crop` | Validated region crop |
| `compare` | Perceptual similarity |
| `read_text` | Florence OCR with local VLM fallback |
| `describe` | Local semantic description |
| `palette_ui` | Interactive palette App |
| `image_inspector` | Image preview and measurement overlay App |

## Registration

```yaml
mcp_servers:
  vision:
    command: C:\Python314\python.exe
    args: ["D:/Projects/Hermes/vision/server.py"]
```

HTTP mode binds to loopback by default:

```powershell
C:\Python314\python.exe server.py --http --port 8003
```

Use `--host 0.0.0.0` only when network exposure is intentional.

## Verify

```powershell
C:\Python314\python.exe test_vision_mcp.py
hermes mcp test vision
```

The checked-in acceptance fixture is `work/test_text.png`.
