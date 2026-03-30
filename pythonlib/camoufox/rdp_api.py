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
from geckordp.actors.resources import Resources
from geckordp.actors.root import RootActor
from geckordp.actors.screenshot import ScreenshotActor
from geckordp.actors.string import StringActor
from geckordp.actors.targets.window_global import WindowGlobalActor
from geckordp.actors.watcher import WatcherActor
from geckordp.actors.web_console import WebConsoleActor
from geckordp.rdp_client import RDPClient

logger = logging.getLogger(__name__)

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
        self.mouse = _Mouse(self)
        self.keyboard = _Keyboard(self)

    def _refresh_target(self):
        tab = TabActor(self._client, self._tab_actor_id)
        target = tab.get_target()
        if target and isinstance(target, dict):
            new_console = target.get("consoleActor", "")
            if new_console and new_console != self._console_actor_id:
                self._console_actor_id = new_console
                self._console_started = False
            self._target_actor_id = target.get("actor", self._target_actor_id)
            self._browsing_context_id = target.get("browsingContextID", self._browsing_context_id)

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
        try:
            result = self._eval_sync("window.location.href")
            if isinstance(result, str):
                self._url = result
        except Exception:
            pass
        return self._url

    async def goto(self, url: str, wait_until: str = "load", timeout: int = 30000) -> None:
        target_event = "dom-complete" if wait_until in ("load", "networkidle") else "dom-interactive"
        load_done = asyncio.Event()

        def _on_resource(data):
            for item in data.get("array", data.get("resources", [data])):
                if isinstance(item, dict) and item.get("resourceType") == "document-event":
                    name = item.get("name", "")
                    if name == "dom-complete" or (target_event == "dom-interactive" and name == "dom-interactive"):
                        load_done.set()

        # Setup watcher for document events
        tab = TabActor(self._client, self._tab_actor_id)
        watcher_ctx = tab.get_watcher()
        watcher = WatcherActor(self._client, watcher_ctx["actor"])
        watcher.watch_resources([Resources.DOCUMENT_EVENT])
        self._client.add_event_listener(
            watcher_ctx["actor"],
            Events.Watcher.RESOURCES_AVAILABLE_ARRAY,
            _on_resource,
        )

        try:
            def _nav():
                wg = WindowGlobalActor(self._client, self._target_actor_id)
                wg.navigate_to(url)

            await asyncio.to_thread(_nav)
            self._url = url
            self._console_started = False

            try:
                await asyncio.wait_for(load_done.wait(), timeout=timeout / 1000)
            except asyncio.TimeoutError:
                pass
        finally:
            self._client.remove_event_listener(
                watcher_ctx["actor"],
                Events.Watcher.RESOURCES_AVAILABLE_ARRAY,
                _on_resource,
            )
            try:
                watcher.unwatch_resources([Resources.DOCUMENT_EVENT])
            except Exception:
                pass

        # Refresh target after navigation (cross-process)
        await asyncio.to_thread(self._refresh_target)

    async def reload(self, timeout: int = 30000) -> None:
        def _reload():
            wg = WindowGlobalActor(self._client, self._target_actor_id)
            wg.reload()

        await asyncio.to_thread(_reload)
        deadline = time.time() + (timeout / 1000)
        while time.time() < deadline:
            await asyncio.sleep(0.5)
            try:
                state = await self.evaluate("document.readyState")
                if state == "complete":
                    return
            except Exception:
                pass

    async def content(self) -> str:
        return await self.evaluate("document.documentElement.outerHTML") or ""

    async def evaluate(self, expression: str) -> Any:
        return await asyncio.to_thread(self._eval_sync, expression)

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

        if self._bridge and self._bridge.is_connected and self._tab_id is not None:
            await self._bridge.send_command("click", {"tabId": self._tab_id, "x": x, "y": y})
        else:
            await self.evaluate(
                f"document.querySelector('{selector}').click()"
            )

    async def fill(self, selector: str, text: str) -> None:
        await self.click(selector)
        await asyncio.sleep(0.1)

        if self._bridge and self._bridge.is_connected and self._tab_id is not None:
            await self._bridge.send_command("type", {"tabId": self._tab_id, "text": text})
        else:
            await self.evaluate(
                f"(function(){{ var el = document.querySelector('{selector}');"
                f"el.value = '{text}';"
                f"el.dispatchEvent(new Event('input', {{bubbles:true}}));"
                f"el.dispatchEvent(new Event('change', {{bubbles:true}})); }})()"
            )

    async def screenshot(self, path: Optional[str] = None) -> bytes:
        if self._bridge and self._bridge.is_connected:
            result = await self._bridge.send_command("screenshot", {})
            if result and result.get("dataUrl"):
                b64 = result["dataUrl"].split(",", 1)[1] if "," in result["dataUrl"] else result["dataUrl"]
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
            b64_data = result.get("value", {}).get("data", "") if isinstance(result.get("value"), dict) else result.get("value", "")
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

    async def wait_for_load_state(self, state: str = "load", timeout: int = 30000) -> None:
        target = "complete" if state in ("load", "networkidle") else "interactive"
        deadline = time.time() + (timeout / 1000)
        while time.time() < deadline:
            try:
                current = await self.evaluate("document.readyState")
                if current == target or current == "complete":
                    return
            except Exception:
                pass
            await asyncio.sleep(0.5)

    async def wait_for_selector(self, selector: str, timeout: int = 30000,
                                 state: str = "visible") -> Optional[Dict]:
        """Wait for an element matching selector to appear.
        state: 'visible', 'attached', or 'hidden'.
        Returns element rect or None on timeout."""
        sel_escaped = selector.replace("'", "\\'")
        deadline = time.time() + (timeout / 1000)
        while time.time() < deadline:
            try:
                if state == "hidden":
                    gone = await self.evaluate(
                        f"document.querySelector('{sel_escaped}') === null"
                    )
                    if gone:
                        return {}
                else:
                    js = (
                        f"(function(){{ var el = document.querySelector('{sel_escaped}');"
                        f"if(!el) return null;"
                    )
                    if state == "visible":
                        js += (
                            f"var r = el.getBoundingClientRect();"
                            f"if(r.width===0 && r.height===0) return null;"
                            f"return JSON.stringify({{x:r.x,y:r.y,w:r.width,h:r.height}});"
                        )
                    else:
                        js += (
                            f"var r = el.getBoundingClientRect();"
                            f"return JSON.stringify({{x:r.x,y:r.y,w:r.width,h:r.height}});"
                        )
                    js += "})()"
                    result = await self.evaluate(js)
                    if result and isinstance(result, str):
                        return json.loads(result)
            except Exception:
                pass
            await asyncio.sleep(0.3)
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
        deadline = time.time() + (timeout / 1000)
        while time.time() < deadline:
            try:
                if state == "hidden":
                    result = await self._page.evaluate(f"({find_js}) === null")
                    if result:
                        return
                else:
                    js = (
                        f"(function(){{ var el = {find_js}; if(!el) return null; "
                        f"var r = el.getBoundingClientRect(); "
                    )
                    if state == "visible":
                        js += "if(r.width===0&&r.height===0) return null; "
                    js += "return JSON.stringify({x:r.x,y:r.y,w:r.width,h:r.height}); })()"
                    result = await self._page.evaluate(js)
                    if result and isinstance(result, str):
                        return
            except Exception:
                pass
            await asyncio.sleep(0.3)
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
                    f"return JSON.stringify({{x:r.x+r.width/2,y:r.y+r.height/2}}); }})()"
                )
                result = await self._page.evaluate(js)
                if result and isinstance(result, str):
                    pos = json.loads(result)
                    if (self._page._bridge and self._page._bridge.is_connected
                            and self._page._tab_id is not None):
                        await self._page._bridge.send_command(
                            "click", {"tabId": self._page._tab_id, "x": pos["x"], "y": pos["y"]}
                        )
                    else:
                        await self._page.evaluate(f"({find_js}).click()")
                    return
            except Exception:
                pass
            await asyncio.sleep(0.3)
        raise TimeoutError(f"Locator '{self._selector}' not clickable within {timeout}ms")

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


