"""CLI backup regression: the SQLite snapshot must be closed before zipping."""

from __future__ import annotations

import argparse
import json
import zipfile

import channel_press.cli as cli


def test_backup_archive_is_written_and_readable(db, tmp_path):
    target = tmp_path / "backup.zip"
    args = argparse.Namespace(
        out=str(target),
        without_media=True,
        without_secret=True,
    )

    assert cli.cmd_backup(args) == 0
    assert target.exists()

    with zipfile.ZipFile(target) as archive:
        names = set(archive.namelist())
        assert "channel_press.db" in names
        assert "backup-manifest.json" in names
        assert "secret.key" not in names
        assert len(archive.read("channel_press.db")) > 0
        manifest = json.loads(archive.read("backup-manifest.json"))
        assert manifest["included_media"] is False
        assert manifest["included_secret"] is False
        assert "counts" in manifest
