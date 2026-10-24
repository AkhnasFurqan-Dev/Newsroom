"""Disposable collector harness: owns one job + window lease, commits checkpoints.

This mimics the worker's real DB pattern (claim -> commit -> network wait ->
bounded checkpoint commits) so a second process can prove that the web app and
dashboard mutations stay responsive while a collection is active.
"""
import json
import os
import socket
import sys
import time
from datetime import timedelta

from sqlalchemy import select

from channel_press.collection.service import claim_window, get_or_create_window, save_checkpoint
from channel_press.collection.window import EditionWindow
from channel_press.db import SessionLocal
from channel_press.models import Channel, EditionWindowRecord, Job
from channel_press.operations import StopRequested, check_job_control
from channel_press.scheduling.service import claim_job, enqueue_collect_job
from channel_press.timeutil import utcnow

PAGES = int(os.environ.get("HOLDER_PAGES", "10"))
DELAY = float(os.environ.get("HOLDER_DELAY", "2"))


def main() -> int:
    with SessionLocal() as db:
        channel = db.scalar(select(Channel).where(Channel.is_demo.is_(True)))
        end = utcnow() - timedelta(minutes=5)
        window = EditionWindow(
            channel_id=channel.id,
            kind="manual",
            start=end - timedelta(days=1),
            end=end,
            timezone="Asia/Karachi",
            cutoff_local="05:00",
        )
        queued = enqueue_collect_job(db, channel, window=window, mode="sync_window")
        db.commit()
        worker_id = f"{socket.gethostname()}:{os.getpid()}:holder"
        job = claim_job(db, worker_id=worker_id)
        db.commit()
        record = get_or_create_window(db, window, job_id=queued.id)
        assert claim_window(db, record, owner=worker_id), "holder could not claim the window"
        db.commit()
        print(json.dumps({"job_id": job.id, "window": window.key}), flush=True)

        for page in range(1, PAGES + 1):
            time.sleep(DELAY)
            with SessionLocal() as side:
                save_checkpoint(
                    side, window, adapter="holder", page=page, oldest_id=None,
                    fetched=page * 10, status="in_progress",
                )
                side.commit()
            try:
                check_job_control(job.id)
            except StopRequested as exc:
                with SessionLocal() as side:
                    row = side.get(Job, job.id)
                    row.status = "canceled"
                    row.stage = "canceled"
                    row.error = str(exc)
                    row.lock_owner = ""
                    row.lock_expires_at = None
                    row.finished_at = utcnow()
                    window_row = side.scalar(
                        select(EditionWindowRecord).where(EditionWindowRecord.window_key == window.key)
                    )
                    window_row.lock_owner = ""
                    window_row.lock_expires_at = None
                    window_row.status = "failed"
                    side.commit()
                print(json.dumps({"canceled_at_page": page}), flush=True)
                return 0
        with SessionLocal() as side:
            row = side.get(Job, job.id)
            row.status = "completed"
            row.stage = "done"
            row.lock_owner = ""
            row.lock_expires_at = None
            row.finished_at = utcnow()
            window_row = side.scalar(
                select(EditionWindowRecord).where(EditionWindowRecord.window_key == window.key)
            )
            window_row.lock_owner = ""
            window_row.lock_expires_at = None
            window_row.status = "collected"
            window_row.stats_json = json.dumps({"complete": True, "fetched": PAGES * 10})
            side.commit()
        print(json.dumps({"completed": True}), flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
