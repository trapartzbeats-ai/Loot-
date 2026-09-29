from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import DATA_DIR

WORKFLOWS_DIR = DATA_DIR / "workflows"
LIBRARY_DIR = WORKFLOWS_DIR / "library"
PRIVATE_DIR = WORKFLOWS_DIR / "private"

for d in [WORKFLOWS_DIR, LIBRARY_DIR, PRIVATE_DIR]:
    d.mkdir(parents=True, exist_ok=True)


class WorkflowEngine:
    """Manages declarative browser workflows (JSON-based)."""

    def __init__(self):
        self._loaded: Dict[str, Dict] = {}
        self._executions: Dict[str, Dict] = {}

    def create(self, definition: Dict[str, Any], scope: str = "private") -> str:
        """Create or update a workflow."""
        name = definition.get("name", "").strip()
        if not name:
            raise ValueError("Workflow must have a name")

        # Sanitize filename
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", name.lower().replace(" ", "_"))
        definition["_id"] = safe_name
        definition["_updated_at"] = time.time()
        definition["_scope"] = scope

        # Save to disk
        target_dir = LIBRARY_DIR if scope == "library" else PRIVATE_DIR
        path = target_dir / f"{safe_name}.json"
        path.write_text(json.dumps(definition, indent=2), encoding="utf-8")

        self._loaded[safe_name] = definition
        return safe_name

    def load(self, name: str, scope: str = "private") -> Dict:
        """Load a workflow from disk."""
        target_dir = LIBRARY_DIR if scope == "library" else PRIVATE_DIR
        path = target_dir / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"Workflow not found: {name}")
        wf = json.loads(path.read_text(encoding="utf-8"))
        self._loaded[name] = wf
        return wf

    def list_workflows(self, scope: str = "all") -> List[Dict[str, Any]]:
        """List available workflows."""
        results = []
        dirs = []
        if scope in ("all", "library"):
            dirs.append(LIBRARY_DIR)
        if scope in ("all", "private"):
            dirs.append(PRIVATE_DIR)

        for d in dirs:
            for path in sorted(d.glob("*.json")):
                try:
                    wf = json.loads(path.read_text(encoding="utf-8"))
                    results.append({
                        "name": wf.get("name", path.stem),
                        "id": wf.get("_id", path.stem),
                        "scope": wf.get("_scope", scope),
                        "steps": len(wf.get("steps", [])),
                        "description": wf.get("description", ""),
                    })
                except Exception:
                    continue
        return results

    def get(self, name: str) -> Optional[Dict]:
        """Get a loaded workflow."""
        return self._loaded.get(name)

    def validate(self, name: str, scope: str = "private") -> Dict[str, Any]:
        """Validate a workflow without executing."""
        try:
            wf = self.load(name, scope)
        except FileNotFoundError as e:
            return {"valid": False, "error": str(e)}

        errors = []
        steps = wf.get("steps", [])

        if not steps:
            errors.append("Workflow has no steps")

        for i, step in enumerate(steps):
            if "tool" not in step:
                errors.append(f"Step {i}: missing 'tool'")
            if "id" not in step:
                step["id"] = f"step_{i}"

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "name": wf.get("name"),
            "steps": len(steps),
        }

    def delete(self, name: str, scope: str = "private") -> bool:
        """Delete a workflow."""
        target_dir = LIBRARY_DIR if scope == "library" else PRIVATE_DIR
        path = target_dir / f"{name}.json"
        if path.exists():
            path.unlink()
            self._loaded.pop(name, None)
            return True
        return False

    def generate_from_description(self, description: str) -> Dict[str, Any]:
        """Generate a workflow JSON from natural language description."""
        # This creates a template that agents can refine
        # In production, this would call an LLM
        template = {
            "name": description[:60],
            "description": description,
            "version": "1.0",
            "inputs": {},
            "steps": [
                {
                    "id": "step_1",
                    "tool": "navigate",
                    "args": {"url": "https://example.com"},
                    "description": "Navigate to target URL",
                },
                {
                    "id": "step_2",
                    "tool": "page_to_text",
                    "args": {"max_chars": 5000},
                    "description": "Extract page content",
                },
            ],
            "_generated": True,
            "_needs_refinement": True,
        }
        return template


_engine = WorkflowEngine()


def get_engine() -> WorkflowEngine:
    """Get the global workflow engine."""
    return _engine
