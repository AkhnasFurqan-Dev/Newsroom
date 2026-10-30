from __future__ import annotations

import ctypes
import html
import os
import socket
import sys
import threading
import time
from pathlib import Path


def _configure_environment() -> None:
    os.environ["CHANNEL_PRESS_DESKTOP"] = "1"
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            data_root = Path(local_app_data) / "ChannelPress"
            os.environ.setdefault("CHANNEL_PRESS_DATA_DIR", str(data_root))
            os.environ.setdefault("CHANNEL_PRESS_OUTPUT_DIR", str(data_root / "output"))


_START_PAGE = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Channel Press</title>
<style>
body{margin:0;background:#f4f1e8;color:#242321;font:16px/1.5 Segoe UI,sans-serif}
main{max-width:680px;margin:18vh auto;padding:32px 38px;border-top:4px solid #8e2d2b;background:#fff}
h1{font:600 30px Georgia,serif;margin:0 0 8px}p{color:#62605b}
</style><main><h1>Channel Press</h1><p>Starting the local newsroom. This window will continue when the workspace is ready.</p>
<p id="status">Checking local services…</p>
<div id="close" hidden><p>Choose what should happen to the current work before closing.</p>
<button onclick="window.pywebview.api.shutdown('continue')">Continue work in background</button>
<button onclick="window.pywebview.api.shutdown('pause')">Pause safely and exit</button>
<button onclick="document.getElementById('close').hidden=true">Keep Channel Press open</button></div>
<script>window.addEventListener('channel-press-close-request',()=>document.getElementById('close').hidden=false)</script>
</main></html>"""


def _focus_existing_window() -> None:
    if os.name != "nt":
        return
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = []

    def inspect(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length:
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            if "channel press" in title.value.casefold():
                found.append(hwnd)
                return False
        return True

    callback = callback_type(inspect)
    user32.EnumWindows(callback, 0)
    if found:
        user32.ShowWindow(found[0], 9)
        user32.SetForegroundWindow(found[0])


def _acquire_instance_mutex():
    if os.name != "nt":
        return True, None
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, "Local\\ChannelPress.Desktop")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel32.CloseHandle(handle)
        return False, None
    return True, handle


class DesktopBridge:
    def __init__(self) -> None:
        self.window = None
        self.server = None
        self.allow_close = False

    def on_closing(self):
        if self.allow_close:
            return True
        if self.window is not None:
            self.window.evaluate_js(
                "window.dispatchEvent(new Event('channel-press-close-request'))"
            )
        return False

    def shutdown(self, choice: str) -> dict[str, str | bool]:
        if choice not in {"continue", "pause"}:
            return {"closed": False, "message": "Channel Press remains open."}

        def finish() -> None:
            from channel_press.ai.model_manager import cancel_and_wait, wait_for_smoke_test
            from channel_press.processes import pause_owned_workers

            if choice == "continue":
                if self.window is not None:
                    self.window.hide()
                return
            if choice == "pause" and (
                not cancel_and_wait() or not wait_for_smoke_test() or not pause_owned_workers()
            ):
                if self.window is not None:
                    self.window.evaluate_js(
                        "document.getElementById('desktop-close-status').textContent="
                        + repr("Work is still reaching a safe checkpoint. Keep this window open.")
                        + ";document.querySelectorAll('[data-desktop-close]').forEach((button)=>button.disabled=false)"
                    )
                return
            self.allow_close = True
            if self.server is not None:
                self.server.should_exit = True
            if self.window is not None:
                self.window.destroy()

        threading.Thread(target=finish, name="channel-press-shutdown", daemon=True).start()
        return {
            "closed": True,
            "message": (
                "The current worker will finish in the background."
                if choice == "continue"
                else "Channel Press is pausing at a safe checkpoint."
            ),
        }


def _starting_page(log_path: Path | None = None) -> str:
    page = _START_PAGE.replace(
        "</main>",
        '<p id="desktop-close-status" aria-live="polite"></p></main>',
    )
    if log_path:
        page = page.replace(
            "Checking local services…",
            "The local service could not start. Details were saved in the private diagnostics log.",
        )
        page = page.replace(
            "</main>",
            f'<p class="small">{html.escape(str(log_path))}</p></main>',
        )
    return page


def _launch_backend(window, bridge: DesktopBridge) -> None:
    from channel_press.config import settings
    from channel_press.logging_setup import configure_logging, get_logger

    logger = get_logger("desktop")
    log_path = None
    listener = None
    server = None
    server_thread = None
    try:
        settings.ensure_dirs()
        log_path = configure_logging("desktop", console=False)
        from channel_press.migrations import upgrade_database

        upgrade_database()
        import uvicorn

        from channel_press.web.app import app

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(socket.SOMAXCONN)
        listener.setblocking(False)
        port = listener.getsockname()[1]
        settings.host = "127.0.0.1"
        settings.port = port
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_config=None,
                access_log=False,
            )
        )
        bridge.server = server
        failure: list[BaseException] = []

        def run_server() -> None:
            try:
                server.run(sockets=[listener])
            except BaseException as exc:
                failure.append(exc)
                logger.exception("desktop_api_failed", extra={"kind": "desktop"})

        server_thread = threading.Thread(target=run_server, name="channel-press-api", daemon=True)
        server_thread.start()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and not server.started and server_thread.is_alive():
            time.sleep(0.05)
        if failure:
            raise RuntimeError(f"Local service failed: {type(failure[0]).__name__}")
        if not server.started:
            raise TimeoutError("The local newsroom service did not become ready in 90 seconds.")
        window.load_url(f"http://127.0.0.1:{port}/")
        while server_thread.is_alive() and not bridge.allow_close:
            time.sleep(0.1)
    # Catch at the process boundary so startup errors are recorded and shown in the window.
    except Exception as exc:
        logger.exception("desktop_start_failed", extra={"kind": "desktop"})
        window.load_html(_starting_page(log_path))
        window.evaluate_js(
            "document.getElementById('status').textContent="
            + repr(f"Startup failed: {type(exc).__name__}. You can close this window safely.")
        )
    finally:
        if server is not None:
            server.should_exit = True
        if server_thread is not None:
            server_thread.join(timeout=15)
            if server_thread.is_alive():
                logger.error("desktop_api_shutdown_timeout", extra={"kind": "desktop"})
        if listener is not None:
            listener.close()


def main() -> int:
    _configure_environment()
    running, mutex = _acquire_instance_mutex()
    if not running:
        _focus_existing_window()
        return 0

    try:
        import webview

        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        bridge = DesktopBridge()
        window = webview.create_window(
            "Channel Press",
            html=_starting_page(),
            width=1440,
            height=940,
            min_size=(1024, 700),
            resizable=True,
            js_api=bridge,
        )
        bridge.window = window
        window.events.closing += bridge.on_closing
        backend_started = threading.Event()

        def start_backend_once() -> None:
            if backend_started.is_set():
                return
            backend_started.set()
            threading.Thread(
                target=_launch_backend,
                args=(window, bridge),
                name="channel-press-bootstrap",
                daemon=True,
            ).start()

        window.events.loaded += start_backend_once
        webview.start(gui="edgechromium", debug=False)
        bridge.allow_close = True
        if bridge.server is not None:
            bridge.server.should_exit = True
        return 0
    # Catch at the shell boundary so startup errors go to the private diagnostics log.
    except Exception:  # noqa: BLE001
        from channel_press.logging_setup import configure_logging, get_logger

        configure_logging("desktop", console=False)
        get_logger("desktop").exception("desktop_shell_failed", extra={"kind": "desktop"})
        return 1
    finally:
        if mutex is not None:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(mutex)


if __name__ == "__main__":
    raise SystemExit(main())
