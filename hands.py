from __future__ import annotations

import subprocess
from typing import Any, Dict, List, Optional


def run_command(cmd: str, timeout: int = 30) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout,
            creationflags=0x08000000,
        )
        return {"stdout": result.stdout.strip(), "stderr": result.stderr.strip(), "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out", "returncode": -1}
    except Exception as e:
        return {"error": str(e), "returncode": -1}


def launch_app(path: str) -> Dict[str, Any]:
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", f"Start-Process '{path}'"],
            creationflags=0x08000000,
        )
        return {"launched": True, "path": path}
    except Exception as e:
        return {"launched": False, "error": str(e)}


def list_windows() -> Dict[str, Any]:
    try:
        import uiautomation as auto
        windows = []
        for w in auto.GetRootControl().GetChildren():
            if w.ControlType == auto.ControlType.WindowControl and w.Name:
                windows.append({"title": w.Name, "handle": w.NativeWindowHandle})
        return {"windows": windows, "count": len(windows)}
    except Exception as e:
        return {"windows": [], "error": str(e)}


def focus_window(title: str) -> Dict[str, Any]:
    try:
        import uiautomation as auto
        w = auto.WindowControl(searchDepth=1, SubName=title)
        if w.Exists(0, 0):
            w.SetFocus()
            w.ShowWindow(auto.ShowWindow.ShowNormal)
            return {"focused": True, "title": w.Name}
        return {"focused": False, "error": "Window not found"}
    except Exception as e:
        return {"focused": False, "error": str(e)}


def click_element(name: str = "", auto_id: str = "", className: str = "") -> Dict[str, Any]:
    try:
        import uiautomation as auto
        if name:
            ctrl = auto.ButtonControl(searchDepth=3, Name=name)
            if ctrl.Exists(0, 0):
                ctrl.Click()
                return {"clicked": True, "name": name}
        if auto_id:
            ctrl = auto.ButtonControl(searchDepth=3, AutomationId=auto_id)
            if ctrl.Exists(0, 0):
                ctrl.Click()
                return {"clicked": True, "auto_id": auto_id}
        return {"clicked": False, "error": "Element not found"}
    except Exception as e:
        return {"clicked": False, "error": str(e)}


def type_text_ctrl(text: str, handle: int = 0) -> Dict[str, Any]:
    try:
        import uiautomation as auto
        if handle:
            ctrl = auto.ControlFromHandle(handle)
        else:
            ctrl = auto.GetFocusedControl()
        if ctrl:
            ctrl.SendKeys(text)
            return {"typed": True, "text": text}
        return {"typed": False, "error": "No focused control"}
    except Exception as e:
        return {"typed": False, "error": str(e)}


def press_hotkey(keys: str) -> Dict[str, Any]:
    try:
        import uiautomation as auto
        auto.SendKeys("{" + keys + "}")
        return {"pressed": keys}
    except Exception as e:
        return {"pressed": False, "error": str(e)}
