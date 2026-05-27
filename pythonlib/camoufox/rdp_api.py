"""
RDPBrowser: Camoufox automation via Firefox RDP + WebExtension.
Zero-detection-surface alternative to Playwright/Juggler.

Usage:
    from camoufox.rdp_api import RDPBrowser

    async with RDPBrowser() as browser:
        page = await browser.new_page()
        await page.goto("https://example.com")
        html = await page.content()
        await page.click("#button")
        await page.fill("#input", "text")
        await page.mouse.wheel(0, 500)
        await page.screenshot("shot.png")
"""

import asyncio
import base64
import ctypes
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


# Windows Job Object for killing process trees
_kernel32 = ctypes.windll.kernel32 if os.name == "nt" else None


def _create_job_object():
    """Create a Windows Job Object that kills all children when closed."""
    if not _kernel32:
        return None
    import ctypes.wintypes as wt

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wt.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wt.DWORD),
            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
            ("PriorityClass", wt.DWORD),
            ("SchedulingClass", wt.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
    if not _kernel32.SetInformationJobObject(
        job, 9, ctypes.byref(info), ctypes.sizeof(info)
    ):
        _kernel32.CloseHandle(job)
        return None
    return job


from geckordp.actors.addon.addons import AddonsActor
from geckordp.actors.descriptors.tab import TabActor
from geckordp.actors.events import Events
from geckordp.actors.memory import MemoryActor
from geckordp.actors.resources import Resources
from geckordp.actors.root import RootActor
from geckordp.actors.screenshot import ScreenshotActor
from geckordp.actors.string import StringActor
from geckordp.actors.targets.window_global import WindowGlobalActor
from geckordp.actors.watcher import WatcherActor
from geckordp.actors.web_console import WebConsoleActor
from geckordp.rdp_client import RDPClient

logger = logging.getLogger(__name__)
logging.getLogger("geckordp").setLevel(logging.CRITICAL)

EXTENSION_DIR = str(Path(__file__).parent / "extension")
DEFAULT_RDP_PORT = 6000
DEFAULT_WS_PORT = 8775


def _get_default_binary() -> str:
    try:
        from .pkgman import launch_path

        return str(launch_path())
    except Exception:
        return ""


def _write_user_prefs(profile_dir: str, prefs: Dict[str, Any]) -> None:
    user_js = os.path.join(profile_dir, "user.js")
    with open(user_js, "a", encoding="utf-8") as f:
        for key, value in prefs.items():
            if isinstance(value, bool):
                val_str = "true" if value else "false"
            elif isinstance(value, str):
                val_str = f'"{value}"'
            else:
                val_str = str(value)
            f.write(f'user_pref("{key}", {val_str});\n')


def _check_port(host: str, port: int) -> bool:
    """Synchronous TCP port check (Windows-compatible)."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1.0)
    try:
        sock.connect((host, port))
        sock.close()
        return True
    except (ConnectionRefusedError, OSError, socket.timeout):
        return False


async def _wait_for_port(host: str, port: int, timeout: float = 60.0) -> None:
    """Wait for a TCP port to accept connections. Uses sync socket in thread for Windows compatibility."""
    deadline = time.time() + timeout
    delay = 0.2
    while time.time() < deadline:
        is_open = await asyncio.to_thread(_check_port, host, port)
        if is_open:
            return
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 2.0)
    raise TimeoutError(f"Port {port} not ready within {timeout}s")


class _ExtensionBridge:
    def __init__(self, port: int):
        self._port = port
        self._server = None
        self._ws = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._connected = asyncio.Event()

    async def start(self):
        try:
            import websockets

            self._server = await websockets.serve(
                self._handler, "127.0.0.1", self._port
            )
            logger.info(f"Extension bridge listening on ws://127.0.0.1:{self._port}")
        except ImportError:
            logger.warning("websockets not installed, extension input unavailable")

    async def _handler(self, ws):
        self._ws = ws
        self._connected.set()
        logger.info("Extension connected")
        try:
            async for raw in ws:
                data = json.loads(raw)
                if data.get("type") == "hello":
                    logger.info(f"Extension hello: {data.get('extensionId')}")
                    continue
                msg_id = data.get("id")
                if msg_id and msg_id in self._pending:
                    self._pending[msg_id].set_result(data)
        except Exception:
            pass
        finally:
            self._ws = None
            self._connected.clear()

    async def send_command(self, cmd: str, params: dict, timeout: float = 10.0) -> Any:
        if not self._ws:
            if not self._connected.is_set():
                try:
                    await asyncio.wait_for(self._connected.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    raise ConnectionError("Extension not connected")

        msg_id = str(uuid.uuid4())[:8]
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = fut

        await self._ws.send(json.dumps({"id": msg_id, "cmd": cmd, "params": params}))

        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(msg_id, None)

        if result.get("error"):
            raise RuntimeError(f"Extension error: {result['error']}")
        return result.get("result")

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    @property
    def is_connected(self) -> bool:
        return self._ws is not None


class RDPPage:
    """Page handle with Playwright-like API over Firefox RDP."""

    def __init__(
        self,
        client: RDPClient,
        tab_actor_id: str,
        target_actor_id: str,
        console_actor_id: str,
        browsing_context_id: Optional[int] = None,
        bridge: Optional[_ExtensionBridge] = None,
        tab_id: Optional[int] = None,
    ):
        self._client = client
        self._tab_actor_id = tab_actor_id
        self._target_actor_id = target_actor_id
        self._console_actor_id = console_actor_id
        self._browsing_context_id = browsing_context_id
        self._bridge = bridge
        self._tab_id = tab_id
        self._url = ""
        self._console_started = False
        self._target_ver = 0
        self._watcher_id = None
        self._observing = False
        self._screenshot_task = None
        self.mouse = _Mouse(self)
        self.keyboard = _Keyboard(self)

    async def _idle_mouse_loop(self):
        """Humanized micro-drift while waiting on navigation.

        Uses humanize.idle_mouse_drift (sigma-lognormal sub-movements +
        2-5Hz tremor) instead of teleporting between random points. This
        matches what real readers do while a page loads and keeps the SFU
        ring buffer filled with plausible deltas.

        Generates drift in 3-6s chunks so asyncio cancellation is honored
        within at most one sub-step (~100-300ms).
        """
        import random as _r
        from .humanize import idle_mouse_drift

        try:
            while True:
                cx, cy = self.mouse._x, self.mouse._y
                chunk = _r.uniform(3.0, 6.0)
                path = idle_mouse_drift(
                    cx, cy, chunk, viewport_w=1400, viewport_h=800
                )
                for x, y, delay in path:
                    try:
                        await self.mouse._raw_move(x, y)
                        self.mouse._x = x
                        self.mouse._y = y
                    except Exception:
                        pass
                    await asyncio.sleep(delay)
        except asyncio.CancelledError:
            pass

    async def simulate_tab_switch(self, duration: Optional[float] = None) -> None:
        """Simulate the user switching to another tab/window.

        Minimizes the browser window, waits `duration` seconds (5-25 default),
        then restores it. Produces native window.blur, document.visibilitychange
        (hidden), rAF throttling, and on restore: focus + visibilitychange (visible).
        Anti-bot systems (PerimeterX, Shopee SFU, Akamai) track these as a
        strong signal of human behavior. Real users switch tabs 3-8x per session.

        Call only from idle periods -- do not call during navigation or
        extraction, since the tab gets throttled while minimized.
        """
        import random as _r

        if not self._bridge or not self._bridge.is_connected:
            return

        if duration is None:
            duration = _r.uniform(5.0, 25.0)

        try:
            await self._bridge.send_command("minimizeWindow", {}, timeout=5)
        except Exception:
            return

        await asyncio.sleep(duration)

        try:
            await self._bridge.send_command("restoreWindow", {}, timeout=5)
        except Exception:
            pass

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _with_idle_mouse(self):
        """Run idle mouse movements in background during a wait."""
        task = asyncio.create_task(self._idle_mouse_loop())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _start_persistent_watcher(self):
        """Set up a tab-level watcher that auto-updates actors on any
        navigation (goto, click, JS redirect, etc.)."""
        tab = TabActor(self._client, self._tab_actor_id)
        watcher_ctx = tab.get_watcher()
        self._watcher_id = watcher_ctx["actor"]
        watcher = WatcherActor(self._client, self._watcher_id)
        watcher.watch_targets(WatcherActor.Targets.FRAME)

        def _on_target(data):
            t = data.get("target", {})
            if t.get("isTopLevelTarget"):
                new_actor = t.get("actor", "")
                new_console = t.get("consoleActor", "")
                if new_console and new_console != self._console_actor_id:
                    self._console_actor_id = new_console
                    self._console_started = False
                if new_actor:
                    self._target_actor_id = new_actor
                bc = t.get("browsingContextID")
                if bc is not None:
                    self._browsing_context_id = bc
                new_url = t.get("url", "")
                if new_url and new_url.startswith("http"):
                    self._url = new_url
                self._target_ver += 1
                logger.debug(
                    f"Persistent watcher: target updated v{self._target_ver} -> {new_console}"
                )

        self._client.add_event_listener(
            self._watcher_id, Events.Watcher.TARGET_AVAILABLE_FORM, _on_target
        )
        self._persistent_target_cb = _on_target

    def _refresh_target(self):
        tab = TabActor(self._client, self._tab_actor_id)
        target = tab.get_target()
        if target and isinstance(target, dict):
            new_console = target.get("consoleActor", "")
            if new_console and new_console != self._console_actor_id:
                self._console_actor_id = new_console
                self._console_started = False
            self._target_actor_id = target.get("actor", self._target_actor_id)
            self._browsing_context_id = target.get(
                "browsingContextID", self._browsing_context_id
            )

    def _rebind_to_active_sync(self) -> bool:
        """Re-bind RDP actors a la tab actualmente SELECCIONADA en el browser.

        Multi-tab: tras activar otra tab (incluida una que abrio el sitio con
        window.open), esto reapunta console/target/browsingContext a esa tab para
        que evaluate()/content() lean la tab correcta. La parte de input (bridge)
        usa _tab_id, refrescado aparte via getActiveTab.
        """
        root = RootActor(self._client)
        desc = None
        try:
            desc = root.current_tab()
        except Exception:
            desc = None
        if not (isinstance(desc, dict) and desc.get("actor")):
            tabs = root.list_tabs()
            desc = tabs[0] if tabs else None
        if not (isinstance(desc, dict) and desc.get("actor")):
            return False
        self._tab_actor_id = desc.get("actor", self._tab_actor_id)
        tab = TabActor(self._client, self._tab_actor_id)
        target = tab.get_target()
        if isinstance(target, dict):
            self._target_actor_id = target.get("actor", self._target_actor_id)
            self._console_actor_id = target.get("consoleActor", self._console_actor_id)
            self._browsing_context_id = target.get(
                "browsingContextID", self._browsing_context_id
            )
            self._console_started = False
        return True

    async def rebind_to_active(self) -> bool:
        """Async: re-bind a la tab activa + refrescar _tab_id (bridge) + watcher."""
        ok = await asyncio.to_thread(self._rebind_to_active_sync)
        if self._bridge and self._bridge.is_connected:
            try:
                r = await self._bridge.send_command("getActiveTab", {}, timeout=3)
                if r:
                    self._tab_id = r.get("tabId")
                    if r.get("url"):
                        self._url = r["url"]
            except Exception:
                pass
        try:
            await asyncio.to_thread(self._start_persistent_watcher)
        except Exception:
            pass
        return ok

    def _ensure_console(self):
        if not self._console_started:
            console = WebConsoleActor(self._client, self._console_actor_id)
            console.start_listeners([])
            self._console_started = True

    def _eval_sync(self, expression: str, timeout: float = 10.0) -> Any:
        self._ensure_console()

        fut = Future()

        def on_result(data):
            try:
                fut.set_result(data)
            except Exception:
                pass

        console_id = self._console_actor_id
        self._client.add_event_listener(
            console_id, Events.WebConsole.EVALUATION_RESULT, on_result
        )

        try:
            console = WebConsoleActor(self._client, console_id)
            response = console.evaluate_js_async(expression)

            # geckordp returns error dict (not None) on stale actors
            _is_error = response is None or (
                isinstance(response, dict) and "error" in response
            )
            if _is_error:
                self._client.remove_event_listener(
                    console_id, Events.WebConsole.EVALUATION_RESULT, on_result
                )
                self._console_started = False
                self._refresh_target()
                self._ensure_console()
                console_id = self._console_actor_id
                self._client.add_event_listener(
                    console_id, Events.WebConsole.EVALUATION_RESULT, on_result
                )
                console = WebConsoleActor(self._client, console_id)
                response = console.evaluate_js_async(expression)
                if response is None or (
                    isinstance(response, dict) and "error" in response
                ):
                    return None

            data = fut.result(timeout=timeout)
        except Exception:
            return None
        finally:
            self._client.remove_event_listener(
                console_id, Events.WebConsole.EVALUATION_RESULT, on_result
            )

        val = data.get("result")
        if isinstance(val, dict):
            if val.get("type") == "longString":
                actor_id = val.get("actor", "")
                length = val.get("length", 0)
                sa = StringActor(self._client, actor_id)
                full = sa.substring(0, length)
                if isinstance(full, str):
                    return full
                if isinstance(full, dict):
                    return full.get("substring", val.get("initial", ""))
                return val.get("initial", "")
            if val.get("type") == "undefined":
                return None
        return val

    @property
    def url(self) -> str:
        # Live evaluate for backward compat (CAPTCHA loops rely on fresh value).
        # Events also update _url during goto/reload for faster access.
        try:
            result = self._eval_sync("window.location.href")
            if isinstance(result, str):
                self._url = result
        except Exception:
            pass
        return self._url

    @property
    def url_cached(self) -> str:
        """Return cached URL (updated by goto/reload events). Zero round-trips."""
        return self._url

    async def url_fresh(self) -> str:
        """Async explicit evaluate for exact URL."""
        try:
            result = await self.evaluate("window.location.href")
            if isinstance(result, str):
                self._url = result
        except Exception:
            pass
        return self._url

    async def goto(
        self, url: str, wait_until: str = "load", timeout: int = 30000
    ) -> None:
        loop = asyncio.get_running_loop()
        load_done = asyncio.Event()
        deadline = time.time() + (timeout / 1000)
        console_listeners: list = []

        goal = (
            "dom-complete"
            if wait_until in ("load", "networkidle")
            else "dom-interactive"
        )

        def _on_doc_event(data):
            logger.debug(f"goto DOCUMENT_EVENT: {data}")
            name = data.get("name", "")
            if name == goal or name == "dom-complete":
                evt_url = data.get("url", "")
                if evt_url:
                    self._url = evt_url
                loop.call_soon_threadsafe(load_done.set)

        def _attach_console_listener(console_id):
            WebConsoleActor(self._client, console_id).start_listeners(
                [WebConsoleActor.Listeners.DOCUMENT_EVENTS]
            )
            self._client.add_event_listener(
                console_id, Events.WebConsole.DOCUMENT_EVENT, _on_doc_event
            )
            console_listeners.append(console_id)

        # Listen on current console for same-origin nav events
        await asyncio.to_thread(
            lambda: _attach_console_listener(self._console_actor_id)
        )
        self._console_started = True

        try:
            # Snapshot target version before navigating
            ver_before = self._target_ver

            # Navigate via BrowsingContext.fixupAndLoadURIString with user activation.
            # Sends sec-fetch-user:?1 without any DOM manipulation.
            # Falls back to TabDescriptor for about:blank or no bridge.
            navigated = False
            if self._bridge and self._bridge.is_connected and self._tab_id is not None:
                try:
                    await self._bridge.send_command(
                        "navigate", {"tabId": self._tab_id, "url": url}
                    )
                    navigated = True
                except Exception:
                    pass

            if not navigated:
                await asyncio.to_thread(
                    lambda: self._client.send_receive(
                        {
                            "to": self._tab_actor_id,
                            "type": "navigateTo",
                            "url": url,
                            "waitForLoad": False,
                        }
                    )
                )
            self._url = url
            self._console_started = False

            last_target_ver = ver_before
            nav_started = False

            async with self._with_idle_mouse():
                while time.time() < deadline:
                    if load_done.is_set():
                        return

                    # Persistent watcher updated the target (cross-process nav)
                    if self._target_ver > last_target_ver:
                        last_target_ver = self._target_ver
                        nav_started = True
                        await asyncio.to_thread(
                            lambda: _attach_console_listener(self._console_actor_id)
                        )
                        self._console_started = True

                        if load_done.is_set():
                            return
                        try:
                            state = await self.evaluate("document.readyState")
                            if state == "complete" or (
                                goal == "dom-interactive"
                                and state in ("interactive", "complete")
                            ):
                                return
                        except Exception:
                            pass
                        continue

                    remaining = max(0.1, deadline - time.time())
                    try:
                        await asyncio.wait_for(
                            load_done.wait(), timeout=min(1.0, remaining)
                        )
                        return
                    except asyncio.TimeoutError:
                        try:
                            state = await self.evaluate("document.readyState")
                            # Only trust "complete" if we saw nav start first
                            if state in ("loading", "interactive"):
                                nav_started = True
                            if nav_started and (
                                state == "complete"
                                or (
                                    goal == "dom-interactive"
                                    and state in ("interactive", "complete")
                                )
                            ):
                                return
                        except Exception:
                            # evaluate failed = actor stale = nav in progress
                            nav_started = True
        finally:
            for cid in console_listeners:
                try:
                    self._client.remove_event_listener(
                        cid, Events.WebConsole.DOCUMENT_EVENT, _on_doc_event
                    )
                except Exception:
                    pass

        # Post-navigation: reposition cursor and drift naturally
        try:
            import random as _r

            await self.mouse._raw_move(self.mouse._x, self.mouse._y)
            drift_x = self.mouse._x + _r.uniform(-80, 80)
            drift_y = self.mouse._y + _r.uniform(-60, 60)
            drift_x = max(50, min(drift_x, 1800))
            drift_y = max(50, min(drift_y, 900))
            await self.mouse.move_smooth(drift_x, drift_y)
        except Exception:
            pass

    async def _wait_for_doc_event(
        self, goal: str = "dom-complete", timeout_s: float = 30.0
    ) -> None:
        """Wait for a WebConsoleActor DOCUMENT_EVENT matching *goal*.
        Used by reload() and wait_for_load_state() where no cross-process
        nav is expected so we only listen on the current console."""
        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        console_id = self._console_actor_id

        def _on_evt(data):
            name = data.get("name", "")
            if name == goal or name == "dom-complete":
                evt_url = data.get("url", "")
                if evt_url:
                    self._url = evt_url
                loop.call_soon_threadsafe(done.set)

        await asyncio.to_thread(
            lambda: WebConsoleActor(self._client, console_id).start_listeners(
                [WebConsoleActor.Listeners.DOCUMENT_EVENTS]
            )
        )
        self._console_started = True
        self._client.add_event_listener(
            console_id, Events.WebConsole.DOCUMENT_EVENT, _on_evt
        )
        try:
            async with self._with_idle_mouse():
                await asyncio.wait_for(done.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            pass
        finally:
            try:
                self._client.remove_event_listener(
                    console_id, Events.WebConsole.DOCUMENT_EVENT, _on_evt
                )
            except Exception:
                pass

    async def reload(self, timeout: int = 30000) -> None:
        loop = asyncio.get_running_loop()
        goal = "dom-complete"
        timeout_s = timeout / 1000

        # Start doc event listener BEFORE triggering reload
        done = asyncio.Event()
        console_id = self._console_actor_id

        def _on_evt(data):
            name = data.get("name", "")
            if name == goal or name == "dom-complete":
                evt_url = data.get("url", "")
                if evt_url:
                    self._url = evt_url
                loop.call_soon_threadsafe(done.set)

        await asyncio.to_thread(
            lambda: WebConsoleActor(self._client, console_id).start_listeners(
                [WebConsoleActor.Listeners.DOCUMENT_EVENTS]
            )
        )
        self._console_started = True
        self._client.add_event_listener(
            console_id, Events.WebConsole.DOCUMENT_EVENT, _on_evt
        )

        try:
            await asyncio.to_thread(
                lambda: WindowGlobalActor(self._client, self._target_actor_id).reload()
            )
            self._console_started = False

            async with self._with_idle_mouse():
                await asyncio.wait_for(done.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            pass
        finally:
            try:
                self._client.remove_event_listener(
                    console_id, Events.WebConsole.DOCUMENT_EVENT, _on_evt
                )
            except Exception:
                pass

    async def content(self) -> str:
        return await self.evaluate("document.documentElement.outerHTML") or ""

    async def evaluate(self, expression: str) -> Any:
        expr = expression.strip()
        auto_called = False
        # Playwright compat: auto-call arrow/function expressions
        if (
            expr.startswith("() =>")
            or expr.startswith("async () =>")
            or expr.startswith("function")
        ) and not expr.endswith("()"):
            expr = f"({expr})()"
            auto_called = True

        # For auto-called functions, wrap to serialize object/array results
        # (geckordp returns RDP grips for non-primitives, not actual values)
        if auto_called:
            expr = (
                f"(function(){{var v=({expr});"
                f"return typeof v==='object'&&v!==null?JSON.stringify(v):v}})()"
            )

        # Mask "debugger eval code" in Error().stack traces
        if "//# sourceURL=" not in expr:
            expr += "\n//# sourceURL=resource://gre/modules/AppConstants.sys.mjs"

        result = await asyncio.to_thread(self._eval_sync, expr)

        # Parse stringified objects/arrays back to Python
        if auto_called and isinstance(result, str):
            try:
                return json.loads(result)
            except (json.JSONDecodeError, ValueError):
                pass
        return result

    async def query_selector(self, selector: str) -> Optional[Dict]:
        result = await self.evaluate(
            f"(function(){{ var el = document.querySelector('{selector}');"
            f"if(!el) return null;"
            f"var r = el.getBoundingClientRect();"
            f"return JSON.stringify({{x:r.x,y:r.y,w:r.width,h:r.height}}); }})()"
        )
        if result and isinstance(result, str):
            return json.loads(result)
        return None

    async def click(self, selector: str) -> None:
        rect = await self.query_selector(selector)
        if not rect:
            raise ValueError(f"Element not found: {selector}")
        x = rect["x"] + rect["w"] / 2
        y = rect["y"] + rect["h"] / 2
        await self.mouse.click_smooth(x, y, target_width=rect.get("w", 50))

    async def fill(self, selector: str, text: str) -> None:
        await self.click(selector)
        await asyncio.sleep(0.1)
        # Clear existing value via select-all + delete
        if self._bridge and self._bridge.is_connected and self._tab_id is not None:
            await self._bridge.send_command(
                "keyPress", {"tabId": self._tab_id, "key": "a", "modifiers": 4}
            )
            await asyncio.sleep(0.05)
            await self._bridge.send_command(
                "keyPress", {"tabId": self._tab_id, "key": "Backspace"}
            )
            await asyncio.sleep(0.05)
            await self._bridge.send_command(
                "type", {"tabId": self._tab_id, "text": text}
            )
        else:
            raise ConnectionError(
                "Extension bridge not connected, cannot fill with trusted events"
            )

    async def screenshot(self, path: Optional[str] = None) -> bytes:
        if self._bridge and self._bridge.is_connected:
            result = await self._bridge.send_command("screenshot", {})
            if result and result.get("dataUrl"):
                b64 = (
                    result["dataUrl"].split(",", 1)[1]
                    if "," in result["dataUrl"]
                    else result["dataUrl"]
                )
                data = base64.b64decode(b64)
                if path:
                    with open(path, "wb") as f:
                        f.write(data)
                return data

        def _capture():
            root = RootActor(self._client)
            root_data = root.get_root()
            sa_id = root_data.get("screenshotActor", "")
            if not sa_id:
                return b""
            sa = ScreenshotActor(self._client, sa_id)
            result = sa.capture(self._browsing_context_id or 0)
            b64_data = (
                result.get("value", {}).get("data", "")
                if isinstance(result.get("value"), dict)
                else result.get("value", "")
            )
            if isinstance(b64_data, str) and b64_data:
                b64_data = b64_data.replace("data:image/png;base64,", "")
                return base64.b64decode(b64_data)
            return b""

        data = await asyncio.to_thread(_capture)
        if path and data:
            with open(path, "wb") as f:
                f.write(data)
        return data

    def on(self, event: str, callback) -> None:
        """Register event listener (stub for Playwright compatibility).
        Network events like 'requestfinished' are not available via RDP."""
        if not hasattr(self, "_event_listeners"):
            self._event_listeners = {}
        self._event_listeners.setdefault(event, []).append(callback)
        logger.debug(f"Event listener registered (stub): {event}")

    def remove_listener(self, event: str, callback) -> None:
        """Remove event listener (stub for Playwright compatibility)."""
        if hasattr(self, "_event_listeners") and event in self._event_listeners:
            try:
                self._event_listeners[event].remove(callback)
            except ValueError:
                pass

    # --- Network capture via extension filterResponseData ---

    async def start_capture(self, patterns: list) -> None:
        """Start capturing HTTP responses whose URL contains any of the patterns.
        Captured via extension filterResponseData (invisible to page JS)."""
        if not self._bridge or not self._bridge.is_connected:
            raise ConnectionError("Extension bridge not connected")
        await self._bridge.send_command("startCapture", {"patterns": patterns})
        self._capture_ts = int(time.time() * 1000)
        logger.info(f"Network capture started for patterns: {patterns}")

    async def stop_capture(self) -> None:
        """Stop capturing network responses."""
        if self._bridge and self._bridge.is_connected:
            await self._bridge.send_command("stopCapture", {})

    async def get_captured_responses(self, clear: bool = True) -> list:
        """Get captured network responses. Returns list of {url, body, timestamp}."""
        if not self._bridge or not self._bridge.is_connected:
            return []
        since = getattr(self, "_capture_ts", 0)
        result = await self._bridge.send_command(
            "getCapturedResponses", {"since": since}
        )
        responses = result.get("responses", []) if result else []
        if clear and responses:
            await self._bridge.send_command("clearCaptures", {})
        return responses

    async def wait_for_response(
        self, url_pattern: str, timeout: float = 30.0
    ) -> Optional[dict]:
        """Wait until a captured response matching url_pattern appears.
        Returns the response dict {url, body, timestamp} or None on timeout."""
        deadline = time.time() + timeout
        since = getattr(self, "_capture_ts", 0)
        while time.time() < deadline:
            if self._bridge and self._bridge.is_connected:
                result = await self._bridge.send_command(
                    "getCapturedResponses", {"since": since}
                )
                responses = result.get("responses", []) if result else []
                for r in responses:
                    if url_pattern in r.get("url", ""):
                        await self._bridge.send_command("clearCaptures", {})
                        return r
            await asyncio.sleep(0.5)
        return None

    async def start_spy(self, patterns: list) -> None:
        """Start spying on outgoing requests matching URL patterns.
        Captures request headers, body, and response body."""
        if not self._bridge or not self._bridge.is_connected:
            raise ConnectionError("Extension bridge not connected")
        await self._bridge.send_command("startSpy", {"patterns": patterns})
        self._spy_ts = int(time.time() * 1000)
        logger.info(f"Request spy started for patterns: {patterns}")

    async def stop_spy(self) -> None:
        """Stop spying on requests."""
        if self._bridge and self._bridge.is_connected:
            await self._bridge.send_command("stopSpy", {})

    async def get_spied_requests(self, clear: bool = False) -> list:
        """Get spied requests. Returns list of {url, method, headers, body, responseBody, timestamp}."""
        if not self._bridge or not self._bridge.is_connected:
            return []
        since = getattr(self, "_spy_ts", 0)
        result = await self._bridge.send_command("getSpiedRequests", {"since": since})
        requests = result.get("requests", []) if result else []
        if clear and requests:
            await self._bridge.send_command("clearSpied", {})
        return requests

    async def wait_for_load_state(
        self, state: str = "load", timeout: int = 30000
    ) -> None:
        # Quick check: already at target state?
        target = "complete" if state in ("load", "networkidle") else "interactive"
        try:
            current = await self.evaluate("document.readyState")
            if current == target or current == "complete":
                return
        except Exception:
            pass
        goal = "dom-complete" if state in ("load", "networkidle") else "dom-interactive"
        await self._wait_for_doc_event(goal=goal, timeout_s=timeout / 1000)

    def _get_memory_actor_id(self) -> str:
        tab = TabActor(self._client, self._tab_actor_id)
        target = tab.get_target()
        return target.get("memoryActor", "")

    async def clear_cookies(self, domain: Optional[str] = None) -> int:
        """Clear cookies via the WebExtension bridge.
        Returns the number of cookies removed.
        If domain is given, only cookies for that domain are removed.
        """
        if not self._bridge or not self._bridge.is_connected:
            logger.warning("clear_cookies: bridge not connected")
            return 0
        params = {}
        if domain:
            params["domain"] = domain
        try:
            result = await self._bridge.send_command("clearCookies", params, timeout=10)
            removed = result.get("removed", 0) if result else 0
            logger.info(
                f"Cleared {removed} cookies" + (f" for {domain}" if domain else "")
            )
            return removed
        except Exception as e:
            logger.error(f"clear_cookies failed: {e}")
            return 0

    async def start_observing(self, screenshot_interval: int = 0) -> None:
        """Activate content script observer. Injects observer.js into the active tab
        and starts buffering user interaction events (clicks, scroll, input, etc.).
        If screenshot_interval > 0, captures screenshots every N seconds."""
        if not self._bridge or not self._bridge.is_connected:
            raise ConnectionError("Extension bridge not connected")
        params = {}
        if self._tab_id is not None:
            params["tabId"] = self._tab_id
        await self._bridge.send_command("startObserving", params)
        self._observing = True
        self._screenshot_task = None
        if screenshot_interval > 0:
            self._screenshot_task = asyncio.create_task(
                self._screenshot_loop(screenshot_interval)
            )
        logger.info("Observation started")

    async def stop_observing(self) -> dict:
        """Stop observation and return the full event log."""
        if self._screenshot_task:
            self._screenshot_task.cancel()
            try:
                await self._screenshot_task
            except asyncio.CancelledError:
                pass
            self._screenshot_task = None
        self._observing = False
        if not self._bridge or not self._bridge.is_connected:
            return {"events": []}
        result = await self._bridge.send_command("stopObserving", {})
        events = await self.get_observations(clear=True)
        logger.info(f"Observation stopped, {len(events)} events collected")
        return {"events": events, "count": result.get("count", 0) if result else 0}

    async def get_observations(self, since: int = 0, clear: bool = False) -> list:
        """Get buffered observation events, sorted chronologically by `t`.
        Events come from two sources (content script + background) so insertion
        order is not always chronological. Sorting here gives the consumer a
        single, ordered timeline."""
        if not self._bridge or not self._bridge.is_connected:
            return []
        result = await self._bridge.send_command(
            "getObservations", {"since": since, "clear": clear}
        )
        events = result.get("events", []) if result else []
        events.sort(key=lambda e: e.get("t", 0))
        return events

    async def get_accessibility_tree(self) -> list:
        """Get a simplified DOM snapshot: tag, id, class, role, aria-label, text
        for semantically relevant elements."""
        if not self._bridge or not self._bridge.is_connected:
            return []
        params = {}
        if self._tab_id is not None:
            params["tabId"] = self._tab_id
        result = await self._bridge.send_command("getAccessibilityTree", params)
        return result.get("tree", []) if result else []

    async def _screenshot_loop(self, interval: int) -> None:
        """Internal: periodically capture screenshots while observing."""
        while self._observing:
            try:
                await asyncio.sleep(interval)
                data = await self.screenshot()
                if data and self._bridge and self._bridge.is_connected:
                    # Add screenshot event to observation buffer via direct inject
                    import base64 as _b64

                    b64 = _b64.b64encode(data).decode("ascii")
                    # Truncate to first 200 chars for the event log (full data in file)
                    await self._bridge.send_command(
                        "getObservations", {"since": 0, "clear": False}
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Screenshot loop error: {e}")

    async def force_gc(self) -> None:
        """Force garbage + cycle collection on the current tab."""

        def _gc():
            actor_id = self._get_memory_actor_id()
            if not actor_id:
                return
            mem = MemoryActor(self._client, actor_id)
            mem.attach()
            mem.force_garbage_collection()
            mem.force_cycle_collection()
            mem.detach()

        await asyncio.to_thread(_gc)

    async def memory_usage(self) -> Optional[Dict]:
        """Return memory measurement for the current tab."""

        def _measure():
            actor_id = self._get_memory_actor_id()
            if not actor_id:
                return None
            mem = MemoryActor(self._client, actor_id)
            mem.attach()
            result = mem.measure()
            mem.detach()
            return result

        return await asyncio.to_thread(_measure)

    async def wait_for_network_idle(
        self, idle_ms: int = 500, timeout: int = 30000
    ) -> None:
        """Wait until no network requests are pending for *idle_ms* ms.
        Uses WatcherActor NETWORK_EVENT resource tracking."""
        loop = asyncio.get_running_loop()
        idle_event = asyncio.Event()
        pending: set = set()
        timer_handle: list = [None]

        def _reschedule():
            if timer_handle[0]:
                timer_handle[0].cancel()
                timer_handle[0] = None
            if not pending:
                timer_handle[0] = loop.call_later(idle_ms / 1000, idle_event.set)

        def _on_available(data):
            actors = [
                item.get("actor", "")
                for item in data.get("array", [])
                if isinstance(item, dict)
                and item.get("resourceType") == "network-event"
                and item.get("actor")
            ]
            if actors:

                def _add():
                    pending.update(actors)
                    _reschedule()

                loop.call_soon_threadsafe(_add)

        def _on_updated(data):
            actors = [
                item.get("actor", "")
                for item in data.get("array", [])
                if isinstance(item, dict)
                and item.get("resourceType") == "network-event"
                and item.get("actor")
            ]
            if actors:

                def _remove():
                    for a in actors:
                        pending.discard(a)
                    _reschedule()

                loop.call_soon_threadsafe(_remove)

        tab = TabActor(self._client, self._tab_actor_id)
        watcher_ctx = tab.get_watcher()
        watcher_id = watcher_ctx["actor"]

        def _setup_watcher():
            w = WatcherActor(self._client, watcher_id)
            w.watch_targets(WatcherActor.Targets.FRAME)
            w.watch_resources([Resources.NETWORK_EVENT])

        await asyncio.to_thread(_setup_watcher)

        self._client.add_event_listener(
            watcher_id, Events.Watcher.RESOURCES_AVAILABLE_ARRAY, _on_available
        )
        self._client.add_event_listener(
            watcher_id, Events.Watcher.RESOURCES_UPDATED_ARRAY, _on_updated
        )

        # If network is already idle, start timer immediately
        _reschedule()

        try:
            async with self._with_idle_mouse():
                await asyncio.wait_for(idle_event.wait(), timeout=timeout / 1000)
        except asyncio.TimeoutError:
            pass
        finally:
            if timer_handle[0]:
                timer_handle[0].cancel()
            try:
                self._client.remove_event_listener(
                    watcher_id,
                    Events.Watcher.RESOURCES_AVAILABLE_ARRAY,
                    _on_available,
                )
            except Exception:
                pass
            try:
                self._client.remove_event_listener(
                    watcher_id,
                    Events.Watcher.RESOURCES_UPDATED_ARRAY,
                    _on_updated,
                )
            except Exception:
                pass

    async def wait_for_selector(
        self, selector: str, timeout: int = 30000, state: str = "visible"
    ) -> Optional[Dict]:
        """Wait for an element matching selector to appear/hide.
        Uses MutationObserver + lightweight global-variable poll.
        state: 'visible', 'attached', or 'hidden'.
        Returns element rect or None on timeout."""
        sel_escaped = selector.replace("'", "\\'")

        # Use a namespaced store to avoid detectable global variable patterns
        wfs_key = f"_s{int(time.time() * 1000) % 100000}"

        if state == "hidden":
            setup_js = (
                f"(function(){{"
                f"  if(!window._ws)window._ws={{}};"
                f"  if (!document.querySelector('{sel_escaped}')) {{ window._ws['{wfs_key}']='ok'; return '{wfs_key}'; }}"
                f"  var obs = new MutationObserver(function(){{"
                f"    if (!document.querySelector('{sel_escaped}')) {{ obs.disconnect(); window._ws['{wfs_key}']='ok'; }}"
                f"  }});"
                f"  obs.observe(document.body||document.documentElement,"
                f"    {{childList:true,subtree:true,attributes:true}});"
                f"  setTimeout(function(){{ obs.disconnect(); if(!window._ws['{wfs_key}']) window._ws['{wfs_key}']='timeout'; }},{timeout});"
                f"  return '{wfs_key}';"
                f"}})()"
            )
        else:
            vis_check = (
                "if(r.width===0&&r.height===0) return null;"
                if state == "visible"
                else ""
            )
            setup_js = (
                f"(function(){{"
                f"  if(!window._ws)window._ws={{}};"
                f"  function chk(){{"
                f"    var el=document.querySelector('{sel_escaped}');"
                f"    if(!el) return null;"
                f"    var r=el.getBoundingClientRect(); {vis_check}"
                f"    return JSON.stringify({{x:r.x,y:r.y,w:r.width,h:r.height}});"
                f"  }}"
                f"  var hit=chk(); if(hit){{ window._ws['{wfs_key}']=hit; return '{wfs_key}'; }}"
                f"  var obs=new MutationObserver(function(){{"
                f"    var hit=chk(); if(hit){{ obs.disconnect(); window._ws['{wfs_key}']=hit; }}"
                f"  }});"
                f"  obs.observe(document.body||document.documentElement,"
                f"    {{childList:true,subtree:true,attributes:true}});"
                f"  setTimeout(function(){{ obs.disconnect(); if(!window._ws['{wfs_key}']) window._ws['{wfs_key}']='timeout'; }},{timeout});"
                f"  return '{wfs_key}';"
                f"}})()"
            )

        try:
            await self.evaluate(setup_js)
        except Exception:
            return None

        deadline = time.time() + (timeout / 1000)
        while time.time() < deadline:
            try:
                val = await self.evaluate(f"(window._ws||{{}})['{wfs_key}']")
                if val and val != "null":
                    await self.evaluate(
                        f"try{{delete window._ws['{wfs_key}']}}catch(e){{}}"
                    )
                    if val == "timeout":
                        return None
                    if val == "ok":
                        return {}
                    if isinstance(val, str):
                        return json.loads(val)
                    return val
            except Exception:
                pass
            await asyncio.sleep(0.1)
        # Cleanup on timeout
        try:
            await self.evaluate(f"try{{delete window._ws['{wfs_key}']}}catch(e){{}}")
        except Exception:
            pass
        return None

    def locator(self, selector: str) -> "_Locator":
        """Create a Playwright-compatible locator."""
        return _Locator(self, selector)

    async def query_selector_all(self, selector: str) -> List[Dict]:
        """Return list of element rects matching selector."""
        sel_escaped = selector.replace("'", "\\'")
        result = await self.evaluate(
            f"(function(){{ var els = document.querySelectorAll('{sel_escaped}');"
            f"var out = [];"
            f"for(var i=0; i<els.length; i++) {{"
            f"  var r = els[i].getBoundingClientRect();"
            f"  out.push({{x:r.x,y:r.y,w:r.width,h:r.height,i:i}});"
            f"}}"
            f"return JSON.stringify(out); }})()"
        )
        if result and isinstance(result, str):
            return json.loads(result)
        return []


class _Locator:
    """Playwright-compatible locator for RDPPage."""

    def __init__(self, page: "RDPPage", selector: str):
        self._page = page
        self._selector = selector

    def _to_css_and_js(self) -> str:
        """Convert Playwright-style selector to JS find expression."""
        sel = self._selector
        # Handle text= and text=/regex/ selectors
        if sel.startswith("text="):
            text = sel[5:]
            if text.startswith("/") and "/" in text[1:]:
                # Regex: text=/pattern/flags
                return (
                    f"(function(){{ var re = new RegExp({text}); "
                    f"var tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);"
                    f"while(tw.nextNode()) {{ if(re.test(tw.currentNode.textContent)) "
                    f"return tw.currentNode.parentElement; }} return null; }})()"
                )
            else:
                text_escaped = text.replace("\\", "\\\\").replace("'", "\\'")
                return (
                    f"(function(){{ var tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);"
                    f"while(tw.nextNode()) {{ if(tw.currentNode.textContent.includes('{text_escaped}')) "
                    f"return tw.currentNode.parentElement; }} return null; }})()"
                )
        # Handle css= prefix
        if sel.startswith("css:") or sel.startswith("css="):
            sel = sel[4:]
        return f"document.querySelector('{sel.replace(chr(39), chr(92) + chr(39))}')"

    async def wait_for(self, state: str = "visible", timeout: int = 5000) -> None:
        find_js = self._to_css_and_js()
        wfs_key = f"_l{int(time.time() * 1000) % 100000}"

        if state == "hidden":
            setup_js = (
                f"(function(){{"
                f"  if(!window._ws)window._ws={{}};"
                f"  if (({find_js}) === null) {{ window._ws['{wfs_key}']='ok'; return; }}"
                f"  var obs = new MutationObserver(function(){{"
                f"    if (({find_js}) === null) {{ obs.disconnect(); window._ws['{wfs_key}']='ok'; }}"
                f"  }});"
                f"  obs.observe(document.body||document.documentElement,"
                f"    {{childList:true,subtree:true,attributes:true}});"
                f"  setTimeout(function(){{ obs.disconnect(); if(!window._ws['{wfs_key}']) window._ws['{wfs_key}']='timeout'; }},{timeout});"
                f"}})()"
            )
        else:
            vis = (
                "if(r.width===0&&r.height===0) return null; "
                if state == "visible"
                else ""
            )
            setup_js = (
                f"(function(){{"
                f"  if(!window._ws)window._ws={{}};"
                f"  function chk(){{"
                f"    var el={find_js}; if(!el) return null;"
                f"    var r=el.getBoundingClientRect(); {vis}"
                f"    return JSON.stringify({{x:r.x,y:r.y,w:r.width,h:r.height}});"
                f"  }}"
                f"  var hit=chk(); if(hit){{ window._ws['{wfs_key}']=hit; return; }}"
                f"  var obs=new MutationObserver(function(){{"
                f"    var hit=chk(); if(hit){{ obs.disconnect(); window._ws['{wfs_key}']=hit; }}"
                f"  }});"
                f"  obs.observe(document.body||document.documentElement,"
                f"    {{childList:true,subtree:true,attributes:true}});"
                f"  setTimeout(function(){{ obs.disconnect(); if(!window._ws['{wfs_key}']) window._ws['{wfs_key}']='timeout'; }},{timeout});"
                f"}})()"
            )

        await self._page.evaluate(setup_js)

        deadline = time.time() + (timeout / 1000)
        while time.time() < deadline:
            val = await self._page.evaluate(f"(window._ws||{{}})['{wfs_key}']")
            if val and val != "null":
                await self._page.evaluate(
                    f"try{{delete window._ws['{wfs_key}']}}catch(e){{}}"
                )
                if val == "timeout":
                    raise TimeoutError(
                        f"Locator '{self._selector}' not {state} within {timeout}ms"
                    )
                return
            await asyncio.sleep(0.1)
        try:
            await self._page.evaluate(
                f"try{{delete window._ws['{wfs_key}']}}catch(e){{}}"
            )
        except Exception:
            pass
        raise TimeoutError(f"Locator '{self._selector}' not {state} within {timeout}ms")

    async def click(self, timeout: int = 5000) -> None:
        find_js = self._to_css_and_js()
        deadline = time.time() + (timeout / 1000)
        while time.time() < deadline:
            try:
                js = (
                    f"(function(){{ var el = {find_js}; if(!el) return null; "
                    f"var r = el.getBoundingClientRect(); "
                    f"if(r.width===0&&r.height===0) return null; "
                    f"return JSON.stringify({{x:r.x+r.width/2,y:r.y+r.height/2,w:r.width}}); }})()"
                )
                result = await self._page.evaluate(js)
                if result and isinstance(result, str):
                    pos = json.loads(result)
                    await self._page.mouse.click_smooth(
                        pos["x"], pos["y"], target_width=pos.get("w", 50)
                    )
                    return
            except Exception:
                pass
            await asyncio.sleep(0.3)
        raise TimeoutError(
            f"Locator '{self._selector}' not clickable within {timeout}ms"
        )

    async def text_content(self) -> Optional[str]:
        find_js = self._to_css_and_js()
        result = await self._page.evaluate(
            f"(function(){{ var el = {find_js}; return el ? el.textContent : null; }})()"
        )
        return result

    async def get_attribute(self, name: str) -> Optional[str]:
        find_js = self._to_css_and_js()
        name_escaped = name.replace("'", "\\'")
        result = await self._page.evaluate(
            f"(function(){{ var el = {find_js}; return el ? el.getAttribute('{name_escaped}') : null; }})()"
        )
        return result

    async def count(self) -> int:
        sel = self._selector
        if sel.startswith("text="):
            # Can't easily count text matches, return 0 or 1
            find_js = self._to_css_and_js()
            result = await self._page.evaluate(f"({find_js}) !== null ? 1 : 0")
            return result or 0
        if sel.startswith("css:") or sel.startswith("css="):
            sel = sel[4:]
        sel_escaped = sel.replace("'", "\\'")
        result = await self._page.evaluate(
            f"document.querySelectorAll('{sel_escaped}').length"
        )
        return result or 0


from camoufox.humanize import (
    generate_path as _generate_path,
    hover_delay as _hover_delay,
)


class _Mouse:
    def __init__(self, page: RDPPage):
        self._page = page
        import random as _r

        self._x: float = _r.uniform(300, 700)
        self._y: float = _r.uniform(200, 500)

    async def _raw_move(self, x: float, y: float) -> None:
        if (
            self._page._bridge
            and self._page._bridge.is_connected
            and self._page._tab_id is not None
        ):
            await self._page._bridge.send_command(
                "moveTo", {"tabId": self._page._tab_id, "x": x, "y": y}
            )

    async def _follow_path(self, path):
        for x, y, delay in path:
            await self._raw_move(x, y)
            await asyncio.sleep(delay)
        if path:
            self._x, self._y = path[-1][0], path[-1][1]

    async def click(self, x: float, y: float, button: int = 0, modifiers: int = 0) -> None:
        """Click at (x, y) with humanized movement first.

        Always moves the cursor with sigma-lognormal before clicking, so
        callers cannot accidentally produce a teleport. If the cursor is
        already at the target (distance < 2px), move_smooth is a no-op.
        Use click_smooth() for additional hover delay (visual confirmation pause).
        """
        dx = x - self._x
        dy = y - self._y
        if dx * dx + dy * dy >= 4:  # distance >= 2px
            await self.move_smooth(x, y)

        if (
            self._page._bridge
            and self._page._bridge.is_connected
            and self._page._tab_id is not None
        ):
            await self._page._bridge.send_command(
                "click", {"tabId": self._page._tab_id, "x": self._x, "y": self._y,
                          "button": button, "modifiers": modifiers}
            )
        else:
            await self._page.evaluate(f"document.elementFromPoint({self._x},{self._y})?.click()")

    async def move(self, x: float, y: float) -> None:
        """Instant move (no animation). Use move_smooth() for human-like."""
        await self._raw_move(x, y)
        self._x, self._y = x, y

    async def move_smooth(self, x: float, y: float, target_width: float = 50.0) -> None:
        """Human-like mouse movement with sub-movements, overshoot, tremor."""
        path = _generate_path(self._x, self._y, x, y, target_width)
        await self._follow_path(path)

    async def click_smooth(
        self, x: float, y: float, button: int = 0, target_width: float = 50.0, modifiers: int = 0
    ) -> None:
        """Human-like: move to target, hover delay, click. `modifiers` = bitmask
        nsIDOMWindowUtils (Ctrl=0x0008) para ej. abrir link en background sin perder foco."""
        await self.move_smooth(x, y, target_width)
        await asyncio.sleep(_hover_delay())
        await self.click(self._x, self._y, button, modifiers)

    async def down(self, x: float, y: float, button: int = 0) -> None:
        if (
            self._page._bridge
            and self._page._bridge.is_connected
            and self._page._tab_id is not None
        ):
            await self._page._bridge.send_command(
                "mouseDown",
                {"tabId": self._page._tab_id, "x": x, "y": y, "button": button},
            )

    async def up(self, x: float, y: float, button: int = 0) -> None:
        if (
            self._page._bridge
            and self._page._bridge.is_connected
            and self._page._tab_id is not None
        ):
            await self._page._bridge.send_command(
                "mouseUp",
                {"tabId": self._page._tab_id, "x": x, "y": y, "button": button},
            )

    async def wheel(self, delta_x: float, delta_y: float) -> None:
        """Single wheel event. Use wheel_smooth() for human-like scrolling."""
        if (
            self._page._bridge
            and self._page._bridge.is_connected
            and self._page._tab_id is not None
        ):
            await self._page._bridge.send_command(
                "scroll",
                {
                    "tabId": self._page._tab_id,
                    "x": self._x,
                    "y": self._y,
                    "deltaX": delta_x,
                    "deltaY": delta_y,
                },
            )

    async def wheel_smooth(self, delta_y: float) -> None:
        """Human-like scroll: bursts with momentum decay and reading pauses."""
        from camoufox.humanize import scroll_sequence

        events = scroll_sequence(delta_y)
        for dy, delay in events:
            if abs(dy) > 0.5:
                await self.wheel(0, dy)
            await asyncio.sleep(delay)


class _Keyboard:
    def __init__(self, page: RDPPage):
        self._page = page

    async def type(self, text: str, instant: bool = False) -> None:
        """Type text character-by-character with log-normal inter-key delays.

        Shopee SFU SDK tracks 5 keyboard events with timestamps. Typing the
        whole string at once produces 0ms inter-key intervals (bot signature).
        Set instant=True to bypass humanization (for non-detected contexts).
        """
        if (
            not self._page._bridge
            or not self._page._bridge.is_connected
            or self._page._tab_id is None
        ):
            return

        if instant or not text:
            await self._page._bridge.send_command(
                "type", {"tabId": self._page._tab_id, "text": text}
            )
            return

        from camoufox.humanize import typing_sequence
        for ch, delay in typing_sequence(text):
            try:
                await self._page._bridge.send_command(
                    "type", {"tabId": self._page._tab_id, "text": ch}
                )
            except Exception:
                break
            await asyncio.sleep(delay)

    async def press(self, key: str) -> None:
        if (
            self._page._bridge
            and self._page._bridge.is_connected
            and self._page._tab_id is not None
        ):
            await self._page._bridge.send_command(
                "keyPress", {"tabId": self._page._tab_id, "key": key}
            )


class RDPBrowser:
    """
    Camoufox browser via Firefox RDP + WebExtension.
    Zero detection surface. Passes PerimeterX, Shopee, Akamai.

    Robust initialization: TCP port probe + retry logic eliminates
    race conditions when launching multiple instances.
    """

    # Limit concurrent browser initializations to avoid disk/CPU thrashing
    _init_semaphore: Optional[asyncio.Semaphore] = None

    @classmethod
    def _get_semaphore(cls) -> asyncio.Semaphore:
        if cls._init_semaphore is None:
            cls._init_semaphore = asyncio.Semaphore(2)
        return cls._init_semaphore

    def __init__(
        self,
        executable_path: Optional[str] = None,
        headless: bool = False,
        proxy: Optional[Dict[str, str]] = None,
        viewport: Optional[Dict[str, int]] = None,
        locale: Optional[str] = None,
        timezone: Optional[str] = None,
        rdp_port: int = DEFAULT_RDP_PORT,
        ws_port: int = DEFAULT_WS_PORT,
        firefox_user_prefs: Optional[Dict[str, Any]] = None,
        profile_path: Optional[str] = None,
        extension_dir: str = EXTENSION_DIR,
        fingerprint: Optional[Dict[str, Any]] = None,
        allow_addon_newtab: bool = False,
    ):
        self._fingerprint = fingerprint
        # Camoufox bloquea browser.tabs.create por default (patch disable-extension-newtab,
        # gateado por camouGetBool('allowAddonNewtab')). Habilitarlo es chrome-level, NO
        # observable por la pagina -> no afecta anti-deteccion. Necesario para multi-tab.
        self._allow_addon_newtab = allow_addon_newtab

        # Derive viewport, timezone, locale from fingerprint if not explicit
        if fingerprint:
            if not viewport:
                ow = fingerprint.get("window.outerWidth", 1920)
                oh = fingerprint.get("window.outerHeight", 1040)
                dpr = fingerprint.get("window.devicePixelRatio", 1.0)
                # Firefox --width/--height are physical pixels. With DPR > 1
                # the CSS viewport = physical / DPR. Multiply by DPR so the
                # actual CSS viewport matches the spoofed outerWidth/Height.
                if dpr and dpr > 1.0:
                    ow = int(ow * dpr)
                    oh = int(oh * dpr)
                viewport = {"width": ow, "height": oh}
            if not timezone:
                timezone = fingerprint.get("timezone")
            if not locale:
                lang = fingerprint.get("locale:language", "en")
                region = fingerprint.get("locale:region", "US")
                locale = f"{lang}-{region}, {lang}, en-US, en"

        self._executable = executable_path or _get_default_binary()
        self._headless = headless
        self._proxy = proxy
        self._viewport = viewport or {"width": 1920, "height": 1080}
        self._locale = locale
        self._timezone = timezone
        self._rdp_port = rdp_port
        self._ws_port = ws_port
        self._user_prefs = firefox_user_prefs or {}
        self._profile_path = profile_path
        self._extension_dir = extension_dir
        self._proc: Optional[subprocess.Popen] = None
        self._job = None  # Windows Job Object for process tree cleanup
        self._client: Optional[RDPClient] = None
        self._bridge: Optional[_ExtensionBridge] = None
        self._temp_profile = False
        self._temp_dirs: List[str] = []

    async def __aenter__(self) -> "RDPBrowser":
        await self.start()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    def _prepare_extension_with_proxy(
        self, proxy_host: str, proxy_port: int, username: str, password: str
    ) -> str:
        """Copy extension to temp dir and inject proxy routing + auth."""
        ext_copy = os.path.join(self._profile_path, "_ext_with_proxy")
        if os.path.exists(ext_copy):
            shutil.rmtree(ext_copy)
        shutil.copytree(EXTENSION_DIR, ext_copy)
        bg_path = os.path.join(ext_copy, "background.js")
        with open(bg_path, "r", encoding="utf-8") as f:
            content = f.read()

        proxy_js = (
            f"let proxyConfig = {{\n"
            f'  host: "{proxy_host}",\n'
            f"  port: {proxy_port}\n"
            f"}};\n"
            f'let proxyCredentials = {{ username: "{username}", password: "{password}" }};\n'
            f"\n"
            f"browser.proxy.onRequest.addListener(\n"
            f"  (details) => {{\n"
            f'    if (details.url.startsWith("ws://127.0.0.1") ||\n'
            f'        details.url.startsWith("http://127.0.0.1") ||\n'
            f'        details.url.startsWith("http://localhost")) {{\n'
            f'      return {{ type: "direct" }};\n'
            f"    }}\n"
            f"    return {{\n"
            f'      type: "http",\n'
            f"      host: proxyConfig.host,\n"
            f"      port: proxyConfig.port\n"
            f"    }};\n"
            f"  }},\n"
            f'  {{ urls: ["<all_urls>"] }}\n'
            f");\n"
            f"\n"
            f"browser.webRequest.onAuthRequired.addListener(\n"
            f"  (details) => {{\n"
            f"    if (details.isProxy && proxyCredentials) {{\n"
            f"      return {{ authCredentials: proxyCredentials }};\n"
            f"    }}\n"
            f"  }},\n"
            f'  {{ urls: ["<all_urls>"] }},\n'
            f'  ["blocking"]\n'
            f");\n"
        )

        content = content.replace(
            "let proxyConfig = null;\nlet proxyCredentials = null;", proxy_js
        )
        with open(bg_path, "w", encoding="utf-8") as f:
            f.write(content)
        self._temp_dirs.append(ext_copy)
        return ext_copy

    async def start(self) -> None:
        if not self._profile_path:
            self._profile_path = tempfile.mkdtemp(prefix="camou_rdp_")
            self._temp_profile = True
            self._temp_dirs.append(self._profile_path)

        os.makedirs(self._profile_path, exist_ok=True)

        prefs = {
            "extensions.experiments.enabled": True,
            "xpinstall.signatures.required": False,
            "extensions.autoDisableScopes": 0,
            "extensions.enabledScopes": 15,
            "browser.startup.page": 0,
            "browser.startup.homepage_override.mstone": "ignore",
            "browser.aboutwelcome.enabled": False,
            "browser.newtabpage.enabled": False,
            "browser.safebrowsing.enabled": False,
            "browser.safebrowsing.malware.enabled": False,
            "browser.safebrowsing.phishing.enabled": False,
            "network.captive-portal-service.enabled": False,
            "network.connectivity-service.enabled": False,
            "app.update.enabled": False,
            "extensions.getAddons.showPane": False,
            "extensions.getAddons.cache.enabled": False,
            # Anti-detection: force-detach debugger thread actor so WAF
            # debugger traps don't fire. Current binary reads librewolf.*
            # namespace (patch bug fixed for next build).
            "librewolf.debugger.force_detach": True,
            # Restore session history so back/forward works normally.
            # camoufox.cfg defaults to 0 which is detectable.
            "browser.sessionhistory.max_entries": 50,
            # Enable async event dispatch so sendMouseEvent crosses
            # Fission process boundaries to reach content.
            "test.events.async.enabled": True,
            # Tell the extension exactly which WS port to connect to
            # (avoids scanning 8775-8790 which causes multi-instance conflicts).
            "extensions.input.ws_port": self._ws_port,
            # Fix Bug 1749009: proxy onAuthRequired + blocking breaks iframe
            # loading (COEP check on 407 response). Captcha iframes behind
            # authenticated proxies fail with NS_ERROR_DOM_CORP_FAILED.
            "browser.tabs.remote.useCrossOriginEmbedderPolicy": False,
        }
        if self._proxy:
            parsed = urlparse(
                self._proxy["server"]
                if "://" in self._proxy.get("server", "")
                else f"http://{self._proxy.get('server', '')}"
            )
            proxy_host = parsed.hostname or ""
            proxy_port = parsed.port or 8080
            if self._proxy.get("username"):
                self._extension_dir = self._prepare_extension_with_proxy(
                    proxy_host,
                    proxy_port,
                    self._proxy["username"],
                    self._proxy.get("password", ""),
                )
            else:
                prefs["network.proxy.type"] = 1
                prefs["network.proxy.http"] = proxy_host
                prefs["network.proxy.http_port"] = proxy_port
                prefs["network.proxy.ssl"] = proxy_host
                prefs["network.proxy.ssl_port"] = proxy_port
                prefs["network.proxy.no_proxies_on"] = "localhost, 127.0.0.1"
        if self._locale:
            prefs["intl.accept_languages"] = self._locale

        # prefers-color-scheme: apply from fingerprint profile (50/50 light/dark
        # avoids the 100%-dark-mode anomaly in CreepJS headlessRating).
        # Fallback for legacy profiles without the field: derive deterministically
        # from canvas:seed so the same profile always gets the same scheme.
        if self._fingerprint:
            scheme = self._fingerprint.get("_prefers_color_scheme")
            if not scheme:
                canvas_seed = int(self._fingerprint.get("canvas:seed", 0) or 0)
                scheme = "dark" if (canvas_seed & 1) else "light"
            # layout.css.prefers-color-scheme.content-override: 1=light, 2=dark
            if scheme == "dark":
                prefs["ui.systemUsesDarkTheme"] = 1
                prefs["layout.css.prefers-color-scheme.content-override"] = 2
            else:
                prefs["ui.systemUsesDarkTheme"] = 0
                prefs["layout.css.prefers-color-scheme.content-override"] = 1

        _write_user_prefs(self._profile_path, prefs)

        args = [
            self._executable,
            "--new-instance",
            "--no-remote",
            f"--start-debugger-server={self._rdp_port}",
            "--profile",
            self._profile_path,
            f"--width={self._viewport['width']}",
            f"--height={self._viewport['height']}",
        ]
        if self._headless:
            args.append("--headless")

        self._bridge = _ExtensionBridge(self._ws_port)
        await self._bridge.start()

        env = os.environ.copy()
        # Config CAMOU unificado: fingerprint (sin _meta) + timezone + flags.
        config: Dict[str, Any] = {}
        if self._fingerprint:
            config = {
                k: v for k, v in self._fingerprint.items() if not k.startswith("_")
            }
        if self._timezone:
            env["TZ"] = self._timezone
            config.setdefault("timezone", self._timezone)
        if self._allow_addon_newtab:
            config["allowAddonNewtab"] = True
        if config:
            config_str = json.dumps(config)
            chunk_size = 2047
            for i in range(0, len(config_str), chunk_size):
                chunk = config_str[i : i + chunk_size]
                env[f"CAMOU_CONFIG_{(i // chunk_size) + 1}"] = chunk

        logger.info(f"Launching Camoufox RDP on port {self._rdp_port}")
        self._proc = subprocess.Popen(args, env=env)

        # Assign to Job Object so all child processes are killed on close
        if _kernel32 and self._proc:
            self._job = _create_job_object()
            if self._job:
                _kernel32.AssignProcessToJobObject(self._job, int(self._proc._handle))

        await self._connect_rdp()
        await self._install_extension()
        await self._wait_for_bridge()
        await self._apply_overrides()

    async def _connect_rdp(self, max_retries: int = 5) -> None:
        await _wait_for_port("localhost", self._rdp_port, timeout=30.0)
        for i in range(max_retries):
            try:
                client = RDPClient(timeout_sec=10)
                client.connect("localhost", self._rdp_port)
                self._client = client
                logger.info("RDP connected")
                return
            except Exception as e:
                if self._proc and self._proc.poll() is not None:
                    raise RuntimeError("Camoufox process exited unexpectedly")
                if i < max_retries - 1:
                    await asyncio.sleep(1)
                else:
                    raise ConnectionError(f"RDP connection failed: {e}")

    async def _install_extension(self, max_retries: int = 3) -> None:
        """Install the WebExtension with retry logic."""
        if not os.path.isdir(self._extension_dir):
            logger.warning(f"Extension dir not found: {self._extension_dir}")
            return

        for attempt in range(1, max_retries + 1):
            try:
                root = RootActor(self._client)
                root_data = root.get_root()
                if not root_data:
                    raise RuntimeError("get_root returned None")
                addons_id = root_data.get("addonsActor", "")
                if not addons_id:
                    raise RuntimeError("No addonsActor available")

                addons = AddonsActor(self._client, addons_id)
                ext_path = os.path.abspath(self._extension_dir)
                result = addons.install_temporary_addon(ext_path)
                logger.info(f"Extension installed (attempt {attempt}): {result}")
                return
            except Exception as e:
                logger.debug(f"Extension install attempt {attempt} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(1.0 * attempt)
                else:
                    logger.warning(
                        f"Extension install failed after {max_retries} attempts: {e}"
                    )

    async def _wait_for_bridge(self, timeout: float = 10.0) -> None:
        """Wait for the extension WebSocket bridge to connect."""
        if not self._bridge:
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._bridge.is_connected:
                logger.info("Extension bridge connected")
                return
            await asyncio.sleep(0.5)
        logger.warning(f"Extension bridge not connected after {timeout}s")

    async def _apply_overrides(self) -> None:
        """Apply timezone via window.setTimezone() WebIDL method (Camoufox built-in)."""
        if not self._timezone:
            return
        try:
            root = RootActor(self._client)
            tabs = root.list_tabs()
            if not tabs:
                return
            tab = TabActor(self._client, tabs[0].get("actor", ""))
            target = tab.get_target()
            console_id = target.get("consoleActor", "")
            if not console_id:
                return
            console = WebConsoleActor(self._client, console_id)
            console.start_listeners([])
            console.evaluate_js_async(f'window.setTimezone("{self._timezone}")')
            logger.info(f"Timezone override applied: {self._timezone}")
        except Exception as e:
            logger.debug(f"Timezone override via JS failed: {e}")

    def _read_stderr(self) -> str:
        try:
            if hasattr(self, "_stderr_file") and self._stderr_file:
                self._stderr_file.flush()
                with open(self._stderr_file.name, "r", errors="replace") as f:
                    return f.read()[-1000:]
        except Exception:
            pass
        return ""

    def is_alive(self) -> bool:
        """Check if the browser process is still running."""
        return self._proc is not None and self._proc.poll() is None

    def is_connected(self) -> bool:
        """Check if the RDP connection is alive."""
        if not self._client:
            return False
        try:
            return self._client.connected()
        except Exception:
            return False

    async def new_page(self) -> RDPPage:
        for attempt in range(10):
            root = RootActor(self._client)
            tabs = root.list_tabs()
            if tabs and isinstance(tabs, list) and len(tabs) > 0:
                tab_desc = tabs[0]
                tab_actor_id = tab_desc.get("actor", "")
                tab = TabActor(self._client, tab_actor_id)
                target = tab.get_target()
                if target and isinstance(target, dict) and target.get("actor"):
                    tab_id = None
                    if self._bridge and self._bridge.is_connected:
                        try:
                            result = await self._bridge.send_command(
                                "getActiveTab", {}, timeout=3
                            )
                            if result:
                                tab_id = result.get("tabId")
                        except Exception:
                            pass

                    page = RDPPage(
                        client=self._client,
                        tab_actor_id=tab_actor_id,
                        target_actor_id=target.get("actor", ""),
                        console_actor_id=target.get("consoleActor", ""),
                        browsing_context_id=target.get("browsingContextID"),
                        bridge=self._bridge,
                        tab_id=tab_id,
                    )
                    await asyncio.to_thread(page._start_persistent_watcher)
                    return page
            await asyncio.sleep(1)

        raise RuntimeError("No tabs available after waiting")

    # ── Multi-tab real (via WebExtension browser.tabs) ────────────────────────

    async def list_tabs(self) -> List[Dict]:
        """Todas las tabs reales del browser (incluidas las de window.open).

        Usa la WebExtension (autoritativa, trae tabId). Fallback a RootActor RDP.
        """
        if self._bridge and self._bridge.is_connected:
            try:
                r = await self._bridge.send_command("listTabs", {}, timeout=5)
                if r and "tabs" in r:
                    return r["tabs"]
            except Exception:
                pass

        def _rdp():
            root = RootActor(self._client)
            out = []
            for t in (root.list_tabs() or []):
                out.append({
                    "tabId": t.get("browserId"),
                    "url": t.get("url"),
                    "title": t.get("title"),
                    "active": bool(t.get("selected")),
                })
            return out

        return await asyncio.to_thread(_rdp)

    async def create_tab(self, url: Optional[str] = None, active: bool = True) -> Dict:
        """Abre una tab NUEVA de verdad (browser.tabs.create). Devuelve {tabId,url,index}."""
        if not (self._bridge and self._bridge.is_connected):
            raise ConnectionError("Extension bridge not connected, cannot create tab")
        return await self._bridge.send_command(
            "createTab", {"url": url or "about:blank", "active": active}, timeout=15
        ) or {}

    async def activate_tab(self, tab_id: int) -> None:
        if self._bridge and self._bridge.is_connected:
            await self._bridge.send_command("activateTab", {"tabId": tab_id}, timeout=5)

    async def close_tab(self, tab_id: int) -> None:
        if self._bridge and self._bridge.is_connected:
            await self._bridge.send_command("closeTab", {"tabId": tab_id}, timeout=5)

    async def close(self) -> None:
        if self._bridge:
            await self._bridge.stop()
            self._bridge = None

        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None

        if self._proc:
            # Graceful shutdown: terminate first to let Firefox flush cookies/state
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Force kill if graceful shutdown failed
                if self._job and _kernel32:
                    _kernel32.TerminateJobObject(self._job, 1)
                else:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                try:
                    self._proc.wait(timeout=3)
                except Exception:
                    pass
            if self._job and _kernel32:
                _kernel32.CloseHandle(self._job)
                self._job = None
            self._proc = None
            await asyncio.sleep(0.5)

        if self._temp_profile:
            for d in self._temp_dirs:
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass

        logger.info("RDPBrowser closed")
