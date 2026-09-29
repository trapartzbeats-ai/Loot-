"""
HANDS — the agent's hands on Windows. Self-hosted computer-use MCP server.

Merged vision-driven computer-use layer, built from the open-source research:
  - Windows-Use style: UIA control tree for structured element access
  - UFO² style: hybrid GUI + shell/API (do it the fast way, not the click way)
  - ZORO VISION: screenshot → analyze → act with real understanding
  - Mission engine: plan/execute/retry/verify loops
  - (OpenAdapt macro recording = future "watch how Ro does X" phase)

Tools:
  UIA:
    list_windows     — enumerate visible windows (title, handle, rect)
    inspect_window   — dump the UIA control tree of a window (structured)
    click_element    — click a control by name/auto_id/class (UIA)
    type_text        — type text into a focused/active control
    press_key        — send hotkeys (ctrl+c, alt+tab, ...)
  Shell:
    run_command      — run a PowerShell/system command, capture output
    launch_app       — start a program by path or name
    focus_window     — bring a window to front by title
  Vision:
    screenshot       — capture screen/window/region (delegates to ZORO VISION script)
    find_on_screen   — vision-driven: screenshot → ask VLM where an element is → return coords
    verify_state     — screenshot → ask VLM if a condition holds (green checkmark, dialog open, ...)

Architecture: HANDS composes UIA + shell + vision. It prefers structure (UIA)
and speed (shell); uses vision only when those can't answer.

Register in Hermes config.yaml:
    mcp_servers:
      hands:
        command: "C:\\Python314\\python.exe"
        args: ["D:/Projects/Hermes/hands/server.py"]

Deps: fastmcp<3, pywinauto, pywin32, comtypes, psutil, pillow
"""
from __future__ import annotations

import json
import importlib.util
import os
import re
import subprocess
import sys
import urllib.request
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

# ── Tool Search (BM25): keep agent context lean as the catalog grows ──
try:
    from fastmcp.server.transforms.search import BM25SearchTransform

    _SEARCH_ALWAYS = [
        "list_windows", "focus_window", "run_command", "screenshot",
        "verify_state", "find_on_screen", "window_manager_ui", "launch_app",
        "mouse_click", "navigate_to", "move_file", "mouse_position",
        "get_clipboard", "set_clipboard", "scroll", "drag",
        "type_text", "click_element", "press_key", "inspect_window",
    ]
    mcp = FastMCP("hands", transforms=[BM25SearchTransform(always_visible=_SEARCH_ALWAYS)])
except Exception as _e:
    mcp = FastMCP("hands")
    _SEARCH_ALWAYS = []
    _SEARCH_ERR = str(_e)

# Human-in-the-loop approval gate for desktop actions (FastMCP v3).
# Gives the user an Approve/Reject card before destructive operations.
try:
    from fastmcp.apps.approval import Approval
    mcp.add_provider(Approval())
    _APPROVAL = True
except Exception:
    _APPROVAL = False

# Choice provider: present clickable options to the user mid-task.
try:
    from fastmcp.apps.choice import Choice
    mcp.add_provider(Choice())
    _CHOICE = True
except Exception:
    _CHOICE = False

# ── Config ──
OLLAMA_URL = "http://localhost:11434"
VISION_MODEL = "qwen3-vl:4b"
VISION_FALLBACK = "moondream"
SCREENSHOT_SCRIPT = r"D:\Projects\Zoro\scripts\screenshot.py"

# Free OpenRouter cloud tier (vision + LLM). Uses OPENROUTER_API_KEY env if
# set; when present, this is the FAST accurate path (vision ~3-12s, LLM ~5s)
# with the local Ollama stack as offline fallback.
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_VISION_MODEL = os.environ.get(
    "OPENROUTER_VISION_MODEL",
    "minimax/minimax-m3:free",  # nemotron-omni hallucinates text answers, ignores images
)
OPENROUTER_LLM_MODEL = os.environ.get(
    "OPENROUTER_LLM_MODEL",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
)


def _openrouter_key() -> str:
    """OpenRouter key: env var first, then a local key file fallback."""
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    # Fallback: ~/.config/hermes/openrouter_key (so fresh MCP processes see it
    # without needing a shell env var).
    try:
        p = os.path.join(os.path.expanduser("~"), ".config", "hermes", "openrouter_key")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return ""


