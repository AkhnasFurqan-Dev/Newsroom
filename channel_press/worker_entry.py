from __future__ import annotations

import os
import sys
from argparse import Namespace
from datetime import datetime
from pathlib import Path


def main() -> int:
    os.environ["CHANNEL_PRESS_HEADLESS_WORKER"] = "1"
    from channel_press.config import settings

    settings.ensure_dirs()
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = Path(
        os.environ.get(
            "CHANNEL_PRESS_WORKER_LOG_PATH",
            str(log_dir / f"worker-{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}.log"),
        )
    )
    output = log_path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = output
    sys.stderr = output
    from channel_press.cli import cmd_worker

    try:
        return cmd_worker(Namespace(once=True, max_jobs=10, interval=60))
    finally:
        output.flush()
        output.close()


if __name__ == "__main__":
    raise SystemExit(main())
