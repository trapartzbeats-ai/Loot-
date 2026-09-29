from __future__ import annotations

import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import DATA_DIR, BASE_DIR

EXTENSIONS_DIR = BASE_DIR / "extensions"
STORE_DIR = BASE_DIR / "store"
INSTALLED_DIR = EXTENSIONS_DIR / "installed"
CATALOG_PATH = STORE_DIR / "catalog.json"

for d in [EXTENSIONS_DIR, STORE_DIR, INSTALLED_DIR]:
    d.mkdir(parents=True, exist_ok=True)


class ExtensionInfo:
    """Represents a loaded extension."""

    def __init__(self, ext_id: str, name: str, path: str, ext_type: str = "mv3",
                 manifest: dict = None, permissions: list = None):
        self.id = ext_id
        self.name = name
        self.path = path
        self.type = ext_type
        self.manifest = manifest or {}
        self.permissions = permissions or []
        self.version = self.manifest.get("version", "?")
        self.description = self.manifest.get("description", "")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "version": self.version,
            "description": self.description,
            "permissions": self.permissions,
            "path": str(self.path),
        }


class ExtensionManager:
    """Manages loading and discovery of Chrome extensions."""

    def __init__(self):
        self._loaded: Dict[str, ExtensionInfo] = {}
        self._extension_paths: List[str] = []

    def register_path(self, path: str) -> None:
        """Register an extension path for loading."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Extension not found: {path}")
        resolved = str(p.resolve())
        if resolved not in self._extension_paths:
            self._extension_paths.append(resolved)

    def get_launch_args(self) -> List[str]:
        """Get Chrome launch args for all registered extensions."""
        if not self._extension_paths:
            return []
        args = []
        for path in self._extension_paths:
            args.append(f"--disable-extensions-except={path}")
            args.append(f"--load-extension={path}")
        return args

    def load_from_path(self, path: str) -> ExtensionInfo:
        """Load an extension from a folder path."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Extension not found: {path}")

        manifest_path = p / "manifest.json"
        if not manifest_path.exists():
            raise ValueError(f"No manifest.json in {path}")

        with open(manifest_path) as f:
            manifest = json.load(f)

        name = manifest.get("name", p.name)
        ext_type = "zext" if (p / "plugin.json").exists() else "mv3"
        permissions = manifest.get("permissions", [])

        ext = ExtensionInfo(
            ext_id="",
            name=name,
            path=str(p.resolve()),
            ext_type=ext_type,
            manifest=manifest,
            permissions=permissions,
        )
        self._loaded[name] = ext
        self.register_path(str(p.resolve()))
        return ext

    def discover_extensions(self) -> List[ExtensionInfo]:
        """Discover all extensions in the extensions folder."""
        found = []
        for item in EXTENSIONS_DIR.iterdir():
            if item.is_dir() and (item / "manifest.json").exists():
                try:
                    ext = self.load_from_path(str(item))
                    found.append(ext)
                except Exception as e:
                    print(f"[extensions] Failed to load {item.name}: {e}")
        return found

    def get_loaded(self) -> List[ExtensionInfo]:
        """Get all loaded extensions."""
        return list(self._loaded.values())

    def get_by_name(self, name: str) -> Optional[ExtensionInfo]:
        """Get extension by name."""
        return self._loaded.get(name)

    def get_by_id(self, ext_id: str) -> Optional[ExtensionInfo]:
        """Get extension by ID."""
        for ext in self._loaded.values():
            if ext.id == ext_id:
                return ext
        return None

    def set_extension_id(self, name: str, ext_id: str) -> None:
        """Set the extension ID (extracted from service worker at runtime)."""
        if name in self._loaded:
            self._loaded[name].id = ext_id

    def list_to_dict(self) -> List[dict]:
        """List all loaded extensions as dicts."""
        return [ext.to_dict() for ext in self._loaded.values()]


def extract_extension_id(service_worker_url: str) -> str:
    """Extract extension ID from service worker URL."""
    # URL format: chrome-extension://<ID>/background.js
    try:
        return service_worker_url.split("/")[2]
    except (IndexError, AttributeError):
        return ""


async def get_extension_ids_from_context(context) -> Dict[str, str]:
    """Get extension IDs from service workers using event-based detection."""
    ids = {}
    try:
        sws = context.service_workers
        for sw in sws:
            url = sw.url
            ext_id = extract_extension_id(url)
            if ext_id:
                ids[ext_id] = url
    except AttributeError:
        # Fallback: service workers not available in this Playwright version
        pass
    return ids


async def wait_for_service_worker(context, timeout: int = 5000) -> Optional[str]:
    """Wait for a service worker to appear. Returns its URL."""
    try:
        sw = await context.wait_for_event("serviceworker", timeout=timeout)
        return sw.url
    except Exception:
        return None


