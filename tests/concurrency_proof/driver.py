"""Disposable end-to-end concurrency proof: web process + worker + dashboard.

Never touches the owner's real database, session or output tree.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DATA = ROOT / "data" / "concurrency-proof"
DB = DATA / "proof.db"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"
ENV = {
    **os.environ,
    "CHANNEL_PRESS_HOST": "127.0.0.1",
    "CHANNEL_PRESS_PORT": str(PORT),
    "CHANNEL_PRESS_DATA_DIR": str(DATA),
    "CHANNEL_PRESS_OUTPUT_DIR": str(ROOT / "output" / "concurrency-proof"),
    "CHANNEL_PRESS_DATABASE_URL": f"sqlite:///{DB.as_posix()}",
    "CHANNEL_PRESS_SETUP_PASSWORD": "ProofOnly-Passw0rd",
    "PYTHONPATH": str(ROOT),
}
ENV.pop("CHANNEL_PRESS_SYNC_JOBS", None)
LOG = DATA / "logs"
LOG.mkdir(exist_ok=True)
REPORT = {}


def db(sql, *args):
    with sqlite3.connect(DB) as con:
        return con.execute(sql, args).fetchall()


def start(args, log_name, capture=False, interactive=False):
    handle = (LOG / log_name).open("ab")
    return subprocess.Popen(
        [str(PY), *args], cwd=str(ROOT), env=ENV,
        stdout=subprocess.PIPE if capture else handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE if interactive else subprocess.DEVNULL,
    )


def wait_http(timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{BASE}/login", timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def csrf_from(html):
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert match, "csrf token missing"
    return match.group(1)


def timed(client, method, url, **kwargs):
    began = time.monotonic()
    response = client.request(method, url, **kwargs)
    return response, round(time.monotonic() - began, 3)


def main():
    subprocess.run([str(PY), "-m", "channel_press.operations", "resume"], cwd=str(ROOT),
                   env=ENV, capture_output=True, text=True, timeout=60)
    REPORT["before"] = {
        "jobs": db("select count(*) from jobs")[0][0],
        "editions": db("select count(*) from editions")[0][0],
        "audit": db("select count(*) from audit_events")[0][0],
        "posts": db("select count(*) from source_posts")[0][0],
    }
    web = start(["-m", "channel_press.cli", "serve", "--host", "127.0.0.1",
                 "--port", str(PORT)], "web.stdout")
    try:
        assert wait_http(), "web did not start"
        REPORT["web_started"] = True
        client = httpx.Client(base_url=BASE, follow_redirects=False, timeout=15)
        login = client.post("/login", data={"username": "owner", "password": "ProofOnly-Passw0rd"})
        assert login.status_code == 303, login.text
        html = client.get("/schedules").text
        csrf = csrf_from(html)

        reads = {}
        for url in ["/", "/sources/1", "/jobs", "/schedules"]:
            response, elapsed = timed(client, "GET", url)
            reads[url] = {"status": response.status_code, "seconds": elapsed}
        REPORT["idle_reads"] = reads

        build = {"csrf_token": csrf, "channel_id": "1", "window_start": "2026-10-20T00:00",
                 "window_end": "2026-10-21T00:00", "kind": "manual", "title": "",
                 "publish": "preview", "collect": ""}
        first, first_s = timed(client, "POST", "/build", data=build)
        assert first.status_code == 303, first.text
        job_id = int(re.search(r"/jobs/(\d+)", first.headers["location"]).group(1))
        second, second_s = timed(client, "POST", "/build", data=build)
        job_id_2 = int(re.search(r"/jobs/(\d+)", second.headers["location"]).group(1))
        REPORT["duplicate_build"] = {
            "first_job": job_id, "second_job": job_id_2, "same": job_id == job_id_2,
            "seconds": [first_s, second_s],
            "jobs_for_window": db("select count(*) from jobs where key like 'manual:1:manual:%'")[0][0],
        }
        assert job_id == job_id_2

        # Hammer the dashboard while the detached worker builds the edition.
        performed, worst, seen = [], 0.0, set()
        deadline = time.monotonic() + 20
        index = 0
        while time.monotonic() < deadline:
            index += 1
            request = [
                ("GET", "/jobs", None),
                ("GET", f"/api/jobs/{job_id}", None),
                ("GET", "/sources/1", None),
                ("POST", "/schedules/1", {"csrf_token": csrf, "cutoff_time": "05:00",
                                          "release_time": "08:00", "timezone": "Asia/Karachi",
                                          "weekdays": ["0", "1", "2", "3", "4", "5", "6"],
                                          "requires_review": "1"}),
                ("POST", "/branding", {"csrf_token": csrf, "name": f"Concurrent Desk {index}",
                                       "timezone": "Asia/Karachi", "accent": "#7b1f24",
                                       "paper_size": "tabloid", "map_profile": "neutral",
                                       "qr_strategy": "none"}),
            ][index % 5]
            method, url, payload = request
            response, elapsed = timed(client, method, url, **({"data": payload} if payload else {}))
            worst = max(worst, elapsed)
            if url.startswith("/api/jobs/"):
                try:
                    seen.add(response.json().get("status"))
                except ValueError:
                    pass
            performed.append({"method": method, "url": url, "status": response.status_code,
                              "seconds": elapsed})
            if response.status_code >= 500:
                REPORT["hammer_failure"] = performed[-1]
                break
            time.sleep(0.15)
        REPORT["hammer"] = {
            "requests": len(performed),
            "worst_seconds": worst,
            "statuses": sorted({item["status"] for item in performed}),
            "job_statuses_seen": sorted(item for item in seen if item),
            "worker_timeline": db("select started_at, finished_at from jobs where id=?", job_id)[0],
            "server_errors": [item for item in performed if item["status"] >= 500][:5],
        }
        assert all(item["status"] < 500 for item in performed), "dashboard returned 500"
        assert worst < 5, f"dashboard request took {worst}s"

        status = None
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status = client.get(f"/api/jobs/{job_id}").json()
            if status["status"] in {"awaiting_review", "published", "completed", "no_content",
                                    "failed_terminal", "failed_retryable", "paused"}:
                break
            time.sleep(1)
        REPORT["worker_job"] = {"id": job_id, "status": status["status"],
                                "stage": status["stage"], "edition_id": status["edition_id"],
                                "error": (status.get("error") or "")[:200]}
        assert status["status"] in {"awaiting_review", "published", "no_content"}
        if status["edition_id"]:
            row = db("select output_dir, manifest_json from editions where id=?",
                     status["edition_id"])[0]
            manifest = json.loads(row[1] or "{}")
            REPORT["edition"] = {
                "id": status["edition_id"], "output_dir": row[0],
                "pages": manifest.get("pdf", {}).get("page_count"),
            }

        # Holder process: an active collection lease while the desk keeps working.
        holder = start([str(HERE / "holder.py")], "holder.stdout", capture=True)
        first_line = holder.stdout.readline().decode().strip()
        REPORT["holder"] = {"start": first_line}
        holder_job = json.loads(first_line)["job_id"]
        busy = []
        for method, url, payload in [
            ("GET", "/sources/1", None),
            ("POST", "/schedules/1", {"csrf_token": csrf, "cutoff_time": "05:30",
                                      "release_time": "08:30", "timezone": "Asia/Karachi",
                                      "weekdays": ["0", "1", "2", "3", "4", "5", "6"],
                                      "requires_review": "1"}),
            ("POST", "/branding", {"csrf_token": csrf, "name": "Live Collection Desk",
                                   "timezone": "Asia/Karachi", "accent": "#7b1f24",
                                   "paper_size": "tabloid", "map_profile": "neutral",
                                   "qr_strategy": "none"}),
            ("GET", "/jobs", None),
        ]:
            response, elapsed = timed(client, method, url, **({"data": payload} if payload else {}))
            busy.append({"method": method, "url": url, "status": response.status_code,
                         "seconds": elapsed})
        cancel, cancel_s = timed(client, "POST", f"/jobs/{holder_job}/cancel",
                                 data={"csrf_token": csrf})
        busy.append({"method": "POST", "url": f"/jobs/{holder_job}/cancel",
                     "status": cancel.status_code, "seconds": cancel_s})
        remaining = holder.communicate(timeout=60)[0].decode()
        REPORT["holder"]["requests"] = busy
        REPORT["holder"]["output"] = remaining.strip()
        REPORT["holder"]["exit"] = holder.returncode
        REPORT["holder"]["final_job"] = db("select status, lock_owner from jobs where id=?",
                                           holder_job)[0]
        REPORT["holder"]["window_lock"] = db(
            "select status, lock_owner from edition_windows where id=(select max(id) from edition_windows)"
        )[0]
        assert all(item["status"] < 500 for item in busy)
        assert holder.returncode == 0
        assert REPORT["holder"]["final_job"][0] == "canceled"

        # Cross-process worker gate.
        lease = start([str(HERE / "lease_holder.py"), str(DATA / "worker.lock")],
                      "lease.stdout", capture=True, interactive=True)
        assert lease.stdout.readline().decode().strip() == "ready"
        try:
            blocked = subprocess.run([str(PY), "-m", "channel_press.cli", "worker", "--once"],
                                     cwd=str(ROOT), env=ENV, capture_output=True, text=True,
                                     timeout=120)
            REPORT["second_worker"] = {"exit": blocked.returncode,
                                       "stdout": blocked.stdout.strip()[:200]}
            assert blocked.returncode == 2 and "already running" in blocked.stdout.lower()
        finally:
            lease.communicate(b"stop\n", timeout=30)

        # Cross-process Telegram session ownership.
        session_lock = str(DATA / "telegram.session") + ".owner.lock"
        owner = start([str(HERE / "lease_holder.py"), session_lock],
                      "session.stdout", capture=True, interactive=True)
        assert owner.stdout.readline().decode().strip() == "ready"
        try:
            held = subprocess.run([str(PY), str(HERE / "session_probe.py")], cwd=str(ROOT),
                                  env=ENV, capture_output=True, text=True, timeout=60)
            REPORT["session_while_owned"] = json.loads(held.stdout.strip().splitlines()[-1])
        finally:
            owner.communicate(b"stop\n", timeout=30)
        free = subprocess.run([str(PY), str(HERE / "session_probe.py")], cwd=str(ROOT),
                              env=ENV, capture_output=True, text=True, timeout=60)
        REPORT["session_after_release"] = json.loads(free.stdout.strip().splitlines()[-1])
        assert "busy" in REPORT["session_while_owned"]["detail"].lower()
        assert REPORT["session_after_release"]["status"] == "ready"

        # Graceful stop of managed services.
        stop = subprocess.run([str(PY), "-m", "channel_press.operations", "stop"], cwd=str(ROOT),
                              env=ENV, capture_output=True, text=True, timeout=60)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and web.poll() is None:
            time.sleep(0.5)
        owners = json.loads(subprocess.run(
            [str(PY), "-m", "channel_press.operations", "owners"], cwd=str(ROOT), env=ENV,
            capture_output=True, text=True, timeout=60).stdout or "{}")
        REPORT["stop"] = {"request_exit": stop.returncode, "web_exit": web.poll(),
                          "owners": owners}
        assert web.poll() is not None, "web did not stop gracefully"
        assert owners.get("count", 0) == 0

        # Controlled restart with the documented script.
        restart = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                  "-File", str(ROOT / "scripts" / "restart-services.ps1")],
                                 cwd=str(ROOT), env=ENV, capture_output=True, text=True,
                                 timeout=240)
        REPORT["restart"] = {"exit": restart.returncode,
                             "tail": restart.stdout.strip().splitlines()[-6:]}
        assert restart.returncode == 0, restart.stdout + restart.stderr
        assert wait_http(timeout=60), "web did not answer after restart"
        REPORT["restart_http"] = True
        subprocess.run([str(PY), "-m", "channel_press.operations", "stop"], cwd=str(ROOT),
                       env=ENV, capture_output=True, text=True, timeout=60)
        time.sleep(5)
    finally:
        if web.poll() is None:
            subprocess.run([str(PY), "-m", "channel_press.operations", "stop"], cwd=str(ROOT),
                           env=ENV, capture_output=True, text=True, timeout=60)
            try:
                web.wait(timeout=60)
            except subprocess.TimeoutExpired:
                web.terminate()
        REPORT["after"] = {
            "jobs": db("select count(*) from jobs")[0][0],
            "editions": db("select count(*) from editions")[0][0],
            "audit": db("select count(*) from audit_events")[0][0],
            "posts": db("select count(*) from source_posts")[0][0],
            "integrity": db("pragma integrity_check")[0][0],
            "duplicate_editions": db(
                "select count(*) from (select window_start, window_end, count(*) c "
                "from editions group by 1,2 having c > 1)"
            )[0][0],
        }
        print(json.dumps(REPORT, indent=2))


if __name__ == "__main__":
    sys.exit(main())
