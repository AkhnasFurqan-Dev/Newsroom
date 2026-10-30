from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from channel_press.config import BASE_DIR


def _upgrade(database: Path, data_dir: Path, revision: str) -> None:
    env = os.environ.copy()
    env.update(
        CHANNEL_PRESS_DATABASE_URL=f"sqlite:///{database.as_posix()}",
        CHANNEL_PRESS_DATA_DIR=str(data_dir),
        CHANNEL_PRESS_OUTPUT_DIR=str(data_dir / "output"),
        CHANNEL_PRESS_SECRET_KEY="migration-test-only-secret",
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=BASE_DIR,
        check=False,
        env=env,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_additive_provenance_migration_preserves_historical_model_run(tmp_path):
    database = tmp_path / "historical.db"
    data_dir = tmp_path / "data"
    _upgrade(database, data_dir, "b7f2c1d9a310")

    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO model_runs
                (task, provider, model, prompt_version, input_hash, output_hash,
                 valid, latency_ms, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "news_article",
                "ollama",
                "qwen2.5:3b-instruct",
                "news-article-v2",
                "a" * 64,
                "b" * 64,
                1,
                927,
                "",
                "2026-10-29T12:00:00+00:00",
            ),
        )
        connection.commit()

    _upgrade(database, data_dir, "head")

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT provider, model, prompt_version, input_hash, output_hash,
                   valid, latency_ms, status, model_digest, provider_version,
                   generation_settings_json, evidence_manifest_json
            FROM model_runs
            WHERE id = 1
            """
        ).fetchone()
        article_fk = connection.execute("PRAGMA foreign_key_list(model_runs)").fetchall()

    assert row == (
        "ollama",
        "qwen2.5:3b-instruct",
        "news-article-v2",
        "a" * 64,
        "b" * 64,
        1,
        927,
        "historical",
        "",
        "",
        "{}",
        "{}",
    )
    assert any(
        foreign_key[2] == "articles"
        and foreign_key[3] == "article_id"
        and foreign_key[6] == "SET NULL"
        for foreign_key in article_fk
    )