def parse_manifest(path: str) -> dict:
    """Parse an extension's manifest.json."""
    manifest_path = Path(path) / "manifest.json"
    if not manifest_path.exists():
        return {}
    with open(manifest_path) as f:
        return json.load(f)


def get_extension_popup_url(ext_id: str, manifest: dict) -> str:
    """Get the popup HTML URL for an extension."""
    action = manifest.get("action", {})
    popup = action.get("default_popup", "")
    if not popup:
        popup = manifest.get("browser_action", {}).get("default_popup", "")
    if popup:
        return f"chrome-extension://{ext_id}/{popup}"
    return ""


def get_extension_options_url(ext_id: str, manifest: dict) -> str:
    """Get the options page URL for an extension."""
    options = manifest.get("options_page", "")
    if not options:
        options = manifest.get("options_ui", {}).get("page", "")
    if options:
        return f"chrome-extension://{ext_id}/{options}"
    return ""


def download_extension(url: str, output_path: str) -> bool:
    """Download an extension from a URL."""
    try:
        urllib.request.urlretrieve(url, output_path)
        return True
    except Exception as e:
        print(f"[extensions] Download failed: {e}")
        return False


def extract_crx(crx_path: str, output_dir: str) -> bool:
    """Extract a .crx file to a directory."""
    try:
        with zipfile.ZipFile(crx_path, 'r') as z:
            z.extractall(output_dir)
        return True
    except Exception as e:
        print(f"[extensions] Extract failed: {e}")
        return False


def catalog_list() -> Dict[str, Any]:
    """List validated local/remote extension catalog entries and install state."""
    if not CATALOG_PATH.exists():
        return {"count": 0, "extensions": [], "path": str(CATALOG_PATH)}
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    items = []
    for raw in payload.get("extensions", []):
        ext_id = str(raw.get("id", ""))
        if not re_fullmatch_id(ext_id):
            continue
        target = INSTALLED_DIR / ext_id
        items.append({**raw, "installed": target.exists(), "install_path": str(target)})
    return {"count": len(items), "extensions": items, "path": str(CATALOG_PATH)}


def re_fullmatch_id(value: str) -> bool:
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9._-]{1,100}", value))


def _safe_extract_zip(path: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if destination_resolved not in target.parents and target != destination_resolved:
                raise ValueError("catalog package contains an unsafe path")
        archive.extractall(destination)


def catalog_install(ext_id: str, update: bool = False) -> Dict[str, Any]:
    """Install one catalog package with staging and path-traversal protection."""
    if not re_fullmatch_id(ext_id):
        return {"installed": False, "error": "invalid extension id"}
    catalog = catalog_list()
    entry = next((item for item in catalog["extensions"] if item.get("id") == ext_id), None)
    if not entry:
        return {"installed": False, "error": "extension not found in catalog", "id": ext_id}
    target = INSTALLED_DIR / ext_id
    if target.exists() and not update:
        return {"installed": True, "already_installed": True, "id": ext_id, "path": str(target)}
    source = str(entry.get("source", ""))
    if not source:
        return {"installed": False, "error": "catalog entry has no source", "id": ext_id}
    stage_root = Path(tempfile.mkdtemp(prefix="loot-extension-", dir=str(STORE_DIR)))
    try:
        source_path: Path
        if source.startswith(("http://", "https://")):
            package = stage_root / "package.zip"
            urllib.request.urlretrieve(source, package)
            source_path = package
        else:
            source_path = (BASE_DIR / source).resolve() if not Path(source).is_absolute() else Path(source).resolve()
        unpacked = stage_root / "unpacked"
        if source_path.is_dir():
            shutil.copytree(source_path, unpacked)
        elif zipfile.is_zipfile(source_path):
            unpacked.mkdir()
            _safe_extract_zip(source_path, unpacked)
        else:
            return {"installed": False, "error": "source must be an extension directory or zip", "id": ext_id}
        roots = [unpacked] + [p for p in unpacked.iterdir() if p.is_dir()]
        root = next((p for p in roots if (p / "manifest.json").exists() or (p / "plugin.json").exists()), None)
        if not root:
            return {"installed": False, "error": "package has no manifest.json or plugin.json", "id": ext_id}
        staged_target = INSTALLED_DIR / f".{ext_id}.staging"
        if staged_target.exists():
            shutil.rmtree(staged_target)
        shutil.copytree(root, staged_target)
        if target.exists():
            shutil.rmtree(target)
        staged_target.replace(target)
        if (target / "manifest.json").exists():
            get_manager().load_from_path(str(target))
        return {"installed": True, "updated": bool(update), "id": ext_id, "version": entry.get("version", ""), "path": str(target)}
    except Exception as exc:
        return {"installed": False, "error": str(exc)[:500], "id": ext_id}
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


_manager = ExtensionManager()


def get_manager() -> ExtensionManager:
    """Get the global extension manager."""
    return _manager
