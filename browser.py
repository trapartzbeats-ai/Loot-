from __future__ import annotations

import base64
import asyncio
import contextvars
import io
import json
import os
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from config import (
    USER_AGENT, DATA_DIR, IGNORE_HTTPS_ERRORS, SESSION_TTL_SECONDS,
    NETWORK_LOG_LIMIT, CONSOLE_LOG_LIMIT, BROWSER_MODE, VIEWPORT_WIDTH,
    VIEWPORT_HEIGHT, ANTI_DETECT, AUTO_DISMISS_DIALOGS, AUTO_BLOCK_ADS,
    VISUAL_RECOVERY, CAPTURE_CLOSED_SHADOW,
)
import extensions

_playwright = None
_browser = None
_browser_pool: Dict[str, Any] = {}
_client_scope = contextvars.ContextVar("loot_browser_client_scope", default="local")
_shared_bindings: Dict[str, str] = {}


@dataclass
class ClientState:
    context: Any = None
    page: Any = None
    current_session: str = "default"
    engine: str = "chromium"
    stealth: bool = False
    mode: str = BROWSER_MODE
    viewport_width: int = VIEWPORT_WIDTH
    viewport_height: int = VIEWPORT_HEIGHT
    auto_block_ads: bool = AUTO_BLOCK_ADS
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    requested_url: str = ""
    network_events: Any = field(default_factory=lambda: deque(maxlen=NETWORK_LOG_LIMIT))
    console_events: Any = field(default_factory=lambda: deque(maxlen=CONSOLE_LOG_LIMIT))
    route_specs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    route_handlers: Dict[str, Any] = field(default_factory=dict)
    adblock_handler: Any = None
    attached_pages: set = field(default_factory=set)
    dialogs_dismissed: int = 0
    blocked_ads: int = 0
    pending_tabs: List[str] = field(default_factory=list)
    pending_active_tab: int = 0
    metadata_loaded_for: str = ""
    handoff_event: Any = None
    handoff_token: str = ""
    lease_owner: str = ""
    lease_token: str = ""
    lease_expires_at: float = 0.0


_client_states: Dict[str, ClientState] = {}

SESSION_STATE_DIR = DATA_DIR / "sessions"
SESSION_STATE_DIR.mkdir(parents=True, exist_ok=True)
BROWSER_META_DIR = DATA_DIR / "browser_sessions"
BROWSER_META_DIR.mkdir(parents=True, exist_ok=True)
EXTENSION_PROFILE_DIR = DATA_DIR / "extension_profiles"
EXTENSION_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

MODE_CONFIG = {
    "headless": {
        "headless": True,
        "extra_args": [],
        "anti_detect": False,
        "mute_audio": False,
        "disable_notifications": False,
        "auto_dismiss_dialogs": False,
    },
    "headed": {
        "headless": False,
        "extra_args": ["--disable-features=TranslateUI", "--no-first-run", "--disable-infobars", "--start-maximized"],
        "anti_detect": True,
        "mute_audio": False,
        "disable_notifications": False,
        "auto_dismiss_dialogs": False,
    },
    "silent": {
        "headless": True,
        "extra_args": ["--disable-features=TranslateUI", "--no-first-run", "--disable-infobars", "--mute-audio", "--disable-notifications", "--disable-popup-blocking"],
        "anti_detect": True,
        "mute_audio": True,
        "disable_notifications": True,
        "auto_dismiss_dialogs": True,
    },
}

_AD_HOST_RE = re.compile(
    r"(?:^|\.)(?:doubleclick\.net|googlesyndication\.com|googleadservices\.com|adnxs\.com|"
    r"amazon-adsystem\.com|scorecardresearch\.com|taboola\.com|outbrain\.com|adsrvr\.org)$",
    re.I,
)

def set_client_scope(scope: str):
    """Bind browser state to one MCP client for the duration of a request."""
    return _client_scope.set(scope or "local")


def reset_client_scope(token) -> None:
    _client_scope.reset(token)


def get_client_scope() -> str:
    return _client_scope.get()


def get_effective_scope() -> str:
    scope = get_client_scope()
    return _shared_bindings.get(scope, scope)


def _state() -> ClientState:
    scope = get_effective_scope()
    if scope not in _client_states:
        _client_states[scope] = ClientState()
    return _client_states[scope]


def _shared_participants(effective_scope: str = "") -> List[str]:
    effective_scope = effective_scope or get_effective_scope()
    return sorted(client for client, shared in _shared_bindings.items() if shared == effective_scope)


async def shared_browser_join(name: str) -> Dict[str, Any]:
    """Opt this MCP client into a named shared browser room."""
    room = _safe_session_id(name)
    client = get_client_scope()
    effective = f"shared:{room}"
    _shared_bindings[client] = effective
    state = _state()
    return {
        "joined": True, "room": room, "client_scope": client,
        "effective_scope": effective, "participants": _shared_participants(effective),
        "has_context": bool(state.context),
        "warning": "Shared participants can see and change the same tabs, cookies, and page state.",
    }


async def shared_browser_leave() -> Dict[str, Any]:
    """Return this MCP client to its private browser state without closing the room."""
    client = get_client_scope()
    previous = _shared_bindings.pop(client, None)
    return {
        "left": bool(previous),
        "room": previous.removeprefix("shared:") if previous else "",
        "client_scope": client, "effective_scope": get_effective_scope(),
    }


async def shared_browser_status() -> Dict[str, Any]:
    client = get_client_scope()
    effective = get_effective_scope()
    state = _state()
    return {
        "shared": effective.startswith("shared:"),
        "room": effective.removeprefix("shared:") if effective.startswith("shared:") else "",
        "client_scope": client, "effective_scope": effective,
        "participants": _shared_participants(effective) if effective.startswith("shared:") else [client],
        "context_id": id(state.context) if state.context else None,
        "page_id": id(state.page) if state.page else None,
        "url": state.page.url if state.page and not state.page.is_closed() else "",
        "lease": _shared_lease_summary(state),
    }


def _expire_shared_lease(state: ClientState) -> None:
    if state.lease_expires_at and state.lease_expires_at <= time.time():
        state.lease_owner = ""
        state.lease_token = ""
        state.lease_expires_at = 0.0


def _shared_lease_summary(state: Optional[ClientState] = None) -> Dict[str, Any]:
    state = state or _state()
    _expire_shared_lease(state)
    return {
        "held": bool(state.lease_owner),
        "owner": state.lease_owner,
        "owned_by_client": state.lease_owner == get_client_scope(),
        "expires_in_seconds": max(0, round(state.lease_expires_at - time.time(), 1)) if state.lease_owner else 0,
    }


async def shared_browser_acquire(ttl_seconds: int = 30) -> Dict[str, Any]:
    """Acquire or renew the cooperative mutation lease for a shared room."""
    effective = get_effective_scope()
    if not effective.startswith("shared:"):
        return {"acquired": False, "error": "client is not in a shared browser room"}
    state = _state()
    _expire_shared_lease(state)
    client = get_client_scope()
    if state.lease_owner and state.lease_owner != client:
        return {"acquired": False, "room": effective.removeprefix("shared:"), "lease": _shared_lease_summary(state)}
    state.lease_owner = client
    state.lease_token = state.lease_token or secrets.token_urlsafe(18)
    state.lease_expires_at = time.time() + max(5, min(int(ttl_seconds), 300))
    return {"acquired": True, "room": effective.removeprefix("shared:"), "token": state.lease_token,
            "lease": _shared_lease_summary(state)}


async def shared_browser_release(token: str) -> Dict[str, Any]:
    state = _state()
    _expire_shared_lease(state)
    if not state.lease_owner:
        return {"released": False, "error": "no mutation lease is held"}
    if state.lease_owner != get_client_scope() or not secrets.compare_digest(state.lease_token, token or ""):
        return {"released": False, "error": "lease token or owner does not match", "lease": _shared_lease_summary(state)}
    state.lease_owner = ""
    state.lease_token = ""
    state.lease_expires_at = 0.0
    return {"released": True}


def shared_mutation_allowed() -> Dict[str, Any]:
    effective = get_effective_scope()
    if not effective.startswith("shared:") or len(_shared_participants(effective)) <= 1:
        return {"allowed": True}
    state = _state()
    _expire_shared_lease(state)
    allowed = state.lease_owner == get_client_scope()
    return {
        "allowed": allowed, "room": effective.removeprefix("shared:"),
        "reason": "lease_owned" if allowed else "shared room mutation lease required",
        "lease": _shared_lease_summary(state),
        "fix": "Call shared_browser_acquire, perform the mutation, then shared_browser_release.",
    }


def get_current_session() -> str:
    return _state().current_session


def get_active_context():
    return _state().context


def get_active_page():
    return _state().page


def _safe_session_id(session_id: str) -> str:
    value = (session_id or "default").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", value):
        raise ValueError("session_id may contain only letters, numbers, dot, underscore, and hyphen")
    return value