class _Mouse:
    def __init__(self, page: RDPPage):
        self._page = page

    async def click(self, x: float, y: float, button: int = 0) -> None:
        if self._page._bridge and self._page._bridge.is_connected and self._page._tab_id is not None:
            await self._page._bridge.send_command(
                "click", {"tabId": self._page._tab_id, "x": x, "y": y, "button": button}
            )
        else:
            await self._page.evaluate(
                f"document.elementFromPoint({x},{y})?.click()"
            )

    async def move(self, x: float, y: float) -> None:
        if self._page._bridge and self._page._bridge.is_connected and self._page._tab_id is not None:
            await self._page._bridge.send_command(
                "moveTo", {"tabId": self._page._tab_id, "x": x, "y": y}
            )

    async def down(self, x: float, y: float, button: int = 0) -> None:
        if self._page._bridge and self._page._bridge.is_connected and self._page._tab_id is not None:
            await self._page._bridge.send_command(
                "mouseDown", {"tabId": self._page._tab_id, "x": x, "y": y, "button": button}
            )

    async def up(self, x: float, y: float, button: int = 0) -> None:
        if self._page._bridge and self._page._bridge.is_connected and self._page._tab_id is not None:
            await self._page._bridge.send_command(
                "mouseUp", {"tabId": self._page._tab_id, "x": x, "y": y, "button": button}
            )

    async def wheel(self, delta_x: float, delta_y: float) -> None:
        if self._page._bridge and self._page._bridge.is_connected and self._page._tab_id is not None:
            await self._page._bridge.send_command(
                "scroll", {"tabId": self._page._tab_id, "x": 400, "y": 300, "deltaX": delta_x, "deltaY": delta_y}
            )
        else:
            await self._page.evaluate(
                f"window.scrollBy({{left:{delta_x},top:{delta_y},behavior:'smooth'}})"
            )


