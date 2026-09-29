from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import DATA_DIR

APPROVALS_DIR = DATA_DIR / "approvals"
APPROVALS_DIR.mkdir(parents=True, exist_ok=True)

# Actions that require human approval by default
DEFAULT_APPROVAL_REQUIRED = {
    "forms", "purchases", "deletions", "account_changes", "publishing"
}

# Keywords that indicate write actions
WRITE_KEYWORDS = {
    "forms": ["submit", "form", "send", "post"],
    "purchases": ["buy", "purchase", "checkout", "pay", "order", "cart"],
    "deletions": ["delete", "remove", "trash", "destroy", "purge"],
    "account_changes": ["password", "email", "settings", "profile", "update account"],
    "publishing": ["comment", "post", "publish", "share", "tweet", "reply"],
}


class ApprovalRequest:
    """Represents a pending approval request."""

    def __init__(self, action: str, context: Dict[str, Any], tool: str = "", args: Dict = None):
        self.id = str(uuid.uuid4())[:12]
        self.action = action
        self.context = context
        self.tool = tool
        self.args = args or {}
        self.status = "pending"
        self.created_at = time.time()
        self.resolved_at = None
        self.decision = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "action": self.action,
            "context": self.context,
            "tool": self.tool,
            "args": self.args,
            "status": self.status,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "decision": self.decision,
        }


class ApprovalManager:
    """Manages human-in-the-loop approval gates."""

    def __init__(self):
        self._pending: Dict[str, ApprovalRequest] = {}
        self._history: List[ApprovalRequest] = []
        self._policy: set = set(DEFAULT_APPROVAL_REQUIRED)

    def set_policy(self, actions: List[str]) -> None:
        """Configure which action types require approval."""
        self._policy = set(actions)

    def get_policy(self) -> List[str]:
        """Get current approval policy."""
        return sorted(self._policy)

    def requires_approval(self, tool: str, args: Dict[str, Any]) -> Optional[str]:
        """Check if a tool call requires approval. Returns action type or None."""
        tool_lower = tool.lower()
        args_str = json.dumps(args).lower()

        for action_type in self._policy:
            keywords = WRITE_KEYWORDS.get(action_type, [])
            for kw in keywords:
                if kw in tool_lower or kw in args_str:
                    return action_type
        return None

    def request(self, action: str, context: Dict[str, Any], tool: str = "", args: Dict = None) -> str:
        """Create an approval request. Returns approval ID."""
        req = ApprovalRequest(action, context, tool, args)
        self._pending[req.id] = req
        self._notify_user(req)
        return req.id

    def approve(self, approval_id: str) -> Dict[str, Any]:
        """Approve a pending request."""
        req = self._pending.pop(approval_id, None)
        if not req:
            return {"error": "Approval not found", "id": approval_id}
        req.status = "approved"
        req.decision = "approved"
        req.resolved_at = time.time()
        self._history.append(req)
        self._save_to_disk(req)
        return {"approved": True, "id": approval_id, "action": req.action}

    def reject(self, approval_id: str) -> Dict[str, Any]:
        """Reject a pending request."""
        req = self._pending.pop(approval_id, None)
        if not req:
            return {"error": "Approval not found", "id": approval_id}
        req.status = "rejected"
        req.decision = "rejected"
        req.resolved_at = time.time()
        self._history.append(req)
        self._save_to_disk(req)
        return {"rejected": True, "id": approval_id, "action": req.action}

    def pending(self) -> List[Dict[str, Any]]:
        """List all pending approval requests."""
        return [req.to_dict() for req in self._pending.values()]

    def history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get approval history."""
        return [req.to_dict() for req in self._history[-limit:]]

    def is_pending(self, approval_id: str) -> bool:
        """Check if an approval is still pending."""
        return approval_id in self._pending

    def _notify_user(self, req: ApprovalRequest) -> None:
        """Notify user via system toast + console log."""
        message = f"Approval needed: {req.action} - {req.context.get('description', 'No description')}"

        # Console log (always works)
        print(f"\n[APPROVAL NEEDED] {message}")
        print(f"  ID: {req.id}")
        print(f"  Tool: {req.tool}")
        print(f"  Call pending_approvals() to review\n")

        # Windows toast notification (best effort)
        try:
            from win10toast import ToastNotifier
            toaster = ToastNotifier()
            toaster.show_toast(
                "Loot Browser - Approval Needed",
                message,
                duration=10,
                threaded=True
            )
        except Exception:
            pass  # Toast not available, console log is backup

    def _save_to_disk(self, req: ApprovalRequest) -> None:
        """Persist approval record to disk."""
        try:
            path = APPROVALS_DIR / f"{req.id}.json"
            path.write_text(json.dumps(req.to_dict(), indent=2), encoding="utf-8")
        except Exception:
            pass


_manager = ApprovalManager()


def get_manager() -> ApprovalManager:
    """Get the global approval manager."""
    return _manager