def _touch(state: Optional[ClientState] = None) -> None:
    (state or _state()).last_activity = time.time()


def _mode_config(state: Optional[ClientState] = None) -> Dict[str, Any]:
    return MODE_CONFIG[(state or _state()).mode]


def _effective_stealth(state: Optional[ClientState] = None) -> bool:
    state = state or _state()
    return bool(state.stealth or (ANTI_DETECT and _mode_config(state)["anti_detect"]))


def _safe_persisted_url(url: str) -> str:
    """Keep useful tab URLs without writing common credential parameters."""
    if not url or url == "about:blank":
        return url or "about:blank"
    try:
        parts = urlsplit(url)
        sensitive = re.compile(r"token|secret|password|passwd|code|key|session|auth", re.I)
        query = urlencode([(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if not sensitive.search(key)])
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, query, ""))
    except Exception:
        return "about:blank"


def _metadata_path(session_id: str):
    return BROWSER_META_DIR / f"{_safe_session_id(session_id)}.json"


async def _save_browser_metadata(state: Optional[ClientState] = None) -> None:
    state = state or _state()
    tabs: List[str] = []
    active = 0
    if state.context:
        pages = [page for page in state.context.pages if not page.is_closed()]
        tabs = [_safe_persisted_url(page.url) for page in pages]
        if state.page in pages:
            active = pages.index(state.page)
    elif state.pending_tabs:
        tabs = [_safe_persisted_url(url) for url in state.pending_tabs]
        active = state.pending_active_tab
    state.pending_tabs = list(tabs)
    state.pending_active_tab = active
    payload = {
        "version": 1,
        "session_id": state.current_session,
        "mode": state.mode,
        "engine": state.engine,
        "stealth": state.stealth,
        "viewport": {"width": state.viewport_width, "height": state.viewport_height},
        "auto_block_ads": state.auto_block_ads,
        "tabs": tabs,
        "active_tab": max(0, min(active, max(0, len(tabs) - 1))),
        "updated_at": round(time.time()),
    }
    path = _metadata_path(state.current_session)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp.replace(path)


def _load_browser_metadata(state: ClientState, session_id: str) -> None:
    path = _metadata_path(session_id)
    state.metadata_loaded_for = session_id
    if not path.exists():
        state.pending_tabs = []
        state.pending_active_tab = 0
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        mode = payload.get("mode", BROWSER_MODE)
        engine = payload.get("engine", "chromium")
        if mode in MODE_CONFIG:
            state.mode = mode
        if engine in {"chromium", "firefox", "webkit"}:
            state.engine = engine
        state.stealth = bool(payload.get("stealth", False))
        viewport = payload.get("viewport") or {}
        state.viewport_width = max(320, min(int(viewport.get("width", VIEWPORT_WIDTH)), 7680))
        state.viewport_height = max(240, min(int(viewport.get("height", VIEWPORT_HEIGHT)), 4320))
        state.auto_block_ads = bool(payload.get("auto_block_ads", AUTO_BLOCK_ADS))
        state.pending_tabs = [url for url in payload.get("tabs", []) if isinstance(url, str)][:25]
        state.pending_active_tab = max(0, int(payload.get("active_tab", 0)))
    except Exception:
        state.pending_tabs = []
        state.pending_active_tab = 0


def _attach_page(page, state: ClientState) -> None:
    marker = id(page)
    if marker in state.attached_pages:
        return
    state.attached_pages.add(marker)

    def on_console(message):
        try:
            state.console_events.append({
                "timestamp": round(time.time() * 1000),
                "type": message.type,
                "text": message.text[:4000],
                "url": page.url,
                "location": message.location,
            })
        except Exception:
            pass

    def on_page_error(error):
        state.console_events.append({
            "timestamp": round(time.time() * 1000),
            "type": "pageerror",
            "text": str(error)[:4000],
            "url": page.url,
        })

    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    if _mode_config(state)["auto_dismiss_dialogs"] and AUTO_DISMISS_DIALOGS:
        async def dismiss_dialog(dialog):
            try:
                state.console_events.append({
                    "timestamp": round(time.time() * 1000),
                    "type": "dialog-dismissed",
                    "text": f"{dialog.type}: {dialog.message}"[:4000],
                    "url": page.url,
                })
                state.dialogs_dismissed += 1
                await dialog.dismiss()
            except Exception:
                pass

        page.on("dialog", dismiss_dialog)


async def _install_adblock(state: ClientState) -> None:
    if not state.context or not state.auto_block_ads:
        return

    async def adblock_handler(route, request):
        try:
            hostname = (urlsplit(request.url).hostname or "").lower()
            if _AD_HOST_RE.search(hostname):
                state.blocked_ads += 1
                await route.abort("blockedbyclient")
                return
        except Exception:
            pass
        await route.continue_()

    state.adblock_handler = adblock_handler
    await state.context.route("**/*", adblock_handler)


async def _install_route(state: ClientState, pattern: str, spec: Dict[str, Any]) -> None:
    if not state.context:
        return

    async def handler(route, request):
        wanted_method = spec.get("method", "")
        if wanted_method and request.method.upper() != wanted_method:
            await route.continue_()
            return
        headers = dict(spec.get("headers") or {})
        if spec.get("content_type"):
            headers.setdefault("content-type", spec["content_type"])
        await route.fulfill(
            status=spec.get("status", 200),
            body=spec.get("body", ""),
            headers=headers,
        )

    state.route_handlers[pattern] = handler
    await state.context.route(pattern, handler)


