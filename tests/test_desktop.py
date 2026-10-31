from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from channel_press import desktop, processes


def test_desktop_continue_hides_window_and_keeps_local_api_alive():
    hidden = threading.Event()

    class Window:
        def hide(self):
            hidden.set()

    class Server:
        should_exit = False

    bridge = desktop.DesktopBridge()
    bridge.window = Window()
    bridge.server = Server()

    result = bridge.shutdown("continue")

    assert result["closed"] is True
    assert bridge.finish_shutdown() == {"started": True}
    assert hidden.wait(2)
    assert bridge.server.should_exit is False
    assert bridge.allow_close is False
    assert bridge._shutdown_choice is None
    assert bridge._shutdown_started is False


def test_desktop_pause_stops_api_and_destroys_window(monkeypatch):
    from channel_press.ai import model_manager

    stopped = threading.Event()

    class Window:
        def destroy(self):
            stopped.set()

        def evaluate_js(self, _):
            raise AssertionError("A successful pause should not update the page.")

    class Server:
        should_exit = False

    bridge = desktop.DesktopBridge()
    bridge.window = Window()
    bridge.server = Server()
    monkeypatch.setattr(model_manager, "cancel_and_wait", lambda: True)
    monkeypatch.setattr(model_manager, "wait_for_smoke_test", lambda: True)
    monkeypatch.setattr("channel_press.processes.pause_owned_workers", lambda: True)

    result = bridge.shutdown("pause")

    assert result["closed"] is True
    assert bridge.finish_shutdown() == {"started": True}
    assert not stopped.is_set()
    assert stopped.wait(2)
    assert bridge.server.should_exit is True
    assert bridge.allow_close is True


def test_desktop_shutdown_requires_acknowledgement_before_finishing():
    bridge = desktop.DesktopBridge()

    assert bridge.shutdown("pause")["closed"] is True
    assert bridge.finish_shutdown() == {"started": True}
    assert bridge.finish_shutdown() == {"started": False}


def test_desktop_shutdown_rejects_duplicate_close_choices():
    bridge = desktop.DesktopBridge()

    assert bridge.shutdown("pause")["closed"] is True
    result = bridge.shutdown("continue")

    assert result == {"closed": False, "message": "A close action is already in progress."}


def test_desktop_close_callback_does_not_block_on_webview(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    returned = threading.Event()

    class Window:
        def evaluate_js(self, _):
            entered.set()
            release.wait(timeout=1)

    bridge = desktop.DesktopBridge()
    bridge.window = Window()
    threading.Timer(0.5, release.set).start()
    started = time.monotonic()

    def request_close():
        bridge.on_closing()
        returned.set()

    threading.Thread(target=request_close, daemon=True).start()

    assert returned.wait(timeout=0.2)
    assert time.monotonic() - started < 0.2
    assert entered.wait(timeout=1)
    assert release.wait(timeout=1)


def test_desktop_start_page_uses_packaged_newsroom_wordmark():
    page = desktop._starting_page()

    assert "<title>Newsroom</title>" in page
    assert 'alt="Newsroom"' in page
    assert "data:image/svg+xml;base64," in page
    assert "Channel Press" not in page


def test_windows_worker_launch_has_no_console():
    options = processes.hidden_process_options()
    if os.name == "nt":
        assert options["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert options["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW
        assert options["startupinfo"].wShowWindow == subprocess.SW_HIDE
    else:
        assert options == {}


def test_frozen_worker_uses_the_bundled_gui_executable(tmp_path, monkeypatch):
    app_executable = tmp_path / "Newsroom.exe"
    worker_executable = tmp_path / "NewsroomWorker.exe"
    app_executable.touch()
    worker_executable.touch()
    monkeypatch.setattr(processes.sys, "executable", str(app_executable))
    monkeypatch.setattr(processes.sys, "frozen", True, raising=False)

    assert processes.worker_command() == [str(worker_executable)]


def test_worker_launches_with_a_shared_utf8_log_path(tmp_path, monkeypatch):
    observed = {}

    class Process:
        pid = 918273

        @staticmethod
        def wait(timeout=None):
            return 0

        @staticmethod
        def poll():
            return 0

    def fake_popen(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return Process()

    monkeypatch.setattr(processes.settings, "data_dir", tmp_path)
    monkeypatch.setattr(processes, "worker_command", lambda: ["test-worker"])
    monkeypatch.setattr(processes.subprocess, "Popen", fake_popen)

    process, log_path = processes.launch_worker()

    assert process.pid == 918273
    assert observed["command"] == ["test-worker"]
    assert Path(observed["env"]["CHANNEL_PRESS_WORKER_LOG_PATH"]) == log_path
    assert observed["stdout"].name == str(log_path)
    assert observed["stderr"] == subprocess.STDOUT
    assert observed["shell"] is False