class _Keyboard:
    def __init__(self, page: RDPPage):
        self._page = page

    async def type(self, text: str) -> None:
        if self._page._bridge and self._page._bridge.is_connected and self._page._tab_id is not None:
            await self._page._bridge.send_command(
                "type", {"tabId": self._page._tab_id, "text": text}
            )

    async def press(self, key: str) -> None:
        if self._page._bridge and self._page._bridge.is_connected and self._page._tab_id is not None:
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
    ):
        self._fingerprint = fingerprint

        # Derive viewport, timezone, locale from fingerprint if not explicit
        if fingerprint:
            if not viewport:
                ow = fingerprint.get("window.outerWidth", 1920)
                oh = fingerprint.get("window.outerHeight", 1040)
                viewport = {"width": ow, "height": oh}
            if not timezone:
                timezone = fingerprint.get("timezone")
            if not locale:
                lang = fingerprint.get("locale:language", "en")
                region = fingerprint.get("locale:region", "US")
                locale = f"{lang}-{region}"

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

    def _prepare_extension_with_proxy(self, proxy_host: str, proxy_port: int,
                                      username: str, password: str) -> str:
        """Copy extension to temp dir and inject proxy routing + auth."""
        ext_copy = os.path.join(self._profile_path, "_ext_with_proxy")
        shutil.copytree(EXTENSION_DIR, ext_copy)
        bg_path = os.path.join(ext_copy, "background.js")
        with open(bg_path, "r", encoding="utf-8") as f:
            content = f.read()

        proxy_js = (
            f'let proxyConfig = {{\n'
            f'  host: "{proxy_host}",\n'
            f'  port: {proxy_port}\n'
            f'}};\n'
            f'let proxyCredentials = {{ username: "{username}", password: "{password}" }};\n'
            f'\n'
            f'browser.proxy.onRequest.addListener(\n'
            f'  (details) => {{\n'
            f'    if (details.url.startsWith("ws://127.0.0.1") ||\n'
            f'        details.url.startsWith("http://127.0.0.1") ||\n'
            f'        details.url.startsWith("http://localhost")) {{\n'
            f'      return {{ type: "direct" }};\n'
            f'    }}\n'
            f'    return {{\n'
            f'      type: "http",\n'
            f'      host: proxyConfig.host,\n'
            f'      port: proxyConfig.port\n'
            f'    }};\n'
            f'  }},\n'
            f'  {{ urls: ["<all_urls>"] }}\n'
            f');\n'
            f'\n'
            f'browser.webRequest.onAuthRequired.addListener(\n'
            f'  (details) => {{\n'
            f'    if (details.isProxy && proxyCredentials) {{\n'
            f'      return {{ authCredentials: proxyCredentials }};\n'
            f'    }}\n'
            f'  }},\n'
            f'  {{ urls: ["<all_urls>"] }},\n'
            f'  ["blocking"]\n'
            f');\n'
        )

        content = content.replace(
            "let proxyConfig = null;\nlet proxyCredentials = null;",
            proxy_js
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
            "extensions.camoufox.ws_port": self._ws_port,
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
                    proxy_host, proxy_port,
                    self._proxy["username"], self._proxy.get("password", "")
                )
            else:
                prefs["network.proxy.type"] = 1
                prefs["network.proxy.http"] = proxy_host
                prefs["network.proxy.http_port"] = proxy_port
                prefs["network.proxy.ssl"] = proxy_host
                prefs["network.proxy.ssl_port"] = proxy_port
                prefs["network.proxy.no_proxies_on"] = "localhost, 127.0.0.1"
        if self._locale and not self._fingerprint:
            prefs["intl.accept_languages"] = self._locale
        _write_user_prefs(self._profile_path, prefs)

        args = [
            self._executable,
            "--new-instance",
            "--no-remote",
            f"--start-debugger-server={self._rdp_port}",
            "--profile", self._profile_path,
            f"--width={self._viewport['width']}",
            f"--height={self._viewport['height']}",
        ]
        if self._headless:
            args.append("--headless")

        self._bridge = _ExtensionBridge(self._ws_port)
        await self._bridge.start()

        env = os.environ.copy()
        if self._fingerprint:
            # Full fingerprint config: strip _meta, chunk for Windows env var limit
            fp_config = {k: v for k, v in self._fingerprint.items()
                         if not k.startswith("_")}
            config_str = json.dumps(fp_config)
            chunk_size = 2047
            for i in range(0, len(config_str), chunk_size):
                chunk = config_str[i:i + chunk_size]
                env[f"CAMOU_CONFIG_{(i // chunk_size) + 1}"] = chunk
            if self._timezone:
                env["TZ"] = self._timezone
        elif self._timezone:
            env["TZ"] = self._timezone
            config = {"timezone": self._timezone}
            config_str = json.dumps(config)
            env["CAMOU_CONFIG_1"] = config_str

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

    async def _connect_rdp(self, max_retries: int = 20) -> None:
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
                    logger.warning(f"Extension install failed after {max_retries} attempts: {e}")

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
            if hasattr(self, '_stderr_file') and self._stderr_file:
                self._stderr_file.flush()
                with open(self._stderr_file.name, 'r', errors='replace') as f:
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
                            result = await self._bridge.send_command("getActiveTab", {}, timeout=3)
                            if result:
                                tab_id = result.get("tabId")
                        except Exception:
                            pass

                    return RDPPage(
                        client=self._client,
                        tab_actor_id=tab_actor_id,
                        target_actor_id=target.get("actor", ""),
                        console_actor_id=target.get("consoleActor", ""),
                        browsing_context_id=target.get("browsingContextID"),
                        bridge=self._bridge,
                        tab_id=tab_id,
                    )
            await asyncio.sleep(1)

        raise RuntimeError("No tabs available after waiting")

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
            # Kill all processes in the Job Object (browser + all children)
            if self._job and _kernel32:
                _kernel32.TerminateJobObject(self._job, 1)
                _kernel32.CloseHandle(self._job)
                self._job = None
            else:
                self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
            await asyncio.sleep(0.5)

        if self._temp_profile:
            for d in self._temp_dirs:
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass

        logger.info("RDPBrowser closed")