async def _attach_context(context, state: ClientState) -> None:
    def on_response(response):
        try:
            request = response.request
            state.network_events.append({
                "timestamp": round(time.time() * 1000),
                "method": request.method,
                "url": response.url,
                "status": response.status,
                "ok": response.ok,
                "resourceType": request.resource_type,
                "frameUrl": request.frame.url if request.frame else "",
            })
        except Exception:
            pass

    def on_failed(request):
        try:
            state.network_events.append({
                "timestamp": round(time.time() * 1000),
                "method": request.method,
                "url": request.url,
                "status": None,
                "ok": False,
                "resourceType": request.resource_type,
                "failure": request.failure,
                "frameUrl": request.frame.url if request.frame else "",
            })
        except Exception:
            pass

    context.on("response", on_response)
    context.on("requestfailed", on_failed)
    context.on("page", lambda page: _attach_page(page, state))

    def complete_handoff(source, token):
        if token == state.handoff_token and state.handoff_event is not None:
            state.handoff_event.set()

    await context.expose_binding("__lootUserDone", complete_handoff)
    if CAPTURE_CLOSED_SHADOW:
        await context.add_init_script("""
            (() => {
                const proto = Element.prototype;
                const original = proto.attachShadow;
                if (!original || original.__lootClosedShadowCapture) return;
                function attachShadow(init = {mode: 'open'}) {
                    const requestedMode = (init && init.mode) || 'open';
                    const effectiveInit = requestedMode === 'closed'
                        ? Object.assign({}, init, {mode: 'open'}) : init;
                    const root = original.call(this, effectiveInit);
                    if (requestedMode === 'closed') {
                        this.setAttribute('data-loot-original-shadow-mode', 'closed');
                        try { Object.defineProperty(root, '__lootOriginallyClosed', {value: true}); } catch (_) {}
                    }
                    return root;
                }
                Object.defineProperty(attachShadow, '__lootClosedShadowCapture', {value: true});
                try { attachShadow.toString = () => original.toString(); } catch (_) {}
                proto.attachShadow = attachShadow;
            })();
        """)
    if _effective_stealth(state):
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        """)
    if _mode_config(state)["auto_dismiss_dialogs"] and AUTO_DISMISS_DIALOGS:
        await context.add_init_script("""
            window.addEventListener('beforeunload', event => event.stopImmediatePropagation(), true);
        """)
    if _mode_config(state)["disable_notifications"]:
        await context.clear_permissions()
    await _install_adblock(state)
    for pattern, spec in list(state.route_specs.items()):
        await _install_route(state, pattern, spec)


async def _get_browser(engine: str = "", stealth: Optional[bool] = None, mode: str = ""):
    global _playwright, _browser
    state = _state()
    engine = (engine or state.engine).lower()
    mode = (mode or state.mode).lower()
    if mode not in MODE_CONFIG:
        raise ValueError("mode must be headless, headed, or silent")
    stealth = _effective_stealth(state) if stealth is None else bool(stealth or (ANTI_DETECT and MODE_CONFIG[mode]["anti_detect"]))
    if engine not in {"chromium", "firefox", "webkit"}:
        raise ValueError("engine must be chromium, firefox, or webkit")
    key = f"{engine}:{mode}:{int(stealth)}:{state.viewport_width}x{state.viewport_height}"
    if key not in _browser_pool:
        from playwright.async_api import async_playwright
        if _playwright is None:
            _playwright = await async_playwright().start()
        args: List[str] = []
        if engine == "chromium":
            args = ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
            args.extend(extensions.get_manager().get_launch_args())
            args.extend(MODE_CONFIG[mode]["extra_args"])
            args.append(f"--window-size={state.viewport_width},{state.viewport_height}")
            if stealth:
                args.append("--disable-blink-features=AutomationControlled")
        launcher = getattr(_playwright, engine)
        _browser_pool[key] = await launcher.launch(headless=MODE_CONFIG[mode]["headless"], args=args)
    _browser = _browser_pool[key]  # compatibility for older extension helpers
    return _browser_pool[key]


def _realistic_user_agent(browser_obj, engine: str) -> str:
    version = str(getattr(browser_obj, "version", "") or "")
    major = version.split(".")[0] if version else "126"
    if engine == "chromium":
        chrome_version = version if re.fullmatch(r"\d+(?:\.\d+){1,3}", version) else f"{major}.0.0.0"
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36"
        )
    if engine == "firefox":
        return f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{major}.0) Gecko/20100101 Firefox/{major}.0"
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.5 Safari/605.1.15"
    )


async def _get_browser_with_extensions(ext_paths: List[str] = None):
    """Get browser with specific extensions loaded. Triggers restart if needed."""
    global _browser
    if _browser and ext_paths:
        # Check if we need to restart for new extensions
        current_args = extensions.get_manager().get_launch_args()
        new_args = []
        for p in ext_paths:
            new_args.extend([f"--disable-extensions-except={p}", f"--load-extension={p}"])
        if set(new_args) != set(current_args):
            await browser_close(all_clients=True)
            for p in ext_paths:
                extensions.get_manager().register_path(p)
    return await _get_browser()


async def _get_context(session_id: str = ""):
    state = _state()
    session_id = _safe_session_id(session_id or state.current_session)
    if state.context is None or state.current_session != session_id:
        if state.context:
            await _save_session(state.current_session, state)
            await state.context.close()
            state.context = None
            state.page = None
            state.attached_pages.clear()
        if state.metadata_loaded_for != session_id:
            _load_browser_metadata(state, session_id)
        extension_paths = list(extensions.get_manager()._extension_paths)
        context_options = {
            "ignore_https_errors": IGNORE_HTTPS_ERRORS,
            "viewport": {"width": state.viewport_width, "height": state.viewport_height},
        }
        state_file = SESSION_STATE_DIR / f"{session_id}.json"
        if extension_paths and state.engine == "chromium":
            global _playwright
            from playwright.async_api import async_playwright
            if _playwright is None:
                _playwright = await async_playwright().start()
            profile_key = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{get_effective_scope()}-{session_id}")[:160]
            user_data_dir = EXTENSION_PROFILE_DIR / profile_key
            args = ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
            args.extend(extensions.get_manager().get_launch_args())
            args.extend(MODE_CONFIG[state.mode]["extra_args"])
            args.append(f"--window-size={state.viewport_width},{state.viewport_height}")
            if _effective_stealth(state):
                args.append("--disable-blink-features=AutomationControlled")
            state.context = await _playwright.chromium.launch_persistent_context(
                str(user_data_dir), headless=MODE_CONFIG[state.mode]["headless"],
                channel="chromium", args=args, **context_options,
            )
        else:
            browser = await _get_browser(state.engine, state.stealth, state.mode)
            context_options["user_agent"] = _realistic_user_agent(browser, state.engine)
            if state_file.exists():
                state.context = await browser.new_context(storage_state=str(state_file), **context_options)
            else:
                state.context = await browser.new_context(**context_options)
        state.current_session = session_id
        state.created_at = time.time()
        await _attach_context(state.context, state)
    _touch(state)
    return state.context


async def _save_session(session_id: str = "", state: Optional[ClientState] = None):
    """Persist cookies/storage for a session."""
    state = state or _state()
    session_id = _safe_session_id(session_id or state.current_session)
    if state.context:
        state_file = SESSION_STATE_DIR / f"{session_id}.json"
        await state.context.storage_state(path=str(state_file))
    await _save_browser_metadata(state)


async def _get_page(session_id: str = ""):
    state = _state()
    context = await _get_context(session_id)
    if state.page is None or state.page.is_closed():
        restored = []
        pending = list(state.pending_tabs)
        active_index = state.pending_active_tab
        state.pending_tabs = []
        state.pending_active_tab = 0
        for url in pending:
            page = await context.new_page()
            _attach_page(page, state)
            if url and url != "about:blank":
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                except Exception:
                    pass
            restored.append(page)
        if restored:
            state.page = restored[min(active_index, len(restored) - 1)]
        else:
            state.page = await context.new_page()
            _attach_page(state.page, state)
    _touch(state)
    return state.page


async def switch_session(session_id: str) -> Dict[str, Any]:
    """Switch to a different session (loads its cookies/storage).
    The switch is sticky: all subsequent tools operate in this session
    until switch_session is called again."""
    state = _state()
    session_id = _safe_session_id(session_id)
    if state.context:
        await _save_session(state.current_session, state)
    await _get_context(session_id)
    state.page = await _get_page(session_id)
    # Land on a real page so cookies/localStorage work immediately
    # (about:blank has no origin → storage ops are denied).
    try:
        if state.page.url == "about:blank":
            await state.page.goto("https://example.com", wait_until="domcontentloaded", timeout=10000)
    except Exception:
        pass
    return {"switched": True, "session_id": session_id, "client_scope": get_client_scope(), "url": state.page.url}


async def browser_start(profile: str, url: str = "", mode: str = "silent",
                        engine: str = "chromium", stealth: bool = False,
                        auto_block_ads: bool = False) -> Dict[str, Any]:
    """Start one agent task with profile, mode, engine, and URL in one call."""
    profile = _safe_session_id(profile)
    mode = mode.lower().strip()
    engine = engine.lower().strip()
    if mode not in MODE_CONFIG:
        return {"started": False, "error": "mode must be headless, headed, or silent"}
    if engine not in {"chromium", "firefox", "webkit"}:
        return {"started": False, "error": "engine must be chromium, firefox, or webkit"}
    state = _state()
    participants = _shared_participants()
    if get_effective_scope().startswith("shared:") and state.context and len(participants) > 1:
        page = await _get_page()
        navigation = await navigate(url, 30000, state.current_session) if url else None
        return {
            "started": True, "shared_reused": True, "profile": state.current_session,
            "mode": state.mode, "engine": state.engine, "visible": state.mode == "headed",
            "url": page.url, "title": await page.title(), "tabs": len(state.context.pages),
            "participants": participants, "navigation": navigation,
        }
    if state.context:
        await _save_session(state.current_session, state)
        await state.context.close()
        state.context = None
        state.page = None
        state.attached_pages.clear()
        state.adblock_handler = None
    _load_browser_metadata(state, profile)
    state.current_session = profile
    state.metadata_loaded_for = profile
    state.mode = mode
    state.engine = engine
    state.stealth = bool(stealth)
    state.auto_block_ads = bool(auto_block_ads)
    try:
        page = await _get_page(profile)
    except Exception as ex:
        return {"started": False, "failure_class": "browser_unavailable", "error": str(ex)[:500]}
    navigation = None
    if url:
        navigation = await navigate(url, 30000, profile)
        page = await _get_page(profile)
    await _save_session(profile, state)
    return {
        "started": True,
        "profile": profile,
        "mode": state.mode,
        "engine": state.engine,
        "visible": state.mode == "headed",
        "url": page.url,
        "title": await page.title(),
        "tabs": len(state.context.pages),
        "navigation": None if navigation is None else {
            "navigated": navigation.get("navigated"),
            "recovered": navigation.get("recovered"),
            "session_status": (navigation.get("session_health") or {}).get("status"),
            "readiness": (navigation.get("readiness") or {}).get("status"),
            "error": navigation.get("error"),
        },
    }


async def closed_shadow_status() -> Dict[str, Any]:
    """Report early closed-root capture and roots observed on the live page."""
    result = {
        "enabled": CAPTURE_CLOSED_SHADOW,
        "captured_hosts": 0,
        "frames": [],
        "boundary": (
            "Captures page-world roots created after context initialization. "
            "Roots created before instrumentation or in isolated extension worlds remain inaccessible."
        ),
    }
    state = _state()
    if not state.context or not state.page:
        result["active"] = False
        return result
    result["active"] = True
    page = await _get_page()
    for index, frame in enumerate(page.frames):
        try:
            count = await frame.locator("[data-loot-original-shadow-mode='closed']").count()
            if count:
                result["frames"].append({"frame": index, "url": frame.url[:300], "captured_hosts": count})
                result["captured_hosts"] += count
        except Exception as ex:
            result["frames"].append({"frame": index, "url": frame.url[:300], "error": str(ex)[:160]})
    return result


async def save_session(session_id: str = "") -> Dict[str, Any]:
    """Explicitly save current session state."""
    resolved = _safe_session_id(session_id or get_current_session())
    await _save_session(resolved)
    return {"saved": True, "session_id": resolved, "client_scope": get_client_scope()}


async def list_sessions() -> Dict[str, Any]:
    """List all saved sessions."""
    sessions = [f.stem for f in SESSION_STATE_DIR.glob("*.json")]
    return {"sessions": sessions, "current": get_current_session(), "client_scope": get_client_scope()}


async def navigate(url: str, timeout: int = 15000, session_id: str = "") -> Dict[str, Any]:
    """Navigate with one clean-page retry after a poisoned/interrupted load.

    Omitted session_id follows the sticky active profile established by
    switch_session().
    """
    state = _state()
    state.requested_url = url
    page = await _get_page(session_id)
    recovered = False
    response = None
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    except Exception as first_error:
        context = await _get_context(session_id)
        try:
            await page.close()
        except Exception:
            pass
        state.page = await context.new_page()
        _attach_page(state.page, state)
        recovered = True
        try:
            response = await state.page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        except Exception as second_error:
            return {
                "url": state.page.url,
                "title": await state.page.title(),
                "navigated": False,
                "recovered": recovered,
                "failure_class": "navigation",
                "error": str(second_error)[:500],
                "first_error": str(first_error)[:300],
            }
        page = state.page
    readiness = await wait_for_spa_ready(page=page, timeout=min(5000, max(500, timeout // 3)))
    result = {
        "url": page.url,
        "title": await page.title(),
        "navigated": True,
        "recovered": recovered,
        "readiness": readiness,
        "http_status": response.status if response else None,
    }
    result["session_health"] = await session_health(url)
    result["bot_protection"] = await bot_protection_status(page, result["http_status"])
    return result


async def wait_for_spa_ready(timeout: int = 5000, quiet_ms: int = 300, page: Any = None) -> Dict[str, Any]:
    """Wait for a detected SPA root to hydrate and settle; return immediately for static pages."""
    page = page or await _get_page()
    timeout = max(250, min(int(timeout), 30000))
    quiet_ms = max(100, min(int(quiet_ms), 2000))
    started = time.perf_counter()
    initial = await page.evaluate("""() => {
        const root = document.querySelector('#root,#app,#__next,#__nuxt,[data-reactroot]');
        const scripts = [...document.scripts].some(s => /(?:_next|chunk|bundle|vite|webpack|react|vue|angular)/i.test(s.src || ''));
        const text = (document.body?.innerText || '').trim();
        return {root: !!root, scripts, textLength: text.length, readyState: document.readyState,
                iframeCount: document.querySelectorAll('iframe,frame').length};
    }""")
    spa_detected = bool(initial.get("root") and (initial.get("scripts") or initial.get("textLength", 0) < 200))
    if not spa_detected:
        if initial.get("iframeCount", 0):
            await page.wait_for_timeout(max(500, quiet_ms))
        return {
            "status": "static_ready", "spa_detected": False, "hydrated": True,
            "waited_ms": round((time.perf_counter() - started) * 1000),
            "text_length": initial.get("textLength", 0),
            "frames": len(page.frames),
        }
    last_signature = None
    stable_since = time.perf_counter()
    samples = 0
    latest: Dict[str, Any] = {}
    while (time.perf_counter() - started) * 1000 < timeout:
        samples += 1
        latest = await page.evaluate("""() => {
            const body = document.body;
            const text = (body?.innerText || '').trim();
            const controls = body?.querySelectorAll('button,input,select,textarea,a[href],[role=button]').length || 0;
            const busyNodes = [...document.querySelectorAll('[aria-busy=true],[data-loading=true],.loading,.spinner,.skeleton')];
            const visibleBusy = busyNodes.filter(el => { const r=el.getBoundingClientRect(); const s=getComputedStyle(el); return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'; }).length;
            const loadingText = /^(?:loading|please wait|initializing|hydrating)[.!…\\s]*$/i.test(text);
            return {textLength:text.length, controls, visibleBusy, loadingText,
                    htmlLength:document.documentElement.outerHTML.length, readyState:document.readyState};
        }""")
        signature = (latest.get("textLength"), latest.get("controls"), latest.get("visibleBusy"), latest.get("htmlLength"))
        if signature != last_signature:
            last_signature = signature
            stable_since = time.perf_counter()
        hydrated = bool(
            latest.get("readyState") in {"interactive", "complete"}
            and not latest.get("visibleBusy") and not latest.get("loadingText")
            and (latest.get("textLength", 0) >= 20 or latest.get("controls", 0) > 0)
        )
        if hydrated and (time.perf_counter() - stable_since) * 1000 >= quiet_ms:
            return {
                "status": "hydrated", "spa_detected": True, "hydrated": True,
                "waited_ms": round((time.perf_counter() - started) * 1000),
                "samples": samples, "text_length": latest.get("textLength", 0),
                "controls": latest.get("controls", 0),
            }
        await asyncio.sleep(0.1)
    return {
        "status": "hydration_timeout", "spa_detected": True, "hydrated": False,
        "waited_ms": round((time.perf_counter() - started) * 1000), "samples": samples,
        "text_length": latest.get("textLength", 0), "controls": latest.get("controls", 0),
        "visible_busy": latest.get("visibleBusy", 0),
        "recommendation": "Use browser_wait or browser_debug; the SPA root did not reach a stable hydrated state.",
    }


async def bot_protection_status(page: Any = None, http_status: Optional[int] = None) -> Dict[str, Any]:
    """Detect common challenge/block pages and return practical local recovery routes."""
    page = page or await _get_page()
    try:
        snapshot = await page.evaluate("""() => ({
            title: document.title || '',
            text: (document.body?.innerText || '').slice(0, 12000),
            captcha: !!document.querySelector('iframe[src*=captcha],iframe[src*=challenge],.g-recaptcha,[data-sitekey],input[name*=captcha]'),
            challenge: !!document.querySelector('#challenge-running,#cf-challenge-running,[class*=challenge-form],form[action*=challenge]')
        })""")
    except Exception:
        snapshot = {"title": "", "text": "", "captcha": False, "challenge": False}
    combined = f"{snapshot.get('title', '')}\n{snapshot.get('text', '')}".casefold()
    patterns = {
        "cloudflare_challenge": ("just a moment", "checking your browser", "cf-chl-"),
        "human_verification": ("verify you are human", "confirm you are human", "human verification"),
        "captcha": ("complete the captcha", "enter the captcha", "captcha verification", "captcha challenge"),
        "access_denied": ("access denied", "request blocked", "you have been blocked"),
        "rate_limited": ("too many requests", "unusual traffic", "rate limit exceeded"),
    }
    signals = [name for name, needles in patterns.items() if any(needle in combined for needle in needles)]
    if snapshot.get("captcha") and "captcha" not in signals:
        signals.append("captcha")
    if snapshot.get("challenge") and "cloudflare_challenge" not in signals:
        signals.append("cloudflare_challenge")
    if http_status in {401, 403, 429, 503}:
        signals.append(f"http_{http_status}")
    signals = list(dict.fromkeys(signals))
    blocked = bool(signals)
    state = _state()
    recommendations = []
    recovery_routes = []
    if blocked:
        if state.mode != "headed":
            recommendations.append("Switch to headed mode and use wait_for_user for a manual challenge.")
            recovery_routes.append({"strategy": "headed_manual_handoff", "tools": ["set_browser_mode", "wait_for_user", "save_session"]})
        if state.engine == "chromium":
            recommendations.append("Retry in Firefox or WebKit when the profile does not require Chromium-only state.")
            recovery_routes.append({"strategy": "alternate_engine", "tools": ["browser_start"], "engines": ["firefox", "webkit"]})
        if not _effective_stealth(state):
            recommendations.append("Enable Chromium stealth or silent mode before retrying.")
            recovery_routes.append({"strategy": "stealth_retry", "tools": ["browser_start", "set_browser_mode"]})
        recommendations.append("Inspect network_requests and console_logs; do not repeatedly hammer a blocked endpoint.")
        recovery_routes.append({"strategy": "diagnose_without_hammering", "tools": ["network_requests", "console_logs"]})
    return {
        "status": "blocked" if blocked else "clear", "blocked": blocked,
        "signals": signals, "http_status": http_status, "title": snapshot.get("title", "")[:200],
        "recommendations": recommendations,
        "recovery_routes": recovery_routes,
        "boundary": "Advanced bot protection cannot be guaranteed; manual completion may be required." if blocked else "",
    }


async def session_health(requested_url: str = "") -> Dict[str, Any]:
    """Detect likely authentication expiry without claiming auth is proven."""
    state = _state()
    page = await _get_page()
    current_url = page.url or ""
    requested_url = requested_url or state.requested_url
    age_seconds = max(0, round(time.time() - state.created_at))
    url_login = bool(re.search(r"(?:^|[/._?=&-])(login|log-in|signin|sign-in|authenticate|oauth)(?:[/._?=&-]|$)", current_url, re.I))
    try:
        password_visible = await page.locator("input[type='password']:visible").count() > 0
    except Exception:
        password_visible = False
    redirected = bool(requested_url and current_url and requested_url.rstrip("/") != current_url.rstrip("/"))
    login_required = url_login or password_visible
    ttl_exceeded = age_seconds >= SESSION_TTL_SECONDS
    if login_required:
        status = "login_required"
        reason = "login_url" if url_login else "visible_password_field"
        recommendation = "Re-authenticate in this profile, then call save_session."
    elif ttl_exceeded:
        status = "reauth_recommended"
        reason = "session_ttl_exceeded"
        recommendation = "Verify authentication before a sensitive action; re-authenticate if redirected."
    else:
        status = "healthy"
        reason = "no_login_indicators"
        recommendation = ""
    return {
        "status": status,
        "login_required": login_required,
        "reason": reason,
        "recommendation": recommendation,
        "url": current_url,
        "requested_url": requested_url,
        "redirected": redirected,
        "session_id": state.current_session,
        "client_scope": get_client_scope(),
        "age_seconds": age_seconds,
        "ttl_seconds": SESSION_TTL_SECONDS,
    }


async def network_requests(limit: int = 50, since_timestamp: int = 0,
                           url_contains: str = "", method: str = "",
                           clear: bool = False, compact: bool = True,
                           failures_only: bool = False) -> Dict[str, Any]:
    state = _state()
    limit = max(1, min(int(limit), 500))
    method = method.upper().strip()
    events = [
        event for event in state.network_events
        if event.get("timestamp", 0) >= int(since_timestamp or 0)
        and (not url_contains or url_contains.casefold() in event.get("url", "").casefold())
        and (not method or event.get("method") == method)
        and (
            not failures_only
            or event.get("status") is None
            or int(event.get("status", 0)) >= 400
        )
    ]
    selected = events[-limit:]
    if compact:
        selected = [
            {key: event.get(key) for key in ("method", "url", "status", "resourceType", "failure") if event.get(key) is not None}
            for event in selected
        ]
    if clear:
        state.network_events.clear()
    return {
        "requests": selected,
        "matched": len(events),
        "returned": len(selected),
        "truncated": len(events) > len(selected),
        "client_scope": get_client_scope(),
    }


async def console_logs(limit: int = 50, level: str = "", clear: bool = False,
                       compact: bool = True) -> Dict[str, Any]:
    state = _state()
    limit = max(1, min(int(limit), 500))
    level = level.casefold().strip()
    events = [event for event in state.console_events if not level or event.get("type", "").casefold() == level]
    selected = events[-limit:]
    if compact:
        selected = [
            {key: event.get(key) for key in ("type", "text", "url") if event.get(key) not in (None, "")}
            for event in selected
        ]
    if clear:
        state.console_events.clear()
    return {
        "logs": selected,
        "matched": len(events),
        "returned": len(selected),
        "truncated": len(events) > len(selected),
        "client_scope": get_client_scope(),
    }


async def network_mock(pattern: str, body: str = "", status: int = 200,
                       content_type: str = "application/json", method: str = "",
                       headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Mock matching requests in only the current MCP client's context."""
    if not pattern.strip():
        return {"mocked": False, "error": "pattern is required"}
    if status < 100 or status > 599:
        return {"mocked": False, "error": "status must be between 100 and 599"}
    state = _state()
    context = await _get_context()
    if pattern in state.route_handlers:
        await context.unroute(pattern, state.route_handlers[pattern])
    spec = {
        "body": body,
        "status": int(status),
        "content_type": content_type,
        "method": method.upper().strip(),
        "headers": headers or {},
    }
    state.route_specs[pattern] = spec
    await _install_route(state, pattern, spec)
    return {"mocked": True, "pattern": pattern, **spec, "client_scope": get_client_scope()}


async def network_unmock(pattern: str = "") -> Dict[str, Any]:
    state = _state()
    context = await _get_context()
    targets = [pattern] if pattern else list(state.route_handlers)
    removed = []
    for target in targets:
        handler = state.route_handlers.pop(target, None)
        if handler:
            await context.unroute(target, handler)
            removed.append(target)
        state.route_specs.pop(target, None)
    return {"unmocked": removed, "count": len(removed), "client_scope": get_client_scope()}


async def configure_browser(engine: str = "chromium", stealth: bool = False) -> Dict[str, Any]:
    """Select Chromium, Firefox, or WebKit for this client, with optional stealth hints."""
    engine = engine.lower().strip()
    if engine not in {"chromium", "firefox", "webkit"}:
        return {"configured": False, "error": "engine must be chromium, firefox, or webkit"}
    state = _state()
    unchanged = state.engine == engine and state.stealth == bool(stealth)
    if unchanged:
        try:
            await _get_browser(engine, stealth)
        except Exception as ex:
            return {
                "configured": False,
                "engine": engine,
                "stealth": bool(stealth),
                "failure_class": "browser_unavailable",
                "error": str(ex)[:500],
                "hint": f"Install it with: python -m playwright install {engine}",
            }
        return {
            "configured": True,
            "unchanged": True,
            "engine": engine,
            "stealth": bool(stealth),
            "client_scope": get_client_scope(),
            "note": "Existing context and tabs were preserved.",
        }
    old_engine, old_stealth = state.engine, state.stealth
    state.engine, state.stealth = engine, bool(stealth)
    try:
        await _get_browser(engine, stealth)
    except Exception as ex:
        state.engine, state.stealth = old_engine, old_stealth
        return {
            "configured": False,
            "engine": engine,
            "stealth": bool(stealth),
            "failure_class": "browser_unavailable",
            "error": str(ex)[:500],
            "hint": f"Install it with: python -m playwright install {engine}",
        }
    if state.context:
        await _save_session(state.current_session, state)
        await state.context.close()
        state.context = None
        state.page = None
        state.attached_pages.clear()
    return {
        "configured": True,
        "engine": engine,
        "stealth": bool(stealth),
        "client_scope": get_client_scope(),
        "note": "Stealth reduces basic webdriver signals but does not guarantee bot-protection bypass.",
    }


async def set_browser_mode(mode: str, auto_block_ads: Optional[bool] = None) -> Dict[str, Any]:
    """Switch only the current MCP client between headless, headed, and silent."""
    mode = mode.lower().strip()
    if mode not in MODE_CONFIG:
        return {"changed": False, "error": "mode must be headless, headed, or silent"}
    state = _state()
    requested_adblock = state.auto_block_ads if auto_block_ads is None else bool(auto_block_ads)
    unchanged = state.mode == mode and state.auto_block_ads == requested_adblock
    if unchanged:
        return {**await get_browser_mode(), "changed": False, "unchanged": True}

    old_mode, old_adblock = state.mode, state.auto_block_ads
    state.mode, state.auto_block_ads = mode, requested_adblock
    try:
        await _get_browser(state.engine, state.stealth, mode)
    except Exception as ex:
        state.mode, state.auto_block_ads = old_mode, old_adblock
        return {
            "changed": False,
            "failure_class": "browser_unavailable",
            "mode": mode,
            "error": str(ex)[:500],
        }

    if state.context:
        await _save_session(state.current_session, state)
        await state.context.close()
        state.context = None
        state.page = None
        state.attached_pages.clear()
        state.adblock_handler = None
    state.metadata_loaded_for = state.current_session
    await _save_browser_metadata(state)
    page = await _get_page()
    return {
        **await get_browser_mode(),
        "changed": True,
        "url": page.url,
        "tabs_restored": len((await _get_context()).pages),
    }


async def get_browser_mode() -> Dict[str, Any]:
    state = _state()
    config = _mode_config(state)
    return {
        "mode": state.mode,
        "visible": state.mode == "headed",
        "engine": state.engine,
        "anti_detection": _effective_stealth(state),
        "auto_dismiss_dialogs": bool(config["auto_dismiss_dialogs"] and AUTO_DISMISS_DIALOGS),
        "mute_audio": config["mute_audio"],
        "disable_notifications": config["disable_notifications"],
        "auto_block_ads": state.auto_block_ads,
        "blocked_ads": state.blocked_ads,
        "dialogs_dismissed": state.dialogs_dismissed,
        "viewport": {"width": state.viewport_width, "height": state.viewport_height},
        "session_id": state.current_session,
        "client_scope": get_client_scope(),
    }


async def is_browser_visible() -> Dict[str, Any]:
    state = _state()
    page_open = bool(state.page and not state.page.is_closed())
    return {
        "visible": state.mode == "headed" and page_open,
        "mode": state.mode,
        "page_open": page_open,
        "url": state.page.url if page_open else "",
        "client_scope": get_client_scope(),
    }


async def set_viewport(width: int, height: int) -> Dict[str, Any]:
    state = _state()
    width = max(320, min(int(width), 7680))
    height = max(240, min(int(height), 4320))
    state.viewport_width, state.viewport_height = width, height
    page = await _get_page()
    await page.set_viewport_size({"width": width, "height": height})
    outer_window = False
    if state.mode == "headed" and state.engine == "chromium":
        try:
            cdp = await state.context.new_cdp_session(page)
            target = await cdp.send("Browser.getWindowForTarget")
            await cdp.send("Browser.setWindowBounds", {
                "windowId": target["windowId"],
                "bounds": {"width": width, "height": height, "windowState": "normal"},
            })
            await cdp.detach()
            outer_window = True
        except Exception:
            pass
    await _save_browser_metadata(state)
    return {
        "resized": True,
        "width": width,
        "height": height,
        "outer_window": outer_window,
        "mode": state.mode,
    }


_BANNER_JS = """({id, message, waiting, token}) => {
    document.getElementById(id)?.remove();
    const root = document.createElement('div');
    root.id = id;
    root.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#ffeb3b;color:#111;padding:12px 16px;font:600 14px/1.4 Arial,sans-serif;box-shadow:0 2px 8px rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;gap:16px';
    const text = document.createElement('span');
    text.textContent = message;
    root.appendChild(text);
    const button = document.createElement('button');
    button.textContent = waiting ? 'Done — return to agent' : 'Dismiss';
    button.style.cssText = 'border:1px solid #222;border-radius:5px;background:#fff;padding:6px 10px;cursor:pointer;font-weight:700';
    button.onclick = async () => {
        if (waiting && typeof window.__lootUserDone === 'function') {
            await window.__lootUserDone(token);
        }
        root.remove();
    };
    root.appendChild(button);
    (document.body || document.documentElement).appendChild(root);
    return true;
}"""


async def signal_user(message: str) -> Dict[str, Any]:
    state = _state()
    if state.mode != "headed":
        return {"shown": False, "blocked": True, "reason": "browser_not_visible", "mode": state.mode}
    page = await _get_page()
    await page.evaluate(_BANNER_JS, {"id": "__loot_agent_signal", "message": f"Agent: {message}"[:500], "waiting": False, "token": ""})
    try:
        await page.bring_to_front()
    except Exception:
        pass
    return {"shown": True, "message": message[:500], "mode": state.mode, "url": page.url}


async def wait_for_user(action: str = "complete the task", timeout: int = 120) -> Dict[str, Any]:
    state = _state()
    if state.mode != "headed":
        return {
            "completed": False,
            "blocked": True,
            "reason": "browser_not_visible",
            "message": "Switch to headed mode before requesting user handoff.",
        }
    timeout = max(1, min(int(timeout), 600))
    page = await _get_page()
    banner_id = "__loot_agent_wait"
    handoff_token = secrets.token_urlsafe(18)
    state.handoff_token = handoff_token
    state.handoff_event = asyncio.Event()
    banner_payload = {"id": banner_id, "message": f"Agent waiting: {action}"[:500], "waiting": True, "token": handoff_token}
    await page.evaluate(
        _BANNER_JS,
        banner_payload,
    )
    try:
        await page.bring_to_front()
    except Exception:
        pass
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        if page.is_closed():
            return {"completed": False, "closed": True, "waited_s": round(time.perf_counter() - started, 1)}
        if state.handoff_event.is_set():
            state.handoff_event = None
            state.handoff_token = ""
            return {
                "completed": True,
                "action": action,
                "waited_s": round(time.perf_counter() - started, 1),
                "url": page.url,
            }
        try:
            gone = await page.evaluate("id => !document.getElementById(id)", banner_id)
            if gone:
                await page.evaluate(_BANNER_JS, banner_payload)
        except Exception:
            pass
        await asyncio.sleep(0.5)
    try:
        await page.evaluate("id => document.getElementById(id)?.remove()", banner_id)
    except Exception:
        pass
    state.handoff_event = None
    state.handoff_token = ""
    return {"completed": False, "timeout": True, "action": action, "waited_s": timeout, "url": page.url}


async def client_isolation_status() -> Dict[str, Any]:
    state = _state()
    return {
        "client_scope": get_client_scope(),
        "effective_scope": get_effective_scope(),
        "shared": get_effective_scope().startswith("shared:"),
        "participants": _shared_participants() if get_effective_scope().startswith("shared:") else [get_client_scope()],
        "active_clients": len(_client_states),
        "session_id": state.current_session,
        "engine": state.engine,
        "stealth": state.stealth,
        "mode": state.mode,
        "context_id": id(state.context) if state.context else None,
        "page_id": id(state.page) if state.page else None,
        "url": state.page.url if state.page and not state.page.is_closed() else "",
    }


async def browser_state() -> Dict[str, Any]:
    """Compact current-task state for agents; avoids several status calls."""
    state = _state()
    page = await _get_page()
    health = await session_health()
    pages = state.context.pages if state.context else []
    active_tab = pages.index(page) if page in pages else 0
    return {
        "profile": state.current_session,
        "mode": state.mode,
        "engine": state.engine,
        "visible": state.mode == "headed",
        "url": page.url,
        "title": await page.title(),
        "tabs": len(pages),
        "active_tab": active_tab,
        "session_status": health["status"],
        "network_events": len(state.network_events),
        "console_events": len(state.console_events),
        "blocked_ads": state.blocked_ads,
        "dialogs_dismissed": state.dialogs_dismissed,
    }


async def get_text() -> Dict[str, str]:
    page = await _get_page()
    text = await page.evaluate("() => document.body.innerText")
    return {"text": text or ""}


async def get_html() -> Dict[str, str]:
    page = await _get_page()
    html = await page.content()
    return {"html": html or ""}


async def get_title() -> Dict[str, str]:
    page = await _get_page()
    return {"title": await page.title()}


async def get_url() -> Dict[str, str]:
    page = await _get_page()
    return {"url": page.url}


async def _click_selector(page, selector: str, timeout: int = 10000) -> bool:
    """Click with selector fallbacks. Tries the raw selector first, then:
    - if it looks like an href/URL, exact attribute match, then ends-with match,
      then netloc (host) contains match — handles protocol-relative hrefs
      (//docs.python.org/ vs https://docs.python.org/)
    - text match (a:has-text)
    Returns True if a click landed."""
    import re
    candidates = [selector]
    s = selector.strip()
    netloc = ""
    m = re.match(r"https?://([^/]+)", s)
    if m:
        netloc = m.group(1)
    # href-like selectors: normalize the common get_links→click gap
    # (resolved URLs vs raw href attributes, incl. protocol-relative)
    if s.startswith("http://") or s.startswith("https://") or s.startswith("//") or s.startswith("/"):
        esc = s.replace('"', '\\"')
        candidates += [
            f"a[href='{esc}']",
            f"a[href=\"{esc}\"]",
            f"a[href$='{esc}']",
            f"a[href*='{esc}']",
        ]
        if netloc:
            # protocol-relative hrefs: //docs.python.org/ won't contain the full https URL
            candidates += [f"a[href*='//{netloc}']", f"a[href*='{netloc}']"]
    elif s.startswith("a[href"):
        url = s.split("'")[1] if "'" in s else (s.split('"')[1] if '"' in s else "")
        if url:
            esc = url.replace('"', '\\"')
            candidates += [f"a[href$='{esc}']", f"a[href*='{esc}']"]
            um = re.match(r"https?://([^/]+)", url)
            if um:
                candidates += [f"a[href*='//{um.group(1)}']", f"a[href*='{um.group(1)}']"]
    for cand in candidates:
        try:
            await page.click(cand, timeout=timeout)
            return True
        except Exception:
            continue
    # last resort: text match on the visible label (or hostname)
    for label in (s, netloc):
        if label:
            try:
                await page.click(f"a:has-text('{label}')", timeout=timeout)
                return True
            except Exception:
                continue
    return False


async def click(selector: str = "", x: float = 0, y: float = 0) -> Dict[str, Any]:
    page = await _get_page()
    if selector:
        ok = await _click_selector(page, selector)
        if not ok:
            return {"clicked": False, "error": f"no element matched '{selector}' (tried selector + href/text fallbacks)"}
        return {"clicked": True, "selector": selector}
    else:
        await page.mouse.click(x, y)
        return {"clicked": True, "x": x, "y": y}


async def type_text(selector: str, text: str) -> Dict[str, Any]:
    page = await _get_page()
    await page.fill(selector, text)
    return {"typed": True, "selector": selector}


async def scroll(x: int = 0, y: int = 0) -> Dict[str, Any]:
    page = await _get_page()
    await page.evaluate(f"window.scrollTo({x}, {y})")
    return {"scrolled": True, "x": x, "y": y}


async def evaluate(code: str) -> Dict[str, Any]:
    page = await _get_page()
    result = await page.evaluate(code)
    return {"result": result}


async def screenshot(full_page: bool = False, save_path: str = "",
                     max_dimension: int = 1600, format: str = "jpeg",
                     quality: int = 72) -> Dict[str, Any]:
    """Capture screenshot. Returns downscaled JPEG base64 by default.

    - save_path: write the FULL-resolution PNG to disk and return {path} only
      (no base64). Use this when a persistent artifact is wanted.
    - max_dimension/format/quality: when returning base64, downscale to keep
      the payload small (default 1600px JPEG q72 — typically 50-150 KB instead
      of a 1 MB PNG blob). Pass max_dimension=0 to disable downscaling.
    """
    page = await _get_page()
    png = await page.screenshot(full_page=full_page)
    if save_path:
        path = os.path.abspath(save_path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(png)
        return {"path": path, "size_bytes": len(png), "full_page": full_page, "saved": True}
    if max_dimension and max_dimension > 0:
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(png))
            if img.width > max_dimension or img.height > max_dimension:
                img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
            fmt = format.upper() if format.upper() in ("JPEG", "PNG", "WEBP") else "JPEG"
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format=fmt, quality=quality)
            b64 = base64.b64encode(buf.getvalue()).decode()
            return {"base64": b64, "size_bytes": len(buf.getvalue()),
                    "full_page": full_page, "downscaled": True,
                    "max_dimension": max_dimension, "format": fmt.lower()}
        except Exception:
            pass  # fall through to raw PNG if PIL is unavailable
    b64 = base64.b64encode(png).decode()
    return {"base64": b64, "size_bytes": len(png), "full_page": full_page}


async def back() -> Dict[str, Any]:
    page = await _get_page()
    await page.go_back()
    return {"url": page.url}


async def forward() -> Dict[str, Any]:
    page = await _get_page()
    await page.go_forward()
    return {"url": page.url}


async def reload() -> Dict[str, Any]:
    page = await _get_page()
    await page.reload()
    return {"url": page.url}


async def get_links(limit: int = 100) -> Dict[str, Any]:
    """Get links on current page. Returns both the raw href attribute and
    the resolved absolute URL (they differ on relative/protocol-relative
    links — use `href` in click selectors, `url` for the destination).
    limit caps the response to avoid context bloat."""
    page = await _get_page()
    links = await page.evaluate(
        """() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
            href: a.getAttribute('href'),
            url: a.href,
            text: a.innerText.trim()
        }))"""
    )
    links = links or []
    total = len(links)
    return {"links": links[:limit], "count": len(links[:limit]), "total": total, "truncated": total > limit}


async def get_attributes(selector: str, attributes: List[str]) -> Dict[str, Any]:
    page = await _get_page()
    result = await page.evaluate(
        """([sel, attrs]) => {
            const el = document.querySelector(sel);
            if (!el) return null;
            const out = {};
            attrs.forEach(a => { out[a] = el.getAttribute(a) || el[a] || ''; });
            return out;
        }""",
        [selector, attributes],
    )
    return {"attributes": result or {}}


async def wait_for(selector: str, timeout: int = 10000) -> Dict[str, Any]:
    page = await _get_page()
    await page.wait_for_selector(selector, timeout=timeout)
    return {"found": True, "selector": selector}


async def list_tabs() -> Dict[str, Any]:
    context = await _get_context()
    pages = context.pages
    return {"tabs": [{"id": i, "url": p.url, "title": await p.title()} for i, p in enumerate(pages)]}


async def new_tab(url: str = "") -> Dict[str, Any]:
    state = _state()
    context = await _get_context()
    page = await context.new_page()
    state.page = page
    _attach_page(page, state)
    if url:
        await page.goto(url)
    return {"tab_id": context.pages.index(page), "url": page.url}


async def switch_tab(tab_id: int) -> Dict[str, Any]:
    state = _state()
    context = await _get_context()
    pages = context.pages
    if tab_id < len(pages):
        state.page = pages[tab_id]
        _attach_page(state.page, state)
        return {"switched": True, "url": state.page.url}
    return {"switched": False, "error": "Tab not found"}


async def close_tab(tab_id: int) -> Dict[str, Any]:
    state = _state()
    context = await _get_context()
    pages = context.pages
    if tab_id < len(pages) and len(pages) > 1:
        closing_current = pages[tab_id] is state.page
        await pages[tab_id].close()
        if closing_current:
            state.page = context.pages[max(0, min(tab_id - 1, len(context.pages) - 1))]
        return {"closed": True}
    return {"closed": False, "error": "Cannot close last tab or tab not found"}


async def submit(selector: str = "") -> Dict[str, Any]:
    page = await _get_page()
    if selector:
        await page.evaluate(f"document.querySelector('{selector}').closest('form')?.submit()")
    else:
        await page.evaluate("document.activeElement?.closest('form')?.submit()")
    return {"submitted": True}


async def press_key(key: str, selector: str = "") -> Dict[str, Any]:
    page = await _get_page()
    if selector:
        await page.focus(selector)
    await page.keyboard.press(key)
    return {"pressed": key}


async def upload_file(selector: str, file_paths: List[str]) -> Dict[str, Any]:
    page = await _get_page()
    try:
        await page.set_input_files(selector, file_paths)
        return {"uploaded": True, "files": file_paths, "via": "selector"}
    except Exception:
        pass
    # Fallback: pierce shadow DOM. Compose widgets (e.g. mail.com's webmailer)
    # hide the real file input inside closed shadow roots that page-level
    # locators cannot reach — and inside CROSS-ORIGIN iframes, so the walk must
    # run per-frame (top-page JS cannot read a cross-origin iframe's DOM).
    pierce_js = """() => {
        function findInput(root) {
            const kids = root.querySelectorAll ? root.querySelectorAll('*') : [];
            for (const el of kids) {
                if (el.shadowRoot) { const f = findInput(el.shadowRoot); if (f) return f; }
                if (el.tagName === 'INPUT' && el.type === 'file') return el;
            }
            return null;
        }
        return findInput(document);
    }"""
    try:
        handle = await page.evaluate_handle(pierce_js)
        el = handle.as_element()
        if el is not None:
            await el.set_input_files(file_paths)
            return {"uploaded": True, "files": file_paths, "via": "shadow-pierce"}
    except Exception:
        pass
    # Frame-level pierce: app shells (webmailers, editors) host the widget in a
    # nested iframe. Run the same walk inside candidate frames.
    for f in page.frames:
        if f == page.main_frame:
            continue
        url = (f.url or "")[:200]
        if not any(k in url for k in ("webmailer", "mail.", "compose", "editor", "mailer")):
            continue
        try:
            h2 = await f.evaluate_handle(pierce_js)
            e2 = h2.as_element()
            if e2 is not None:
                await e2.set_input_files(file_paths)
                return {"uploaded": True, "files": file_paths, "via": "frame-shadow-pierce", "frame": url}
        except Exception:
            continue
    return {
        "uploaded": False,
        "failure_class": "element_not_found",
        "error": f"no file input found via selector '{selector}', shadow pierce, or frame pierce",
    }


async def fullscreen(selector: str = "") -> Dict[str, Any]:
    page = await _get_page()
    if selector:
        await page.evaluate(f"document.querySelector('{selector}')?.requestFullscreen()")
    else:
        await page.keyboard.press("f")
    return {"fullscreen": True}


async def download_file(url: str, output_path: str) -> Dict[str, Any]:
    """Download a file from URL to local path."""
    page = await _get_page()
    async with page.expect_download() as download_info:
        await page.evaluate(f"(() => {{ const a = document.createElement('a'); a.href = '{url}'; a.click(); }})()")
    download = await download_info.value
    await download.save_as(output_path)
    return {"downloaded": True, "path": output_path, "suggested_filename": download.suggested_filename}


async def save_element_image(selector: str, output_path: str) -> Dict[str, Any]:
    """Save an image element to a local file."""
    page = await _get_page()
    element = await page.query_selector(selector)
    if element is None:
        return {"saved": False, "error": "Element not found"}
    await element.screenshot(path=output_path)
    return {"saved": True, "path": output_path}


async def fill_form(fields: Dict[str, str]) -> Dict[str, Any]:
    """Fill multiple form fields at once. fields = {selector: value}."""
    page = await _get_page()
    filled = []
    for selector, value in fields.items():
        await page.fill(selector, value)
        filled.append(selector)
    return {"filled": True, "fields": filled}


async def handle_dialog(accept: bool = True, prompt_text: str = "") -> Dict[str, Any]:
    """Handle JavaScript dialogs (alert, confirm, prompt)."""
    if _mode_config()["auto_dismiss_dialogs"] and AUTO_DISMISS_DIALOGS:
        return {
            "handler_set": False,
            "blocked": True,
            "reason": "silent_auto_dismiss_active",
            "note": "Silent mode dismisses dialogs automatically.",
        }
    page = await _get_page()
    dialog_result = {}

    async def _handler(dialog):
        dialog_result["type"] = dialog.type
        dialog_result["message"] = dialog.message
        if accept:
            await dialog.accept(prompt_text)
        else:
            await dialog.dismiss()

    page.on("dialog", _handler)
    return {"handler_set": True, "note": "Dialog handler is set for next dialog"}


async def get_cookies(domain: str = "") -> Dict[str, Any]:
    """Get cookies for current context, optionally filtered by domain."""
    context = await _get_context()
    cookies = await context.cookies()
    if domain:
        cookies = [c for c in cookies if domain in c.get("domain", "")]
    return {"cookies": cookies, "count": len(cookies)}


async def set_cookie(name: str, value: str, domain: str = "", path: str = "/") -> Dict[str, Any]:
    """Set a cookie. If domain is empty, derives it from the current page
    (works after navigating to the target). On about:blank / non-http(s)
    pages, requires an explicit domain."""
    context = await _get_context()
    page = await _get_page()
    if domain:
        cookie = {"name": name, "value": value, "domain": domain, "path": path}
    else:
        url = page.url
        if not (url.startswith("http://") or url.startswith("https://")):
            return {
                "set": False,
                "error": "no page origin — navigate to the target site first, or pass domain=<host>",
            }
        # Playwright: a cookie takes either `url` OR (`domain` + `path`), never both
        cookie = {"name": name, "value": value, "url": url}
    await context.add_cookies([cookie])
    return {"set": True, "name": name, "domain": domain or page.url}


def _has_origin(page) -> bool:
    """localStorage needs an http(s) origin (about:blank / data: are denied)."""
    url = page.url
    return url.startswith("http://") or url.startswith("https://")


async def get_local_storage(key: str = "") -> Dict[str, Any]:
    """Get localStorage items."""
    page = await _get_page()
    if not _has_origin(page):
        return {"error": "no page origin — navigate to a page first (localStorage needs http(s))"}
    if key:
        value = await page.evaluate(f"localStorage.getItem('{key}')")
        return {"key": key, "value": value}
    items = await page.evaluate("() => Object.fromEntries(Object.entries(localStorage))")
    return {"items": items or {}}


async def set_local_storage(key: str, value: str) -> Dict[str, Any]:
    """Set a localStorage item."""
    page = await _get_page()
    if not _has_origin(page):
        return {"set": False, "error": "no page origin — navigate to a page first (localStorage needs http(s))"}
    await page.evaluate("([key, value]) => localStorage.setItem(key, value)", [key, value])
    return {"set": True, "key": key}


async def right_click(selector: str) -> Dict[str, Any]:
    """Right-click an element."""
    page = await _get_page()
    await page.click(selector, button="right")
    return {"right_clicked": True, "selector": selector}


async def drag_and_drop(source_selector: str, target_selector: str) -> Dict[str, Any]:
    """Drag from source to target."""
    page = await _get_page()
    await page.drag_and_drop(source_selector, target_selector)
    return {"dropped": True, "source": source_selector, "target": target_selector}


async def select_option(selector: str, value: str = "", label: str = "", index: int = -1) -> Dict[str, Any]:
    """Select a dropdown option by value, label, or index."""
    page = await _get_page()
    if value:
        await page.select_option(selector, value=value)
        return {"selected": True, "by": "value", "value": value}
    elif label:
        await page.select_option(selector, label=label)
        return {"selected": True, "by": "label", "label": label}
    elif index >= 0:
        await page.select_option(selector, index=index)
        return {"selected": True, "by": "index", "index": index}
    return {"selected": False, "error": "Provide value, label, or index"}


async def hover(selector: str) -> Dict[str, Any]:
    """Hover over an element."""
    page = await _get_page()
    await page.hover(selector)
    return {"hovered": True, "selector": selector}


async def focus(selector: str) -> Dict[str, Any]:
    """Focus an element."""
    page = await _get_page()
    await page.focus(selector)
    return {"focused": True, "selector": selector}


async def get_selected_text() -> Dict[str, Any]:
    """Get currently selected/highlighted text."""
    page = await _get_page()
    text = await page.evaluate("() => window.getSelection().toString()")
    return {"text": text}


async def clear_input(selector: str) -> Dict[str, Any]:
    """Clear an input field."""
    page = await _get_page()
    await page.fill(selector, "")
    return {"cleared": True, "selector": selector}


async def get_page_source() -> Dict[str, str]:
    """Get full page source with doctype."""
    page = await _get_page()
    html = await page.content()
    return {"html": html, "length": len(html)}


async def visual_action(target: str, action: str = "click", text: str = "") -> Dict[str, Any]:
    """Ground a target in the viewport screenshot and act at its center.

    This is a recovery path for canvas/custom-rendered controls. DOM/AX actions
    remain preferred because they provide stronger identity and verification.
    """
    if not VISUAL_RECOVERY:
        return {"success": False, "failure_class": "visual_mismatch", "error": "visual recovery is disabled"}
    page = await _get_page()
    try:
        import vision

        png = await page.screenshot(full_page=False)
        image = base64.b64encode(png).decode("ascii")
        grounded = await asyncio.to_thread(vision.find_on_screen, image, target)
        boxes = grounded.get("boxes", []) if isinstance(grounded, dict) else []
        if not boxes:
            return {
                "success": False,
                "failure_class": "visual_mismatch",
                "error": grounded.get("error", "visual target was not grounded") if isinstance(grounded, dict) else "visual target was not grounded",
                "model": grounded.get("model") if isinstance(grounded, dict) else None,
            }
        raw_box = boxes[0].get("bbox", boxes[0]) if isinstance(boxes[0], dict) else boxes[0]
        if not isinstance(raw_box, (list, tuple)) or len(raw_box) != 4:
            return {"success": False, "failure_class": "visual_mismatch", "error": "grounding returned an invalid box"}
        viewport = page.viewport_size or {"width": _state().viewport_width, "height": _state().viewport_height}
        x1, y1, x2, y2 = [float(value) for value in raw_box]
        normalized = max(x1, y1, x2, y2) <= 1000.0
        if normalized:
            x1, x2 = x1 * viewport["width"] / 1000.0, x2 * viewport["width"] / 1000.0
            y1, y2 = y1 * viewport["height"] / 1000.0, y2 * viewport["height"] / 1000.0
        x = max(0.0, min((x1 + x2) / 2.0, float(viewport["width"] - 1)))
        y = max(0.0, min((y1 + y2) / 2.0, float(viewport["height"] - 1)))
        hit = await page.evaluate("""({x, y}) => {
            const el = document.elementFromPoint(x, y);
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {tag: el.tagName.toLowerCase(), role: el.getAttribute('role') || '',
                    text: (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 200),
                    rect: {x: r.x, y: r.y, width: r.width, height: r.height}};
        }""", {"x": x, "y": y})
        await page.mouse.click(x, y)
        if action == "fill":
            await page.keyboard.press("Control+A")
            await page.keyboard.insert_text(text)
        return {
            "success": True,
            "clicked": True,
            "filled": action == "fill",
            "verified": action != "fill" or bool(text),
            "via": "visual_grounding",
            "strategy": "florence-viewport-center",
            "target": target,
            "point": {"x": round(x, 1), "y": round(y, 1)},
            "bbox": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
            "normalized_input": normalized,
            "hit": hit,
            "model": grounded.get("model"),
            "time_s": grounded.get("time_s"),
        }
    except Exception as ex:
        return {"success": False, "failure_class": "visual_mismatch", "error": str(ex)[:500]}


async def wait_for_url(url_pattern: str, timeout: int = 10000) -> Dict[str, Any]:
    """Wait for URL to match pattern (useful after redirects)."""
    page = await _get_page()
    await page.wait_for_url(url_pattern, timeout=timeout)
    return {"url": page.url, "matched": True}


async def get_element_count(selector: str) -> Dict[str, Any]:
    """Count elements matching a selector."""
    page = await _get_page()
    count = await page.evaluate(f"document.querySelectorAll('{selector}').length")
    return {"selector": selector, "count": count}


async def browser_close(all_clients: bool = False, close_shared: bool = False):
    global _playwright, _browser
    if not all_clients:
        client = get_client_scope()
        effective = get_effective_scope()
        if effective.startswith("shared:") and not close_shared:
            _shared_bindings.pop(client, None)
            return {
                "closed": False, "detached": True, "scope": client,
                "room": effective.removeprefix("shared:"), "all_clients": False,
                "note": "Detached from shared room; shared browser remains open.",
            }
        state = _state()
        if state.context:
            await _save_session(state.current_session, state)
            await state.context.close()
        _client_states.pop(effective, None)
        if effective.startswith("shared:"):
            for participant in _shared_participants(effective):
                _shared_bindings.pop(participant, None)
        return {"closed": True, "scope": effective, "all_clients": False}

    for state in list(_client_states.values()):
        if state.context:
            try:
                await _save_session(state.current_session, state)
                await state.context.close()
            except Exception:
                pass
    _client_states.clear()
    _shared_bindings.clear()
    for browser_obj in list(_browser_pool.values()):
        try:
            await browser_obj.close()
        except Exception:
            pass
    _browser_pool.clear()
    _browser = None
    if _playwright:
        await _playwright.stop()
        _playwright = None
    return {"closed": True, "scope": "all", "all_clients": True}
