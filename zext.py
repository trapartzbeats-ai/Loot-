from __future__ import annotations

import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import BASE_DIR, DATA_DIR

ZEXT_DIR = BASE_DIR / "extensions"
INSTALLED_DIR = ZEXT_DIR / "installed"


class ZextAPI:
    """Custom API exposed to .zext extensions (Route B)."""

    def __init__(self, extension: "ZextExtension"):
        self.ext = extension

    def tabs_current(self) -> Dict[str, Any]:
        """Get current tab info."""
        from browser import _get_page
        import asyncio
        page = asyncio.get_event_loop().run_until_complete(_get_page())
        return {"url": page.url, "title": asyncio.get_event_loop().run_until_complete(page.title())}

    def tabs_list(self) -> List[Dict[str, str]]:
        """List all tabs."""
        from browser import _get_context
        import asyncio
        context = asyncio.get_event_loop().run_until_complete(_get_context())
        tabs = []
        for i, p in enumerate(context.pages):
            tabs.append({"id": i, "url": p.url, "title": asyncio.get_event_loop().run_until_complete(p.title())})
        return tabs

    def page_get_html(self) -> str:
        """Get current page HTML."""
        from browser import _get_page
        import asyncio
        page = asyncio.get_event_loop().run_until_complete(_get_page())
        return asyncio.get_event_loop().run_until_complete(page.content())

    def page_get_text(self) -> str:
        """Get current page text."""
        from browser import _get_page
        import asyncio
        page = asyncio.get_event_loop().run_until_complete(_get_page())
        return asyncio.get_event_loop().run_until_complete(page.evaluate("() => document.body.innerText"))

    def page_inject_script(self, fn: str) -> Any:
        """Inject JavaScript into current page."""
        from browser import _get_page
        import asyncio
        page = asyncio.get_event_loop().run_until_complete(_get_page())
        return asyncio.get_event_loop().run_until_complete(page.evaluate(fn))

    def page_inject_css(self, css: str) -> None:
        """Inject CSS into current page."""
        from browser import _get_page
        import asyncio
        page = asyncio.get_event_loop().run_until_complete(_get_page())
        asyncio.get_event_loop().run_until_complete(
            page.evaluate(f"(() => {{ const s = document.createElement('style'); s.textContent = `{css}`; document.head.appendChild(s); }})()")
        )

    def screenshot(self) -> str:
        """Take screenshot, return base64."""
        from browser import _get_page
        import asyncio
        page = asyncio.get_event_loop().run_until_complete(_get_page())
        png = asyncio.get_event_loop().run_until_complete(page.screenshot())
        import base64
        return base64.b64encode(png).decode()

    def storage_get(self, key: str) -> Any:
        """Get value from extension storage."""
        storage_file = DATA_DIR / "zext_storage" / f"{self.ext.id}.json"
        storage_file.parent.mkdir(parents=True, exist_ok=True)
        if storage_file.exists():
            with open(storage_file) as f:
                data = json.load(f)
            return data.get(key)
        return None

    def storage_set(self, key: str, value: Any) -> None:
        """Set value in extension storage."""
        storage_file = DATA_DIR / "zext_storage" / f"{self.ext.id}.json"
        storage_file.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if storage_file.exists():
            with open(storage_file) as f:
                data = json.load(f)
        data[key] = value
        with open(storage_file, "w") as f:
            json.dump(data, f)


class ZextExtension:
    """Represents a loaded .zext extension (Route B)."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.manifest = self._load_manifest()
        self.id = self.manifest.get("id", self.path.name)
        self.name = self.manifest.get("name", self.path.name)
        self.version = self.manifest.get("version", "1.0")
        self.description = self.manifest.get("description", "")
        self.permissions = self.manifest.get("permissions", [])
        self.agent_tools: List[dict] = self.manifest.get("agent_tools", [])
        self._api = ZextAPI(self)
        self._tool_cache: Dict[str, Callable] = {}

    def _load_manifest(self) -> dict:
        manifest_path = self.path / "plugin.json"
        if manifest_path.exists():
            with open(manifest_path) as f:
                return json.load(f)
        return {}

    def get_tool_function(self, tool_name: str) -> Optional[Callable]:
        """Get the function for a tool."""
        if tool_name in self._tool_cache:
            return self._tool_cache[tool_name]

        tool_def = None
        for t in self.agent_tools:
            if t["name"] == tool_name:
                tool_def = t
                break
        if not tool_def:
            return None

        entry = tool_def.get("entry", "")
        if entry.endswith(".py"):
            func = self._load_python_tool(entry)
        else:
            func = self._load_inline_tool(tool_def)

        if func:
            self._tool_cache[tool_name] = func
        return func

    def _load_python_tool(self, entry: str) -> Optional[Callable]:
        """Load a Python tool from file."""
        tool_path = self.path / entry
        if not tool_path.exists():
            return None
        try:
            spec = importlib.util.spec_from_file_location(f"zext_{self.id}_{entry.replace('/', '_')}", str(tool_path))
            module = importlib.util.module_from_spec(spec)
            module.browser = self._api
            module.storage = self._api
            spec.loader.exec_module(module)
            if hasattr(module, "run"):
                return module.run
            return None
        except Exception as e:
            print(f"[zext] Failed to load {entry}: {e}")
            return None

    def _load_inline_tool(self, tool_def: dict) -> Optional[Callable]:
        """Create a tool function from inline definition."""
        description = tool_def.get("description", "")
        params = tool_def.get("parameters", {})

        async def inline_tool(**kwargs) -> dict:
            return {"tool": tool_def["name"], "params": kwargs, "note": "Inline tool - implement in .py"}

        inline_tool.__name__ = tool_def["name"]
        inline_tool.__doc__ = description
        return inline_tool


class ZextManager:
    """Manages .zext extensions."""

    def __init__(self):
        self._loaded: Dict[str, ZextExtension] = {}

    def load(self, path: str) -> ZextExtension:
        """Load a .zext extension."""
        ext = ZextExtension(path)
        self._loaded[ext.id] = ext
        return ext

    def discover(self) -> List[ZextExtension]:
        """Discover .zext extensions."""
        found = []
        for item in INSTALLED_DIR.iterdir():
            if item.is_dir() and (item / "plugin.json").exists():
                try:
                    ext = self.load(str(item))
                    found.append(ext)
                except Exception as e:
                    print(f"[zext] Failed to load {item.name}: {e}")
        return found

    def get(self, ext_id: str) -> Optional[ZextExtension]:
        return self._loaded.get(ext_id)

    def list_all(self) -> List[dict]:
        return [{"id": e.id, "name": e.name, "version": e.version, "tools": len(e.agent_tools)} for e in self._loaded.values()]

    def get_all_tools(self) -> Dict[str, tuple]:
        """Get all tools from all loaded extensions. Returns {name: (function, ext_id)}."""
        tools = {}
        for ext in self._loaded.values():
            for tool_def in ext.agent_tools:
                func = ext.get_tool_function(tool_def["name"])
                if func:
                    tools[tool_def["name"]] = (func, ext.id)
        return tools


_manager = ZextManager()


def get_manager() -> ZextManager:
    return _manager