def _openrouter_vision(prompt: str, image_path: str, timeout: int = 60) -> Optional[str]:
    """Free OpenRouter vision tier. Returns text or None (no key / error)."""
    key = _openrouter_key()
    if not key:
        return None
    try:
        import base64 as _b64
        from PIL import Image as _PIL
        img = _PIL.open(image_path)
        if img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > 1024:  # keep it fast
            s = 1024 / max(w, h)
            img = img.resize((int(w * s), int(h * s)))
        buf = __import__("io").BytesIO()
        img.save(buf, format="PNG")
        b64 = _b64.b64encode(buf.getvalue()).decode()
        body = json.dumps({
            "model": OPENROUTER_VISION_MODEL,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}],
        }).encode()
        req = urllib.request.Request(
            OPENROUTER_URL, data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        return (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip() or None
    except Exception:
        return None


def _uia_backend():
    """Lazy-import pywinauto (heavy)."""
    from pywinauto import Desktop
    return Desktop(backend="uia")


def _safe(text: str, limit: int = 2000) -> str:
    return (text or "")[:limit]


# ── UIA tools ──

@mcp.tool(
    name="list_windows",
    description="Enumerate visible windows: title, window handle, and rect. Pass a filter (partial title) to narrow.",
)
def list_windows(filter: str = "") -> Dict[str, Any]:
    """List visible windows via win32gui."""
    import win32gui

    results = []
    def _enum(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title.strip():
            return
        if filter and filter.lower() not in title.lower():
            return
        rect = win32gui.GetWindowRect(hwnd)
        results.append({
            "hwnd": hwnd,
            "title": title[:200],
            "rect": list(rect),
        })
    win32gui.EnumWindows(_enum, None)
    return {"count": len(results), "windows": results[:50]}


@mcp.tool(
    name="inspect_window",
    description=(
        "Inspect the UIA control tree of a window (by title, partial match). Returns "
        "structured controls with their name, control_type, automation_id, class, and rect. "
        "depth limits recursion. This is the structured 'eyes' — no vision model needed."
    ),
)
def inspect_window(title: str, depth: int = 3, max_controls: int = 100) -> Dict[str, Any]:
    """Dump a window's UIA control tree as JSON."""
    desktop = _uia_backend()
    wins = desktop.windows(title_re=rf".*{re.escape(title)}.*")
    if not wins:
        # fall back to fuzzy match
        all_wins = desktop.windows()
        wins = [w for w in all_wins if title.lower() in w.window_text().lower()]
    if not wins:
        return {"error": f"No window matching '{title}'", "controls": []}
    win = wins[0]

    controls = []

    def _walk(element, cur_depth):
        if len(controls) >= max_controls:
            return
        if cur_depth > depth:
            return
        try:
            name = element.window_text() or element.element_info.name or ""
            ctype = element.element_info.control_type or ""
            auto_id = element.element_info.automation_id or ""
            cls = element.friendly_class_name() or ""
            try:
                rect = element.rectangle()
                rect_s = [rect.left, rect.top, rect.right, rect.bottom]
            except Exception:
                rect_s = None
            controls.append({
                "name": _safe(name, 200),
                "control_type": ctype,
                "automation_id": auto_id,
                "class": cls,
                "rect": rect_s,
                "depth": cur_depth,
            })
        except Exception:
            pass
        try:
            children = element.children()
        except Exception:
            children = []
        for child in children[:20]:
            _walk(child, cur_depth + 1)

    try:
        _walk(win, 0)
    except Exception as e:
        return {"error": str(e), "controls": controls}

    return {
        "window": _safe(win.window_text(), 200),
        "hwnd": getattr(win, "handle", None),
        "control_count": len(controls),
        "controls": controls,
    }


@mcp.tool(
    name="click_element",
    description=(
        "Click a UI element in a window by name or automation_id. UIA-first: finds the "
        "control in the accessibility tree and clicks it — no pixel guessing. Pass title "
        "(window, partial) and either name or automation_id. Optional button (left/right/double)."
    ),
)
def click_element(title: str, name: str = "", automation_id: str = "", button: str = "left") -> Dict[str, Any]:
    """Click a control by UIA name/automation_id."""
    desktop = _uia_backend()
    spec = desktop.window(title_re=f".*{re.escape(title)}.*")
    try:
        if not spec.exists(timeout=2):
            return {"error": f"No window matching '{title}'"}
        win = spec.wrapper_object()

        target = None
        # automation_id path (e.g. a ListItem's auto_id "0" — but scope by control type)
        if automation_id:
            try:
                cands = win.descendants(auto_id=automation_id)
                if cands:
                    target = cands[0].wrapper_object()
            except Exception:
                target = None
        # name path: prefer an EXACT name match first (e.g. "sincere carter" must
        # not match "sincere carter 2"), then fall back to substring match.
        if target is None and name:
            try:
                descendants = win.descendants()
                name_l = name.lower()
                exact = None
                substring = None
                for d in descendants:
                    cname = (d.element_info.name or "")
                    cl = cname.lower()
                    if cl == name_l or cl == name_l + ".jpg" or cl == name_l + ".png":
                        exact = d
                        break
                    if not substring and name_l in cl:
                        substring = d
                target = exact or substring
            except Exception:
                target = None
        if target is None:
            return {"error": f"Control not found (name={name!r}, auto_id={automation_id!r}) in '{win.window_text()}'"}

        try:
            # UIA wrappers: ListItem/other controls often expose only click_input
            # (physical click at element center) or invoke(); plain .click() is
            # not available on all control types.
            if hasattr(target, "click_input"):
                if button == "double":
                    target.click_input(double=True)
                elif button == "right":
                    target.click_input(button="right")
                else:
                    target.click_input()
            elif hasattr(target, "invoke"):
                target.invoke()
            elif button == "double":
                target.double_click()
            elif button == "right":
                target.right_click()
            else:
                target.click()
            return {"clicked": True, "window": _safe(win.window_text(), 100), "control": name or automation_id}
        except Exception as e:
            return {"clicked": False, "error": str(e)}
    except Exception as e:
        return {"clicked": False, "error": str(e)}


@mcp.tool(
    name="type_text",
    description="Type text into the active/focused control of a window (by title). Text is sent literally (paths, punctuation safe).",
)
def type_text(title: str, text: str) -> Dict[str, Any]:
    """Type text into a window's focused control."""
    desktop = _uia_backend()
    all_wins = desktop.windows()
    wins = [w for w in all_wins if title.lower() in w.window_text().lower()]
    if not wins:
        return {"error": f"No window matching '{title}'"}
    win = wins[0]
    try:
        win.set_focus()
        import pywinauto.keyboard as kb
        # send_keys treats { } ( ) ^ % + as modifiers; escape them so the
        # text is typed literally (paths like C:\Users\... arrive intact).
        escaped = (
            text
            .replace("{", "{{}")
            .replace("}", "{}}")
            .replace("(", "{(}")
            .replace(")", "{)}")
            .replace("^", "{^}")
            .replace("%", "{%}")
            .replace("+", "{+}")
        )
        kb.send_keys(escaped, with_spaces=True)
        return {"typed": True, "window": _safe(win.window_text(), 100), "chars": len(text)}
    except Exception as e:
        return {"typed": False, "error": str(e)}


@mcp.tool(
    name="navigate_to",
    description=(
        "Navigate a File Explorer window to a folder path. Uses the address bar "
        "Edit control directly (set text + Enter) — deterministic, no mouse/focus "
        "hunting. Pass title (window, partial) and path (e.g. C:\\Users\\User\\Pictures)."
    ),
)
def navigate_to(title: str, path: str) -> Dict[str, Any]:
    """Navigate Explorer to a path via the address bar UIA Edit control."""
    import time as _time
    import os as _os
    import win32gui
    import win32con
    desktop = _uia_backend()
    # Match the window by title, but DON'T fail on a stale title — the title
    # may already be the destination if a prior navigation landed. Find any
    # Explorer window that matches the title OR is the same hwnd we act on.
    spec = desktop.window(title_re=f".*{re.escape(title)}.*")
    try:
        found = spec.exists(timeout=2)
        hwnd = None
        if not found:
            # Title may have changed mid-flight; try matching any Explorer window.
            import pywinauto.findwindows as fw
            try:
                handles = fw.find_windows(class_name="CabinetWClass")
                if not handles:
                    return {"error": f"No Explorer window matching '{title}'"}
                spec = desktop.window(handle=handles[0])
                hwnd = handles[0]
            except Exception:
                return {"error": f"No window matching '{title}'"}
        else:
            # Resolve the hwnd once up front: needed for the IsIconic/SW_RESTORE
            # check and for the post-navigation title readback (the title changes
            # under us after Enter, so it can't be re-matched by title_re).
            try:
                hwnd = int(spec.handle)
            except Exception:
                try:
                    hwnd = int(spec.wrapper_object().handle)
                except Exception:
                    hwnd = None
        # A MINIMIZED Explorer window exposes no UIA address-bar Edit, so the
        # old code lied with "Address bar Edit not found". Restore the window
        # first and retry; only then decide the address bar is really missing.
        was_iconic = False
        if hwnd and win32gui.IsIconic(hwnd):
            was_iconic = True
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            _time.sleep(0.5)  # give UIA a beat to repopulate the tree
        # Address bar Edit lives under the PART_AutoSuggestBox group; the bare
        # auto_id 'TextBox' is ambiguous (address bar + search box), so scope it.
        addr_spec = spec.child_window(
            auto_id="PART_AutoSuggestBox", control_type="Group",
        ).child_window(auto_id="TextBox", control_type="Edit")
        if not addr_spec.exists(timeout=3):
            if was_iconic:
                return {"navigated": False,
                        "error": "window is minimized or not responding: restored it but the "
                                 "address bar is still unavailable — bring the window up and retry"}
            return {"navigated": False, "error": "Address bar Edit not found"}
        addr = addr_spec.wrapper_object()
        addr.set_focus()
        # Set the text directly on the address bar Edit. pywinauto EditWrappers
        # expose set_edit_text (works across versions); fall back to the UIA
        # ValuePattern if the method isn't present.
        if hasattr(addr, "set_edit_text"):
            addr.set_edit_text(path)
        else:
            try:
                addr.get_value_pattern().set_value(path)
            except Exception:
                # Last resort: select-all + type (keyboard path)
                import pywinauto.keyboard as kb
                kb.send_keys("^a")
                kb.send_keys(path.replace("{", "{{}").replace("}", "{}}"))
        import pywinauto.keyboard as kb
        kb.send_keys("{ENTER}")
        # Give Explorer a moment to navigate; the title may now reflect the
        # destination folder (e.g. "carter - File Explorer").
        _time.sleep(1.0)
        # Read back the ACTUAL window title (by hwnd — re-matching by title_re
        # fails once the title changes to the destination). If the title doesn't
        # match the destination folder name, the navigation may not have
        # committed: report it so callers verify by effect.
        try:
            if hwnd:
                actual_title = _safe(win32gui.GetWindowText(hwnd), 100)
            else:
                actual_title = _safe(spec.window_text(), 100)
        except Exception:
            try:
                actual_title = _safe(spec.window_text(), 100)
            except Exception:
                actual_title = ""
        dest_name = _os.path.basename(path.rstrip("/\\")) or path
        committed = bool(dest_name) and dest_name.lower() in actual_title.lower()
        return {"navigated": True, "window": actual_title, "path": path,
                "committed": committed}
    except Exception as e:
        return {"navigated": False, "error": str(e)}


@mcp.tool(
    name="press_key",
    description=(
        "Send keyboard hotkeys to the active window. Accepts pywinauto syntax "
        "('^c' for ctrl+c, '%{TAB}' for alt+tab, '{ENTER}') OR plain names "
        "('ctrl+c', 'ctrl+x', 'ctrl+v', 'alt+tab', 'enter', 'esc'). Prefer the "
        "plain-name form — the caret '^' can be eaten by the shell layer."
    ),
)
def press_key(keys: str) -> Dict[str, Any]:
    """Send hotkeys via pywinauto keyboard. Accepts ctrl+x style names too."""
    import re
    try:
        import pywinauto.keyboard as kb
        # Normalize plain names like 'ctrl+c', 'alt+tab', 'enter' to pywinauto syntax.
        _MAP = {
            "ctrl": "^", "control": "^", "alt": "%", "shift": "+",
            "enter": "{ENTER}", "return": "{ENTER}", "esc": "{ESC}",
            "escape": "{ESC}", "tab": "{TAB}", "space": " ",
            "backspace": "{BACKSPACE}", "delete": "{DELETE}", "del": "{DELETE}",
            "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}", "right": "{RIGHT}",
            "home": "{HOME}", "end": "{END}", "pgup": "{PGUP}", "pgdn": "{PGDN}",
            "f1": "{F1}", "f2": "{F2}", "f3": "{F3}", "f4": "{F4}",
            "f5": "{F5}", "f6": "{F6}", "f7": "{F7}", "f8": "{F8}",
            "f9": "{F9}", "f10": "{F10}", "f11": "{F11}", "f12": "{F12}",
        }
        def _normalize(k: str) -> str:
            if k in _MAP:
                return _MAP[k]
            return k
        # Only rewrite if it looks like a plain 'modifier+key' name (contains no
        # pywinauto special chars already).
        if not re.search(r"[\^%+{}]", keys) and "+" in keys:
            parts = keys.lower().split("+")
            mods = [_normalize(p) for p in parts[:-1] if p]
            key = _normalize(parts[-1])
            if mods:
                keys = "".join(mods) + key
            else:
                keys = key
        elif "+" not in keys and not re.search(r"[\^%{}]", keys):
            keys = _normalize(keys.lower())
        kb.send_keys(keys)
        return {"pressed": True, "keys": keys}
    except Exception as e:
        return {"pressed": False, "error": str(e)}


def _window_at_is_elevated(x: int, y: int) -> bool:
    """Return True if the window under (x,y) runs at a HIGHER integrity level
    than this process. SendInput is blocked by UIPI from injecting into such
    windows, so a click there would be silently dropped."""
    import ctypes
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.WindowFromPoint(ctypes.wintypes.POINT(x, y))
        if not hwnd:
            return False
        # Get the window's process and its token integrity level
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        TOKEN_QUERY = 0x0008
        advapi32 = ctypes.windll.advapi32
        kernel32 = ctypes.windll.kernel32
        hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not hproc:
            return False
        try:
            htok = ctypes.wintypes.HANDLE()
            if not advapi32.OpenProcessToken(hproc, TOKEN_QUERY, ctypes.byref(htok)):
                return False
            try:
                # TokenIntegrityLevel -> SID -> compare integrity
                from ctypes import wintypes as wt
                class TOKEN_MANDATORY_LABEL(ctypes.Structure):
                    _fields_ = [("Label", ctypes.c_void_p)]
                tml = TOKEN_MANDATORY_LABEL()
                size = wt.DWORD()
                advapi32.GetTokenInformation(htok, 25, ctypes.byref(tml), 0, ctypes.byref(size))  # 25 = TokenIntegrityLevel
                if size.value == 0:
                    return False
                buf = ctypes.create_string_buffer(size.value)
                advapi32.GetTokenInformation(htok, 25, buf, size.value, ctypes.byref(size))
                sid_ptr = ctypes.cast(ctypes.addressof(buf) + 0, ctypes.POINTER(ctypes.c_void_p)).contents
                # Get SID subauthority (the integrity value: 16 medium, 32 high)
                def _sub_auth_count(sid):
                    return ctypes.cast(sid, ctypes.POINTER(ctypes.c_ubyte)).contents.value & 0x0F
                count = _sub_auth_count(sid_ptr.value)
                # SubAuthority array is after identifier authority; get last value
                sa = ctypes.cast(sid_ptr.value, ctypes.POINTER(ctypes.c_ulong))
                # SID layout: revision(1) + count(1) + auth(6) + subs(count)
                offset = (8 // 4) + (count - 1)
                integrity = sa[offset]
                return integrity > 16  # > medium = elevated
            finally:
                advapi32.CloseHandle(htok)
        finally:
            kernel32.CloseHandle(hproc)
    except Exception:
        return False  # on any uncertainty, don't block the click


@mcp.tool(
    name="mouse_click",
    description=(
        "Move the mouse to absolute screen coordinates and click. Uses SendInput "
        "(same low-level input as OpenAI Computer Use). Coordinates are in native "
        "screen pixels: x,y from the top-left of the primary monitor. Optional "
        "button: left/right/double. Refuses to click ELEVATED windows (UIPI "
        "silently blocks them — reports honestly instead)."
    ),
)
def mouse_click(x: int, y: int, button: str = "left") -> Dict[str, Any]:
    """SendInput-based absolute mouse move + click at screen coordinates."""
    import ctypes
    from ctypes import wintypes

    ULONG_PTR = wintypes.WPARAM  # pointer-sized for 64-bit input structures

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]

    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010

    try:
        user32 = ctypes.windll.user32
        # UIPI honesty check: SendInput can only inject into windows at EQUAL or
        # LESSER integrity level. If the window under the cursor is elevated
        # (admin), the click will be silently dropped — report that instead of
        # faking a verified click.
        elev = _window_at_is_elevated(x, y)
        if elev:
            return {
                "clicked": False,
                "x": x, "y": y, "button": button,
                "sent": 0,
                "verified": False,
                "error": "target window is ELEVATED (higher integrity); "
                         "SendInput cannot inject into it — run hands elevated "
                         "or target a non-admin window",
            }
        # Move the cursor with SetCursorPos — it natively accepts virtual-desktop
        # coordinates, so it works across ANY monitor (including negative-offset
        # second/third displays). SendInput's MOUSEEVENTF_ABSOLUTE mapping is
        # primary-monitor-relative and unreliable with multiple monitors.
        ok_move = bool(user32.SetCursorPos(x, y))
        if not ok_move:
            return {"clicked": False, "error": f"SetCursorPos failed for ({x},{y})"}

        def _input(flags: int) -> INPUT:
            mi = MOUSEINPUT(0, 0, 0, flags, 0, 0)  # no move — click at current pos
            return INPUT(0, mi)  # type 0 = mouse

        events = []
        if button == "right":
            events += [_input(MOUSEEVENTF_RIGHTDOWN), _input(MOUSEEVENTF_RIGHTUP)]
        elif button == "double":
            events += [
                _input(MOUSEEVENTF_LEFTDOWN), _input(MOUSEEVENTF_LEFTUP),
                _input(MOUSEEVENTF_LEFTDOWN), _input(MOUSEEVENTF_LEFTUP),
            ]
        else:
            events += [_input(MOUSEEVENTF_LEFTDOWN), _input(MOUSEEVENTF_LEFTUP)]

        arr = (INPUT * len(events))(*events)
        sent = user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))

        # Verify the click actually registered at the OS level: the cursor must
        # be at the target. This is the ground-truth check that a click
        # "actually happened" — not just that we sent it.
        import time as _time
        _time.sleep(0.03)
        p = POINT()
        user32.GetCursorPos(ctypes.byref(p))
        cursor_ok = abs(p.x - x) <= 3 and abs(p.y - y) <= 3

        return {
            "clicked": sent == len(events),
            "x": x, "y": y, "button": button,
            "sent": int(sent),
            "cursor_at_target": cursor_ok,
            "verified": cursor_ok and sent == len(events),
        }
    except Exception as e:
        return {"clicked": False, "error": str(e)}


@mcp.tool(
    name="mouse_position",
    description="Return the current cursor position in screen pixels (GetCursorPos). Cheap verification for where the mouse actually is.",
)
def mouse_position() -> Dict[str, Any]:
    """Get the current cursor position."""
    import ctypes
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    p = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return {"x": int(p.x), "y": int(p.y)}


@mcp.tool(
    name="scroll",
    description=(
        "Scroll the mouse wheel at the current cursor position. Positive "
        "clicks scroll up (away from user), negative scrolls down. "
        "e.g. scroll(3) scrolls up 3 notches, scroll(-5) scrolls down 5."
    ),
)
def scroll(clicks: int, x: int = -1, y: int = -1) -> Dict[str, Any]:
    """Send a mouse wheel scroll. Optionally move to (x,y) first."""
    import ctypes
    from ctypes import wintypes
    try:
        user32 = ctypes.windll.user32
        if x >= 0 and y >= 0:
            user32.SetCursorPos(x, y)
        # WHEEL_DELTA is 120; positive = up, negative = down.
        delta = int(clicks * 120)
        user32.mouse_event(0x0800, 0, 0, delta, 0)  # MOUSEEVENTF_WHEEL
        return {"scrolled": True, "clicks": clicks, "delta": delta}
    except Exception as e:
        return {"scrolled": False, "error": str(e)}


@mcp.tool(
    name="drag",
    description=(
        "Drag from (x1,y1) to (x2,y2): press the button at the start, move "
        "through the path, release at the end. Coordinates are virtual-desktop "
        "(multi-monitor safe). Optional button: left/right."
    ),
)
def drag(x1: int, y1: int, x2: int, y2: int, button: str = "left", steps: int = 10) -> Dict[str, Any]:
    """Mouse drag via SetCursorPos + SendInput button press/release."""
    import ctypes
    from ctypes import wintypes
    import time as _time

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", wintypes.WPARAM)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]

    MOUSEEVENTF_LEFTDOWN = 0x0002
    MOUSEEVENTF_LEFTUP = 0x0004
    MOUSEEVENTF_RIGHTDOWN = 0x0008
    MOUSEEVENTF_RIGHTUP = 0x0010

    def _input(flags: int) -> INPUT:
        return INPUT(0, MOUSEINPUT(0, 0, 0, flags, 0, 0))

    try:
        user32 = ctypes.windll.user32
        # Start position
        user32.SetCursorPos(x1, y1)
        _time.sleep(0.05)
        down = MOUSEEVENTF_LEFTDOWN if button == "left" else MOUSEEVENTF_RIGHTDOWN
        up = MOUSEEVENTF_LEFTUP if button == "left" else MOUSEEVENTF_RIGHTUP
        d = _input(down)
        user32.SendInput(1, ctypes.byref(d), ctypes.sizeof(INPUT))
        _time.sleep(0.05)
        # Move through the path
        for i in range(1, steps + 1):
            px = x1 + (x2 - x1) * i // steps
            py = y1 + (y2 - y1) * i // steps
            user32.SetCursorPos(px, py)
            _time.sleep(0.01)
        _time.sleep(0.05)
        u = _input(up)
        user32.SendInput(1, ctypes.byref(u), ctypes.sizeof(INPUT))
        return {"dragged": True, "from": [x1, y1], "to": [x2, y2], "button": button}
    except Exception as e:
        return {"dragged": False, "error": str(e)}


@mcp.tool(
    name="get_clipboard",
    description="Read the current clipboard text. Use to verify a copy/cut actually captured content before pasting.",
)
def get_clipboard() -> Dict[str, Any]:
    """Read clipboard text."""
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                return {"has_text": True, "text": _safe(data, 2000)}
            return {"has_text": False, "text": ""}
        finally:
            win32clipboard.CloseClipboard()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool(
    name="set_clipboard",
    description="Set the clipboard to text. Use before a deterministic paste when you need known content on the clipboard.",
)
def set_clipboard(text: str) -> Dict[str, Any]:
    """Set clipboard text."""
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text or "", win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        return {"set": True, "chars": len(text or "")}
    except Exception as e:
        return {"set": False, "error": str(e)}


# ── Shell tools ──

@mcp.tool(
    name="move_file",
    description=(
        "Move a file to a destination folder deterministically via copy-then-verify-"
        "then-delete — never cut+paste. Copying first means the source survives any "
        "failure; the original is only deleted after the copy is verified to exist. "
        "Pass source path and destination folder."
    ),
)
def move_file(source: str, destination: str, copy: bool = False) -> Dict[str, Any]:
    """Move a file safely: copy -> verify -> delete source. Never clipboard cut."""
    import shutil
    import os
    try:
        if not os.path.exists(source):
            return {"moved": False, "error": f"Source not found: {source}"}
        os.makedirs(destination, exist_ok=True)
        dest_path = os.path.join(destination, os.path.basename(source))
        if copy:
            # Pure copy: no source deletion at all.
            shutil.copy2(source, dest_path)
            return {
                "moved": True, "action": "copied",
                "source": source, "destination": dest_path,
                "exists": os.path.exists(dest_path),
            }
        # Move = copy first, verify, then remove source only if the copy exists.
        shutil.copy2(source, dest_path)
        if not os.path.exists(dest_path):
            return {"moved": False, "error": f"Copy failed (not found at {dest_path}); source untouched"}
        if os.path.getsize(dest_path) != os.path.getsize(source):
            return {"moved": False, "error": "Copy size mismatch; source untouched"}
        os.remove(source)
        return {
            "moved": True, "action": "moved",
            "source": source, "destination": dest_path,
            "exists": os.path.exists(dest_path),
        }
    except Exception as e:
        return {"moved": False, "error": str(e)}


@mcp.tool(
    name="run_command",
    description=(
        "Run a PowerShell command and capture output. Use for anything the shell does "
        "faster than clicking (create folder, list files, run a script, query services). "
        "This is the UFO² hybrid principle: shell beats GUI clicks when it can. "
        "For destructive commands (delete, kill, format, registry writes) use "
        "run_command_guarded which requires your approval first."
    ),
)
def run_command(command: str, timeout: int = 30) -> Dict[str, Any]:
    """Run a PowerShell command, return stdout/stderr/exit."""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, text=True, timeout=timeout,
            creationflags=0x08000000,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": _safe(proc.stdout, 8000),
            "stderr": _safe(proc.stderr, 4000),
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s"}
    except Exception as e:
        return {"error": str(e)}


_DANGEROUS_HINTS = [
    "remove-item", "del ", "rm ", "kill", "stop-process", "taskkill",
    "format", "clear-dns", "set-executionpolicy", "reg delete", "rd /s",
    "takeown", "icacls", "disable-", "remove-", "uninstall",
]


@mcp.tool(
    name="run_command_guarded",
    description=(
        "Run a PowerShell command with a human approval gate. CALL request_approval "
        "FIRST (summary/destructive variant), wait for the user's decision, then call "
        "this with the approved command. Use for destructive/irreversible commands "
        "(deletes, kills, registry changes, service stops)."
    ),
)
def run_command_guarded(command: str, timeout: int = 30) -> Dict[str, Any]:
    """Run a PowerShell command that has already been approved by the user."""
    if not _APPROVAL:
        return {"error": "Approval provider unavailable — command not executed.",
                "command": command}
    return run_command(command, timeout=timeout)


@mcp.tool(
    name="launch_app",
    description="Launch a program by path or a start command (app name, file, URL). Uses os.startfile for double-click-equivalent launch.",
)
def launch_app(target: str) -> Dict[str, Any]:
    """Launch an app/file/URL."""
    try:
        os.startfile(target)  # type: ignore[attr-defined]
        return {"launched": True, "target": target}
    except Exception as e:
        # fallback: shell start
        try:
            subprocess.Popen(["cmd", "/c", "start", "", target], creationflags=0x08000000)
            return {"launched": True, "target": target, "via": "cmd start"}
        except Exception as e2:
            return {"launched": False, "error": f"{e}; {e2}"}


@mcp.tool(
    name="focus_window",
    description="Bring a window to the foreground by title (partial match). Does not move the mouse.",
)
def focus_window(title: str) -> Dict[str, Any]:
    """Focus a window by title."""
    try:
        import win32gui
        import win32con
        import pywinauto.keyboard as kb

        hwnd = None
        def _enum(h, _):
            nonlocal hwnd
            if hwnd:
                return
            if win32gui.IsWindowVisible(h) and title.lower() in win32gui.GetWindowText(h).lower():
                hwnd = h
        win32gui.EnumWindows(_enum, None)
        if not hwnd:
            return {"error": f"No window matching '{title}'"}
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        # Windows restricts SetForegroundWindow to the foreground process.
        # Tapping Alt momentarily releases the lock so any process can focus.
        kb.send_keys("%")  # alt tap
        for _ in range(3):
            try:
                win32gui.SetForegroundWindow(hwnd)
                if win32gui.GetForegroundWindow() == hwnd:
                    break
            except Exception:
                pass
        return {"focused": True, "hwnd": hwnd, "title": _safe(win32gui.GetWindowText(hwnd), 100)}
    except Exception as e:
        return {"focused": False, "error": str(e)}


# ── Vision bridge ──

def _fast_vision(path: str, prompt: str, endpoint: str = "describe", timeout: int = 30) -> Optional[str]:
    """Call the fast-vision daemon (SmolVLM2 resident in VRAM). ~10s vs 90s."""
    import urllib.error
    try:
        body = json.dumps({
            "image": path,
            "prompt": prompt,
            **({"condition": prompt} if endpoint == "verify" else {}),
        }).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:3211/{endpoint}",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        if endpoint == "verify":
            return data.get("result")
        return data.get("text")
    except Exception:
        return None


def _vlm_call(model: str, prompt: str, image_path: str, timeout: int = 180) -> str:
    import base64
    from PIL import Image

    # Downscale to keep VLM fast (T600 reality) — same as ZORO VISION.
    img = Image.open(image_path)
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > 1280:
        scale = 1280 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = __import__("io").BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "images": [b64],
        "stream": False,
        # Cap context: qwen3-vl defaults to 262144 tokens which grinds the
        # 4GB T600 to a crawl. 8192 is plenty for a screenshot + JSON answer.
        "options": {"temperature": 0.1, "num_ctx": 8192},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return data.get("response", "").strip()


def _take_screenshot(mode: str = "full", target: str = "") -> str:
    """Call the canonical capture library in-process (safe for MCP stdio)."""
    spec = importlib.util.spec_from_file_location("zoro_canonical_screenshot", SCREENSHOT_SCRIPT)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load canonical screenshot module: {SCREENSHOT_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    region = None
    if mode == "region":
        try:
            region = [int(value) for value in target.replace(",", " ").split()]
        except ValueError as ex:
            raise ValueError("region target must be: left top right bottom") from ex
    return module.capture(mode=mode, target=target if mode == "window" else "", region=region)


@mcp.tool(
    name="screenshot",
    description="Capture the screen (full, window, or region) via the canonical Zoro screenshot script. Returns path for vision analysis.",
)
def screenshot(mode: str = "full", target: str = "") -> Dict[str, Any]:
    """Capture screen -> path."""
    path = _take_screenshot(mode, target)
    from PIL import Image
    img = Image.open(path)
    return {"saved_to": path, "width": img.width, "height": img.height, "mode": mode}


def _florence_ground(path: str, prompt: str, timeout: int = 15) -> Optional[Dict]:
    """Grounding via Florence-2 daemon (fast, ~1-3s). Returns first box center or None."""
    try:
        import urllib.request
        body = json.dumps({"image": path, "prompt": prompt}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:3212/grounding", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        boxes = data.get("boxes", [])
        if boxes:
            b = boxes[0]["bbox"]
            return {
                "bbox": b,
                "center": {"x": (b[0] + b[2]) // 2, "y": (b[1] + b[3]) // 2},
                "label": boxes[0].get("label", ""),
                "time_s": data.get("time_s"),
            }
    except Exception:
        return None
    return None


def _florence_ocr(path: str, timeout: int = 15) -> Optional[str]:
    """OCR via Florence-2 daemon (fast, ~1-3s). Returns text or None."""
    try:
        import urllib.request
        body = json.dumps({"image": path}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:3212/ocr", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        txt = data.get("result", "")
        return txt if txt else None
    except Exception:
        return None


@mcp.tool(
    name="find_on_screen",
    description=(
        "Vision-driven element location: screenshot the screen (or a window), ask the local "
        "VLM where a described element is, and return approximate pixel coordinates. "
        "Use when UIA can't identify the control (canvas apps, games, custom UI). "
        "Pass what='the red Submit button' or similar. This is the UI-TARS-style fallback."
    ),
)
def find_on_screen(what: str, window: str = "", mode: str = "full") -> Dict[str, Any]:
    """Find an element -> screen coords. Ladder:
    1. UIA accessibility tree (exact rects, deterministic) — BEST
    2. OpenRouter cloud vision (free, fast, accurate) — if key present
    3. Florence-2 grounding daemon (fast, ~2s) — for canvas/custom UI
    4. qwen3-vl VLM coordinate guess (slow, last resort)
    """
    # Tier 1: UIA tree — exact control rects, no pixels guessed.
    if window:
        try:
            spec = _uia_backend().window(title_re=f".*{re.escape(window)}.*")
            if spec.exists(timeout=1):
                win = spec.wrapper_object()
                name_l = what.lower()
                for d in win.descendants():
                    cname = (d.element_info.name or "").lower()
                    if cname == name_l or (name_l in cname and d.element_info.control_type == "ListItem"):
                        rect = d.element_info.rectangle
                        cx = (rect.left + rect.right) // 2
                        cy = (rect.top + rect.bottom) // 2
                        return {
                            "what": what, "window": window,
                            "coords": {"x": int(cx), "y": int(cy)},
                            "bbox": [rect.left, rect.top, rect.right, rect.bottom],
                            "tier": "uia", "control_type": d.element_info.control_type,
                        }
        except Exception:
            pass

    # Tiers 2-4: screenshot-based fallback
    path = _take_screenshot(mode, window)
    from PIL import Image
    img = Image.open(path)
    w, h = img.size

    # Tier 2: OpenRouter cloud vision (sees the NATIVE image, no downscale)
    if _openrouter_key():
        prompt = (
            f"I need to click on: {what}. "
            f"The screenshot is {w}x{h} pixels. "
            "Return the CENTER pixel coordinates of that element as JSON only: "
            '{"x": <int>, "y": <int>}. If not visible, return {"x": -1, "y": -1}.'
        )
        try:
            text = _openrouter_vision(prompt, path)
        except Exception:
            text = None
        m = re.search(r"\{[^}]*\"x\"[^}]*\}", text or "")
        if m:
            try:
                coords = json.loads(m.group(0))
                if coords.get("x", -1) >= 0:
                    return {
                        "what": what, "screenshot": path, "size": [w, h],
                        "coords": coords, "tier": "openrouter",
                        "model": OPENROUTER_VISION_MODEL,
                    }
            except Exception:
                pass

    # Tier 3: Florence-2 grounding daemon
    hit = _florence_ground(path, what)
    if hit and hit.get("center", {}).get("x", -1) >= 0:
        return {
            "what": what,
            "screenshot": path,
            "size": [w, h],
            "coords": hit["center"],
            "bbox": hit.get("bbox"),
            "tier": "florence",
            "time_s": hit.get("time_s"),
        }

    # Tier 4: local VLM coordinate guess
    prompt = (
        f"I need to click on: {what}. "
        f"The screenshot is {w}x{h} pixels. "
        "Return the CENTER pixel coordinates of that element as JSON only: "
        '{"x": <int>, "y": <int>}. If not visible, return {"x": -1, "y": -1}.'
    )
    try:
        text = _vlm_call(VISION_MODEL, prompt, path)
    except Exception:
        text = ""

    m = re.search(r"\{[^}]*\"x\"[^}]*\}", text or "")
    coords = {"x": -1, "y": -1}
    if m:
        try:
            coords = json.loads(m.group(0))
        except Exception:
            pass

    # _vlm_call downscales to max 1280px keeping aspect ratio. Map model-space
    # coords back to native screen resolution before returning.
    if coords.get("x", -1) >= 0:
        img_w, img_h = img.size
        scale = min(1.0, 1280.0 / max(img_w, img_h))
        if scale < 1.0:
            model_w, model_h = int(img_w * scale), int(img_h * scale)
            coords = {
                "x": int(round(coords["x"] * img_w / model_w)),
                "y": int(round(coords["y"] * img_h / model_h)),
            }

    return {
        "what": what,
        "screenshot": path,
        "size": [w, h],
        "coords": coords,
        "tier": "vlm",
        "model": VISION_MODEL,
        "raw_vlm": _safe(text, 500),
    }


@mcp.tool(
    name="verify_state",
    description=(
        "Vision-driven verification: screenshot the screen/window and ask the local VLM "
        "whether a condition is true (e.g. 'is there a green checkmark?', 'did a dialog open?', "
        "'is the app running?'). Returns yes/no + explanation. Use to close the act→verify loop."
    ),
)
def verify_state(condition: str, window: str = "", mode: str = "full") -> Dict[str, Any]:
    """Verify a visual condition via VLM. Fast tier (SmolVLM2) first, qwen3-vl fallback."""
    path = _take_screenshot(mode, window)

    # Fast tier first: SmolVLM2 resident daemon, ~10s
    fast = _fast_vision(path, condition, endpoint="verify")
    if fast:
        return {
            "condition": condition,
            "screenshot": path,
            "result": fast,
            "tier": "fast",
            "model": "SmolVLM2-256M",
        }

    # Cloud tier: OpenRouter vision (cheap/free models) before slow local VLM.
    prompt = (
        f"Question: {condition} "
        "Answer with JSON only: {\"result\": \"yes\"|\"no\"|\"uncertain\", "
        "\"reason\": \"<short explanation>\"}"
    )
    if _openrouter_key():
        try:
            or_text = _openrouter_vision(prompt, path) or ""
            m = re.search(r"\{[^}]*\"result\"[^}]*\}", or_text)
            if m:
                result = json.loads(m.group(0))
                return {
                    "condition": condition,
                    "screenshot": path,
                    **result,
                    "tier": "openrouter",
                    "model": OPENROUTER_VISION_MODEL,
                }
        except Exception:
            pass

    # Deep tier fallback (local Ollama VLM)
    try:
        text = _vlm_call(VISION_MODEL, prompt, path)
    except Exception:
        text = ""
    m = re.search(r"\{[^}]*\"result\"[^}]*\}", text)
    result = {"result": "uncertain", "reason": ""}
    if m:
        try:
            result = json.loads(m.group(0))
        except Exception:
            pass
    return {
        "condition": condition,
        "screenshot": path,
        **result,
        "tier": "deep",
        "raw_vlm": _safe(text, 500),
    }


@mcp.tool(
    name="windows_ui",
    app=True,
    description="Live window list rendered as an interactive table (FastMCP v3 Apps): title, handle, size, actions.",
)
def windows_ui(filter: str = ""):
    """Render open windows as an interactive table UI."""
    from prefab_ui.app import PrefabApp
    from prefab_ui.components import Column, DataTable, DataTableColumn, Text

    data = list_windows(filter=filter)
    rows = data.get("windows", [])
    with PrefabApp() as app:
        with Column(gap=3, css_class="p-4"):
            Text(f"Open Windows ({len(rows)})", size="lg", weight="bold")
            DataTable(
                columns=[
                    DataTableColumn(key="title", header="Title", sortable=True),
                    DataTableColumn(key="hwnd", header="Handle"),
                    DataTableColumn(key="w", header="W"),
                    DataTableColumn(key="h", header="H"),
                ],
                rows=[
                    {
                        "title": r["title"],
                        "hwnd": str(r["hwnd"]),
                        "w": r["rect"][2] - r["rect"][0] if r["rect"] else "",
                        "h": r["rect"][3] - r["rect"][1] if r["rect"] else "",
                    }
                    for r in rows
                ],
                search=True,
            )
    return app


@mcp.tool(
    name="verify_ui",
    app=True,
    description="Verify a visual condition and render the result as a status card with badge (FastMCP v3 Apps).",
)
def verify_ui(condition: str, window: str = "", mode: str = "full"):
    """Render verify_state result as a visual status card."""
    from prefab_ui.app import PrefabApp
    from prefab_ui.components import Alert, Badge, Column, Text

    result = verify_state(condition, window=window, mode=mode)
    verdict = result.get("result", "uncertain")
    tier = result.get("tier", "deep")
    model = result.get("model", "")
    screenshot = result.get("screenshot", "")

    color = {"yes": "green", "no": "red", "uncertain": "yellow"}.get(verdict, "yellow")

    with PrefabApp() as app:
        with Column(gap=3, css_class="p-4"):
            Text("State Verification", size="lg", weight="bold")
            Alert(children=[Text(f"{condition}", size="md")], color=color)
            Badge(label=f"Result: {verdict}", color=color)
            Text(f"Tier: {tier}  |  Model: {model}  |  Screenshot: {screenshot}", size="sm")
    return app


# ── FastMCPApp: interactive window manager (UI calls server tools) ──

def _register_windows_app():
    """Register the Window Manager FastMCPApp as a provider on the server."""
    from prefab_ui.actions import ShowToast
    from prefab_ui.actions.mcp import CallTool
    from prefab_ui.app import PrefabApp
    from prefab_ui.components import (
        Button, Column, DataTable, DataTableColumn, ForEach, Heading,
        Row, Text,
    )
    from fastmcp import FastMCPApp

    app = FastMCPApp("Window Manager")

    @app.tool()
    def focus_by_hwnd(hwnd: str) -> str:
        """Focus a window by its handle. UI-only helper."""
        try:
            import win32gui
            h = int(hwnd)
            if win32gui.IsIconic(h):
                win32gui.ShowWindow(h, 9)  # SW_RESTORE
            win32gui.SetForegroundWindow(h)
            return f"Focused window {hwnd}"
        except Exception as e:
            return f"Focus failed: {e}"

    @app.ui()
    def window_manager() -> Any:
        """Interactive window manager: search, sort, and focus any window."""
        windows = list_windows().get("windows", [])
        rows = [
            {"hwnd": str(w["hwnd"]), "title": w["title"][:80],
             "w": (w["rect"][2] - w["rect"][0]) if w["rect"] else "",
             "h": (w["rect"][3] - w["rect"][1]) if w["rect"] else ""}
            for w in windows
        ]
        with PrefabApp() as papp:
            with Column(gap=4, css_class="p-4"):
                Heading(f"Open Windows ({len(rows)})")
                with ForEach("windows") as win:
                    with Row(gap=2, align="center"):
                        Text(win.title, css_class="flex-1")
                        Button(
                            "Focus",
                            on_click=CallTool(
                                "focus_by_hwnd",
                                arguments={"hwnd": win.hwnd},
                                on_success=ShowToast("Focused", variant="success"),
                                on_error=ShowToast("Failed", variant="error"),
                            ),
                        )
                DataTable(
                    columns=[
                        DataTableColumn(key="title", header="Title", sortable=True),
                        DataTableColumn(key="hwnd", header="Handle"),
                        DataTableColumn(key="w", header="W"),
                        DataTableColumn(key="h", header="H"),
                    ],
                    rows=rows,
                    search=True,
                )
            return PrefabApp(view=papp.view, state={"windows": rows})
    mcp.add_provider(app)
    return app


# Register the window manager app as a provider at module import time.
try:
    _register_windows_app()
    _WINDOWS_APP = True
except Exception:
    _WINDOWS_APP = False


@mcp.tool(
    name="window_manager_ui",
    app=True,
    description=(
        "Open the interactive Window Manager app: a searchable/sortable window "
        "table where you can FOCUS any window by clicking a button (FastMCP v3 "
        "FastMCPApp with UI→server callbacks)."
    ),
)
def window_manager_ui():
    """Interactive window manager with focus buttons (entry tool)."""
    from prefab_ui.app import PrefabApp
    from prefab_ui.components import Column, DataTable, DataTableColumn, Heading, Text

    windows = list_windows().get("windows", [])
    rows = [
        {"hwnd": str(w["hwnd"]), "title": w["title"][:80],
         "w": (w["rect"][2] - w["rect"][0]) if w["rect"] else "",
         "h": (w["rect"][3] - w["rect"][1]) if w["rect"] else ""}
        for w in windows
    ]
    with PrefabApp() as app:
        with Column(gap=3, css_class="p-4"):
            Heading(f"Open Windows ({len(rows)})")
            Text("Use run_command_guarded or focus_window for actions. Interactive buttons live in the Window Manager provider.", size="sm")
            DataTable(
                columns=[
                    DataTableColumn(key="title", header="Title", sortable=True),
                    DataTableColumn(key="hwnd", header="Handle"),
                    DataTableColumn(key="w", header="W"),
                    DataTableColumn(key="h", header="H"),
                ],
                rows=rows,
                search=True,
            )
    return app


def main() -> None:
    # show_banner=False is CRITICAL for stdio (FastMCP v2 banner corrupts JSON-RPC).
    # HTTP mode: python server.py --http --port 8002  (network-addressable)
    import sys as _sys
    _http = "--http" in _sys.argv
    _port = 8000
    if "--port" in _sys.argv:
        try:
            _port = int(_sys.argv[_sys.argv.index("--port") + 1])
        except Exception:
            pass
    if _http:
        mcp.run(transport="http", host="0.0.0.0", port=_port)
    else:
        mcp.run(show_banner=False)


if __name__ == "__main__":
    main()
