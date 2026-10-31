from __future__ import annotations

import base64
import ctypes
import html
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path


_ICON_HANDLES: list[int] = []


def _brand_asset_path(name: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return root / "assets" / "brand" / "newsroom" / name


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
<title>Newsroom</title>
<style>
body{margin:0;background:#F3F0E8;color:#20272B;font:16px/1.5 Segoe UI,sans-serif}
main{max-width:680px;margin:18vh auto;padding:32px 38px;border-top:4px solid #913E36;background:#fff}
.wordmark{display:block;width:190px;height:auto;margin-bottom:20px}
p{color:#5d594f}
</style><main><img class="wordmark" alt="Newsroom" src="__NEWSROOM_LOGO_DATA__"><p>Starting the local edition workspace. This window will continue when the workspace is ready.</p>
<p id="status">Checking local services…</p>
<div id="close" hidden><p>Choose what should happen to the current work before closing.</p>
<button onclick="requestCloseChoice('continue')">Continue work in background</button>
<button onclick="requestCloseChoice('pause')">Pause safely and exit</button>
<button onclick="document.getElementById('close').hidden=true">Keep Newsroom open</button></div>
<script>
window.addEventListener('channel-press-close-request',()=>document.getElementById('close').hidden=false);
async function requestCloseChoice(choice) {
  try {
    const result = await window.pywebview.api.shutdown(choice);
    if (result.closed) window.setTimeout(() => window.pywebview.api.finish_shutdown(), 0);
  } catch (_) {
    document.getElementById('status').textContent = 'The close action failed. Keep this window open and retry.';
  }
}
</script>
</main></html>"""


def _set_window_icon() -> None:
    if os.name != "nt":
        return
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadImageW.argtypes = (
        wintypes.HINSTANCE,
        wintypes.LPCWSTR,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    )
    user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.SendMessageW.argtypes = (
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )
    user32.SendMessageW.restype = ctypes.c_ssize_t
    icon_path = str(_brand_asset_path("newsroom.ico"))
    flags = 0x0010 | 0x0040
    large = user32.LoadImageW(None, icon_path, 1, 32, 32, flags)
    small = user32.LoadImageW(None, icon_path, 1, 16, 16, flags)
    if not large or not small:
        raise ctypes.WinError(ctypes.get_last_error())
    _ICON_HANDLES.extend((large, small))

    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    user32.EnumWindows.restype = wintypes.BOOL

    def set_icon(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        if title.value.casefold() == "newsroom":
            user32.SendMessageW(hwnd, 0x0080, 1, large)
            user32.SendMessageW(hwnd, 0x0080, 0, small)
            return False
        return True

    user32.EnumWindows(callback_type(set_icon), 0)


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
            if "newsroom" in title.value.casefold() or "channel press" in title.value.casefold():
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
        self.control_token = secrets.token_urlsafe(32)
        self._shutdown_choice: str | None = None
        self._shutdown_started = False
        self._shutdown_lock = threading.Lock()

    def on_closing(self):
        if self.allow_close:
            return True
        if self.window is not None:
            threading.Timer(0.05, self._show_close_choice).start()
        return False

    def _show_close_choice(self) -> None:
        if self.allow_close or self.window is None:
            return
        try:
            self.window.evaluate_js(
                "window.dispatchEvent(new Event('channel-press-close-request'))"
            )
        except Exception:
            from .logging_setup import get_logger

            get_logger("desktop").exception(
                "desktop_close_prompt_failed",
                extra={"kind": "desktop"},
            )

    def shutdown(self, choice: str) -> dict[str, str | bool]:
        if choice not in {"continue", "pause"}:
            return {"closed": False, "message": "Newsroom remains open."}
        with self._shutdown_lock:
            if self._shutdown_choice is not None:
                return {"closed": False, "message": "A close action is already in progress."}
            self._shutdown_choice = choice
        return {
            "closed": True,
            "message": (
                "The current worker will finish in the background."
                if choice == "continue"
                else "Newsroom is pausing at a safe checkpoint."
            ),
        }

    def finish_shutdown(self) -> dict[str, bool]:
        with self._shutdown_lock:
            choice = self._shutdown_choice
            if choice is None or self._shutdown_started:
                return {"started": False}
            self._shutdown_started = True
        threading.Timer(1, self._finish_shutdown, args=(choice,)).start()
        return {"started": True}

    def _finish_shutdown(self, choice: str) -> None:
        try:
            from channel_press.ai.model_manager import cancel_and_wait, wait_for_smoke_test
            from channel_press.processes import pause_owned_workers

            if choice == "continue":
                with self._shutdown_lock:
                    self._shutdown_choice = None
                    self._shutdown_started = False
                if self.window is not None:
                    self.window.hide()
                return
            if not cancel_and_wait() or not wait_for_smoke_test() or not pause_owned_workers():
                with self._shutdown_lock:
                    self._shutdown_choice = None
                    self._shutdown_started = False
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
        except Exception:
            from channel_press.logging_setup import get_logger

            get_logger("desktop").exception(
                "desktop_shutdown_failed",
                extra={"kind": "desktop"},
            )
            with self._shutdown_lock:
                self._shutdown_choice = None
                self._shutdown_started = False
            if self.window is not None:
                try:
                    self.window.evaluate_js(
                        "document.getElementById('desktop-close-status').textContent="
                        + repr("Shutdown failed. Keep Newsroom open and retry.")
                        + ";document.querySelectorAll('[data-desktop-close]').forEach((button)=>button.disabled=false)"
                    )
                except Exception:
                    get_logger("desktop").exception(
                        "desktop_shutdown_error_display_failed",
                        extra={"kind": "desktop"},
                    )


def _starting_page(log_path: Path | None = None) -> str:
    logo = base64.b64encode(_brand_asset_path("newsroom-logo-horizontal.svg").read_bytes()).decode("ascii")
    page = _START_PAGE.replace(
        "__NEWSROOM_LOGO_DATA__",
        f"data:image/svg+xml;base64,{logo}",
    ).replace(
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

        app.state.desktop_control_token = bridge.control_token
        app.state.desktop_bridge = bridge

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
            "Newsroom",
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
            _set_window_icon()
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
