"""Channel Press command line entry point.

All commands are local and safe by default. Nothing here contacts Telegram,
an online model or a remote host unless an owner explicitly configures and
invokes the relevant integration.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from .auth import (
    active_rights,
    create_first_owner,
    user_count,
)
from .bootstrap import (
    ensure_defaults,
    get_or_create_demo_channel,
    mark_setup_complete,
    setup_complete,
)
from .config import BASE_DIR, settings
from .db import SessionLocal
from .ingestion.adapters import load_json
from .ingestion.service import ensure_import_media_dir, import_payload, reclassify_posts
from .migrations import current_revision, upgrade_database
from .models import (
    Article,
    Channel,
    ChannelOwnership,
    Edition,
    EditionSchedule,
    Job,
    PostMedia,
    ReviewFlag,
    RightsGrant,
    SourceImport,
    SourcePost,
    StoryCluster,
    User,
)
from .pipeline import PipelineRequest, run_pipeline
from .publishing.service import edition_review_blockers
from .media.service import clear_media_cache, media_cache_summary
from .scheduling.service import (
    WorkerBusy,
    enqueue_manual_job,
    worker_once,
)
from .timeutil import as_utc, iso_utc, utcnow

FIXTURE_SETS = {
    "sample": ("telegram_export_sample.json", 26),
    "30": ("posts_30.json", 30),
    "70": ("posts_70.json", 70),
    "150": ("posts_150.json", 150),
}


def _print(message: str = "") -> None:
    print(message, flush=True)


def _parse_dt(value: str) -> datetime:
    from dateutil import parser as dt_parser

    parsed = dt_parser.isoparse(value) if "T" in value or value.endswith("Z") else dt_parser.parse(value)
    result = as_utc(parsed)
    if result is None:
        raise argparse.ArgumentTypeError(f"Invalid datetime: {value}")
    return result


def _db_path() -> Path:
    url = settings.effective_database_url
    if not url.startswith("sqlite"):
        raise RuntimeError("Backup/restore currently supports the local SQLite database only.")
    raw = url.split("///", 1)[-1]
    return Path(raw)


def _password(args: argparse.Namespace, prompt: str = "Password") -> str:
    value = getattr(args, "password", None) or os.environ.get("CHANNEL_PRESS_SETUP_PASSWORD")
    if value:
        return value
    first = getpass.getpass(f"{prompt}: ")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        raise SystemExit("Passwords do not match.")
    return first


def _get_channel(db, slug: str) -> Channel:
    channel = db.scalar(select(Channel).where(Channel.slug == slug))
    if channel is None:
        raise SystemExit(f"Unknown channel '{slug}'. Run 'channel-press channels' to list channels.")
    return channel


def _get_owner(db):
    return db.scalar(select(User).where(User.role == "owner").order_by(User.id.asc()))


def _window_from_posts(db, channel: Channel) -> tuple[datetime, datetime] | None:
    row = db.execute(
        select(func.min(SourcePost.posted_at), func.max(SourcePost.posted_at)).where(
            SourcePost.channel_id == channel.id, SourcePost.inclusion_status == "included"
        )
    ).one()
    start, end = row
    if start is None or end is None:
        return None
    start = as_utc(start).replace(hour=0, minute=0, second=0, microsecond=0)
    end_day = as_utc(end).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return start, end_day


def cmd_init_db(args: argparse.Namespace) -> int:
    settings.ensure_dirs()
    upgrade_database()
    _print(f"Database ready: {_db_path()}")
    _print(f"Revision: {current_revision()}")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    settings.ensure_dirs()
    upgrade_database()
    with SessionLocal() as db:
        ensure_defaults(db)
        if user_count(db) == 0:
            password = _password(args)
            user = create_first_owner(
                db, args.username, password, args.display_name or args.username
            )
            _print(f"Created owner '{user.username}'.")
        else:
            _print("An owner already exists; left credentials unchanged.")
        if args.demo_channel:
            owner = _get_owner(db)
            channel = get_or_create_demo_channel(db, owner)
            _print(f"Demo channel ready: {channel.slug}")
        mark_setup_complete(db)
        db.commit()
    _print("Setup complete. Start the server with: channel-press serve")
    return 0


def _import_fixture(db, channel: Channel, fixture: Path, actor_id: int | None) -> None:
    payload = load_json(fixture)
    ensure_import_media_dir(db, settings.media_dir)
    stats = import_payload(
        db,
        channel,
        payload,
        imported_by=actor_id,
        media_root=fixture.parent / "media",
        source_kind="fixture",
        original_name=fixture.name,
        rights_grant=active_rights(db, channel.id),
    )
    _print(
        f"Imported {stats.raw_count} messages: {stats.created} new, {stats.updated} updated, "
        f"{stats.rejected} excluded for policy, {stats.media} media files."
    )


def cmd_demo(args: argparse.Namespace) -> int:
    settings.ensure_dirs()
    upgrade_database()
    fixture_name, expected = FIXTURE_SETS[args.posts]
    fixture = BASE_DIR / "fixtures" / fixture_name
    if not fixture.exists():
        raise SystemExit(f"Fixture missing: {fixture}")
    generated_password = False
    with SessionLocal() as db:
        ensure_defaults(db)
        owner = _get_owner(db)
        if owner is None:
            password = getattr(args, "password", None) or os.environ.get("CHANNEL_PRESS_SETUP_PASSWORD")
            if not password:
                import secrets

                password = secrets.token_urlsafe(12)
                generated_password = True
            owner = create_first_owner(db, args.username, password, args.display_name or "Demo Editor")
            _print(f"Created owner '{owner.username}'.")
        channel = get_or_create_demo_channel(db, owner)
        if args.reset:
            _print("Resetting demo source and editorial records for the fixture channel...")
            from .models import (
                ArticleClaimLink,
                ArticleMedia,
                ArticleVersion,
                EditionArticle,
                EditionAsset,
                EditionPage,
                ExternalReference,
                Job,
                JobStep,
                ModelRun,
                PostFragment,
                PostVersion,
                PublicationAttempt,
                ReplyEdge,
                RetryAttempt,
                SourceAppendix,
                StoryClaim,
                StoryRelationship,
                StoryTimelineEvent,
            )

            article_ids = list(
                db.scalars(select(Article.id).join(StoryCluster, Article.story_id == StoryCluster.id).where(StoryCluster.channel_id == channel.id))
            )
            story_ids = list(db.scalars(select(StoryCluster.id).where(StoryCluster.channel_id == channel.id)))
            edition_ids = list(db.scalars(select(Edition.id).where(Edition.channel_id == channel.id)))
            post_ids = list(db.scalars(select(SourcePost.id).where(SourcePost.channel_id == channel.id)))
            if article_ids:
                for model in (ArticleClaimLink, ArticleMedia, ArticleVersion):
                    db.query(model).filter(model.article_id.in_(article_ids)).delete(synchronize_session=False)
            if story_ids:
                # Story-scoped flags and references are not always attached to an
                # article, so deleting by article_id alone leaves FK rows behind.
                db.query(ReviewFlag).filter(
                    (ReviewFlag.article_id.in_(article_ids)) | (ReviewFlag.story_id.in_(story_ids))
                ).delete(synchronize_session=False)
                for model in (StoryClaim, StoryRelationship, StoryTimelineEvent, ExternalReference):
                    db.query(model).filter(model.story_id.in_(story_ids)).delete(synchronize_session=False)
            output_dirs: list[Path] = []
            if edition_ids:
                edition_rows = list(db.scalars(select(Edition).where(Edition.id.in_(edition_ids))))
                output_dirs = [Path(row.output_dir) for row in edition_rows if row.output_dir]
                job_ids = list(db.scalars(select(Job.id).where(Job.channel_id == channel.id)))
                if job_ids:
                    for model in (JobStep, RetryAttempt):
                        db.query(model).filter(model.job_id.in_(job_ids)).delete(synchronize_session=False)
                    db.query(ModelRun).filter(ModelRun.job_id.in_(job_ids)).delete(synchronize_session=False)
                    db.query(Job).filter(Job.id.in_(job_ids)).delete(synchronize_session=False)
                for model in (EditionPage, EditionAsset, SourceAppendix, PublicationAttempt, EditionArticle):
                    db.query(model).filter(model.edition_id.in_(edition_ids)).delete(synchronize_session=False)
                db.query(Edition).filter(Edition.id.in_(edition_ids)).update(
                    {Edition.parent_edition_id: None}, synchronize_session=False
                )
                db.query(Edition).filter(Edition.id.in_(edition_ids)).delete(synchronize_session=False)
            if article_ids:
                db.query(Article).filter(Article.id.in_(article_ids)).delete(synchronize_session=False)
            if post_ids:
                # Fragments point at stories; remove them before the clusters.
                db.query(PostVersion).filter(PostVersion.post_id.in_(post_ids)).delete(synchronize_session=False)
                db.query(PostFragment).filter(PostFragment.post_id.in_(post_ids)).delete(synchronize_session=False)
                db.query(PostMedia).filter(PostMedia.post_id.in_(post_ids)).delete(synchronize_session=False)
                db.query(ReplyEdge).filter(
                    (ReplyEdge.from_post_id.in_(post_ids)) | (ReplyEdge.to_post_id.in_(post_ids))
                ).delete(synchronize_session=False)
            # Break the self-referential merge chain before deleting clusters.
            db.query(StoryCluster).filter(StoryCluster.channel_id == channel.id).update(
                {StoryCluster.merged_into_id: None}, synchronize_session=False
            )
            db.query(StoryCluster).filter(StoryCluster.channel_id == channel.id).delete(synchronize_session=False)
            db.query(SourcePost).filter(SourcePost.channel_id == channel.id).delete(synchronize_session=False)
            db.query(SourceImport).filter(SourceImport.channel_id == channel.id).delete(synchronize_session=False)
            db.commit()
            output_root = settings.output_dir.resolve()
            for directory in output_dirs:
                try:
                    resolved = directory.resolve()
                except OSError:
                    continue
                if resolved.is_relative_to(output_root) and resolved.exists():
                    shutil.rmtree(resolved, ignore_errors=True)
            # Bulk deletion leaves old Edition identities in the session map;
            # reload the entities before the new edition claims a recycled id.
            channel_id, owner_id = channel.id, owner.id
            db.expunge_all()
            channel = db.get(Channel, channel_id)
            owner = db.get(User, owner_id)

        _import_fixture(db, channel, fixture, owner.id)
        window = _window_from_posts(db, channel)
        if window is None:
            db.rollback()
            raise SystemExit("No includable fixture posts were imported.")
        start, end = window
        profile = ensure_defaults(db)
        edition_title = f"{profile.name} — Demonstration edition"
        request = PipelineRequest(
            channel=channel,
            window_start=args.window_start or start,
            window_end=args.window_end or end,
            cutoff_at=args.window_end or end,
            target_release_at=utcnow(),
            kind="manual",
            title=edition_title,
            actor_id=owner.id,
            publish=True,
            mark_published=True,
            demo_edition=True,
            progress=lambda stage, detail: _print(f"  [{stage}] {detail}"),
        )
        _print(f"Building demonstration edition from {expected} fixture messages...")
        started = time.monotonic()
        try:
            result = run_pipeline(db, request)
        except Exception:
            db.rollback()
            raise
        db.commit()
        pages = 0
        if result.publish_result:
            manifest = json.loads(result.edition.manifest_json or "{}")
            pages = manifest.get("pdf", {}).get("page_count", 0)
        _print("")
        _print("Demonstration edition built.")
        _print(f"  Edition:  #{result.edition.id} {result.edition.title}")
        _print(f"  Stories:  {len(result.stories)}   Articles: {len(result.articles)}")
        _print(f"  Review:   {result.review_count} item(s) need an editor decision")
        _print(f"  Provider: {result.provider} {result.provider_note}".rstrip())
        _print(f"  Duration: {time.monotonic() - started:.1f}s")
        if result.publish_result:
            _print(f"  Pages:    {pages}")
            _print(f"  PDF:      {result.publish_result.pdf_path}")
            _print(f"  E-paper:  {result.publish_result.epaper_dir / 'index.html'}")
            _print(f"  Manifest: {result.publish_result.manifest_path}")
        if generated_password:
            _print("")
            _print(f"Generated owner login: {owner.username} / {password}")
            _print("Change this password after signing in.")
        _print("")
        _print("Start the newsroom UI with: channel-press serve")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    settings.ensure_dirs()
    upgrade_database()
    source = Path(args.file).expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"Import file not found: {source}")
    with SessionLocal() as db:
        owner = _get_owner(db)
        if owner is None:
            raise SystemExit("Run 'channel-press setup' before importing content.")
        channel = db.scalar(select(Channel).where(Channel.slug == args.channel))
        if channel is None:
            if not args.create:
                raise SystemExit(
                    f"Unknown channel '{args.channel}'. Re-run with --create --title ... --grantor ... "
                    "--authority-basis ... to register an authorized channel."
                )
            if not args.grantor or not args.authority_basis:
                raise SystemExit(
                    "Creating a live channel requires --grantor and --authority-basis. Channel Press will not "
                    "process real content without a recorded rights declaration."
                )
            channel = Channel(
                slug=args.channel,
                title=args.title or args.channel,
                handle=args.handle or "",
                is_public=bool(args.public),
                is_demo=False,
            )
            db.add(channel)
            db.flush()
            db.add(
                ChannelOwnership(
                    channel_id=channel.id,
                    owner_user_id=owner.id,
                    granted_by_id=owner.id,
                    note="Created by owner during manual import.",
                )
            )
            db.add(
                RightsGrant(
                    channel_id=channel.id,
                    grantor_name=args.grantor,
                    authority_basis=args.authority_basis,
                    scope_json=json.dumps(["ingest", "ai_process", "publish"]),
                    evidence_ref=args.evidence_ref or "",
                    created_by=owner.id,
                    notes=args.rights_notes or "",
                )
            )
            db.flush()
            _print(f"Registered channel '{channel.slug}' with an active rights declaration.")
        elif not channel.is_demo and active_rights(db, channel.id) is None:
            raise SystemExit(
                "This channel has no active rights declaration. Record one in the UI (Sources → Rights) "
                "before importing."
            )
        payload = load_json(source)
        ensure_import_media_dir(db, settings.media_dir)
        media_root = Path(args.media_root).expanduser().resolve() if args.media_root else source.parent
        stats = import_payload(
            db,
            channel,
            payload,
            imported_by=owner.id,
            media_root=media_root,
            source_kind="json",
            original_name=source.name,
            rights_grant=active_rights(db, channel.id),
        )
        db.commit()
        _print(
            f"Import #{stats.import_id}: {stats.raw_count} raw, {stats.created} new, "
            f"{stats.updated} updated, {stats.rejected} excluded, {stats.media} media."
        )
    return 0


def cmd_classify_posts(args: argparse.Namespace) -> int:
    from .editorial.moderation import CLASSIFIER_VERSION

    settings.ensure_dirs()
    upgrade_database()
    with SessionLocal() as db:
        owner = _get_owner(db)
        channel = _get_channel(db, args.channel)
        stats = reclassify_posts(
            db,
            channel,
            start=args.window_start,
            end=args.window_end,
            force=args.force,
            actor_id=owner.id if owner else None,
        )
        db.commit()
        _print(
            f"Classifier {CLASSIFIER_VERSION}: {stats.scanned} stored posts, "
            f"{stats.reclassified} reclassified, {stats.current} already current, "
            f"{stats.out_of_scope} outside the requested window."
        )
        _print(
            f"  Included: {stats.included}; excluded: {stats.excluded}; "
            f"editor overrides preserved: {stats.overrides}; "
            f"repeated copies: {stats.duplicates}; fragments created: {stats.fragments_created}."
        )
        for label, count in sorted(stats.by_classification.items(), key=lambda item: (-item[1], item[0])):
            _print(f"  {label:<28} {count}")
    return 0


def cmd_run_now(args: argparse.Namespace) -> int:
    settings.ensure_dirs()
    upgrade_database()
    with SessionLocal() as db:
        owner = _get_owner(db)
        channel = _get_channel(db, args.channel)
        window = (
            (args.window_start, args.window_end)
            if args.window_start and args.window_end
            else _window_from_posts(db, channel)
        )
        if not window:
            raise SystemExit("No source posts exist for this channel; import content first.")
        start, end = window
        job = enqueue_manual_job(
            db,
            channel,
            window_start=start,
            window_end=end,
            actor_id=owner.id if owner else None,
        )
        db.commit()
        _print(f"Queued manual job #{job.id} for {start.isoformat()} – {end.isoformat()}.")
        stats = worker_once(db, max_jobs=1)
        db.commit()
        _print(
            f"Worker: {stats.processed} processed, {stats.published} published, "
            f"{stats.awaiting_review} awaiting review, {stats.failed} failed."
        )
        jobs = list(db.scalars(select(Job).order_by(Job.id.desc()).limit(3)))
        for item in jobs:
            _print(f"  Job #{item.id}: {item.status} ({item.stage}) {item.error[:300]}")
        if stats.failed:
            return 1
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    from .logging_setup import configure_logging

    settings.ensure_dirs()
    upgrade_database()
    configure_logging("worker", console=True)

    def show_progress(stage: str, detail: str) -> None:
        _print(f"[{utcnow().strftime('%H:%M:%S')}] {stage.replace('_', ' ')} — {detail}")

    try:
        if args.once:
            with SessionLocal() as db:
                stats = worker_once(db, progress=show_progress)
                db.commit()
                _print(
                    f"enqueued={stats.enqueued} processed={stats.processed} published={stats.published} "
                    f"awaiting_review={stats.awaiting_review} failed={stats.failed} released={stats.released} "
                    f"stale_locks={stats.stale_locks}"
                )
                return 0
        _print("Worker running. Press Ctrl+C to stop.")
        while True:
            with SessionLocal() as db:
                stats = worker_once(db, max_jobs=args.max_jobs, progress=show_progress)
                db.commit()
            if stats.processed or stats.enqueued or stats.released:
                _print(
                    f"{utcnow().isoformat()} processed={stats.processed} published={stats.published} "
                    f"awaiting_review={stats.awaiting_review} failed={stats.failed}"
                )
            time.sleep(args.interval)
    except WorkerBusy as exc:
        _print(str(exc))
        return 2
    except KeyboardInterrupt:
        _print("Worker stopped.")
        return 0
    finally:
        from .telegram.service import telegram_service
        telegram_service.close()


def cmd_media_cache(args: argparse.Namespace) -> int:
    settings.ensure_dirs()
    upgrade_database()
    with SessionLocal() as db:
        if args.finalize_edition:
            from .models import Edition
            from .publishing.service import finalize_media_cleanup

            edition = db.get(Edition, args.finalize_edition)
            if edition is None:
                raise SystemExit(f"No edition with id {args.finalize_edition}.")
            if edition.status not in {"published", "archived"}:
                raise SystemExit("Refusing cleanup: the edition is not published.")
            result = finalize_media_cleanup(db, edition)
            db.commit()
            _print(
                f"Edition {args.finalize_edition} media cleanup: "
                f"deleted={result['deleted']} files ({result['bytes']} bytes), "
                f"protected={result['protected']} pending={result['pending']} failed={result['failed']}"
            )
            return 1 if result["failed"] else 0
        # Determine which clear mode to use. The default is the safest mode.
        if args.clear_edition_working:
            mode = "edition_working"
            mode_name = "edition working media"
            include_derivatives = True
        elif args.clear_derivatives:
            mode = "derivatives"
            mode_name = "derivatives"
            include_derivatives = True
        elif args.clear_all_deletable:
            mode = "all"
            mode_name = "all deletable media"
            include_derivatives = not args.no_derivatives
        else:
            mode = "unused"
            mode_name = "unused cache"
            include_derivatives = not args.no_derivatives
        summary = media_cache_summary(db, include_derivatives=include_derivatives, mode=mode)
        _print(
            f"Clearing {mode_name}: {summary['files']} files, {summary['bytes']} bytes "
            f"(protected: {summary['protected_files']} files, {summary['protected_bytes']} bytes)"
        )
        _print(
            "Protected files belong to draft/review/running editions, editor locks, "
            "active jobs, recent ingestion retention or incomplete published output; "
            "provenance is never deleted."
        )
        _print(f"- {summary['protected_refs']} media record(s) are currently protected by active work or retention.")
        _print("- Media may need to be downloaded again after clearing.")
        if args.dry_run:
            return 0
        if not args.confirm:
            raise SystemExit("Refusing to delete media without --confirm.")
        from .auth import audit

        result = clear_media_cache(db, include_derivatives=include_derivatives, mode=mode)
        audit(
            db,
            None,
            "media.cache_clear",
            "media_cache",
            0,
            {"mode": mode, "deleted": result["deleted"], "bytes": result["bytes"],
             "skipped": result["skipped"], "failed": result["failed"], "source": "cli"},
        )
        db.commit()
        _print(
            f"Deleted {result['deleted']} files ({result['bytes']} bytes); "
            f"skipped={result['skipped']} failed={result['failed']}"
        )
        return 1 if result["failed"] else 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .logging_setup import configure_logging

    settings.ensure_dirs()
    upgrade_database()
    configure_logging("web")
    _print(f"Channel Press at http://{args.host}:{args.port}")
    if args.reload:
        _print("Note: --reload is ignored under managed service control; restart to apply changes.")
    from .operations import run_web

    run_web(uvicorn.Server(uvicorn.Config("channel_press.web.app:app", host=args.host, port=args.port)))
    return 0


def cmd_channels(args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        channels = list(db.scalars(select(Channel).order_by(Channel.id)))
        if not channels:
            _print("No channels. Run 'channel-press setup --demo-channel'.")
            return 0
        for channel in channels:
            rights = active_rights(db, channel.id)
            _print(
                f"{channel.id:>3}  {channel.slug:<24} demo={str(channel.is_demo):<5} "
                f"rights={'active' if rights else 'MISSING':<7} {channel.title}"
            )
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    settings.ensure_dirs()
    timestamp = utcnow().strftime("%Y%m%d-%H%M%S")
    target = Path(args.out).expanduser().resolve() if args.out else (
        settings.data_dir / "backups" / f"channel-press-{timestamp}.zip"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    db_path = _db_path()
    if not db_path.exists():
        raise SystemExit("No database exists yet; nothing to back up.")
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "channel_press.db"
        source = sqlite3.connect(str(db_path))
        dest = sqlite3.connect(str(snapshot))
        try:
            source.backup(dest)
        finally:
            dest.close()
            source.close()
        counts = {}
        conn = sqlite3.connect(str(snapshot))
        try:
            for table in ("source_posts", "story_clusters", "articles", "editions", "jobs"):
                try:
                    counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                except sqlite3.Error:
                    counts[table] = 0
        finally:
            # Windows will not let ZipFile read a SQLite file while a handle is
            # still open; a context manager alone only ends the transaction.
            conn.close()
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, "channel_press.db")
            for name in ("secret.key",):
                path = settings.data_dir / name
                if path.exists() and not args.without_secret:
                    archive.write(path, name)
            manifest = {
                "schema": "channel_press.backup.v1",
                "created_at": iso_utc(utcnow()),
                "app_version": "1.0.0",
                "database_revision": current_revision(),
                "counts": counts,
                "included_media": not args.without_media,
                "included_fonts": settings.fonts_dir.exists(),
                "included_secret": (settings.data_dir / "secret.key").exists() and not args.without_secret,
            }
            archive.writestr("backup-manifest.json", json.dumps(manifest, indent=2))
            if not args.without_media and settings.media_dir.exists():
                for path in settings.media_dir.rglob("*"):
                    if path.is_file():
                        archive.write(path, str(Path("media") / path.relative_to(settings.media_dir)))
            # Imported fonts are licensed assets referenced by theme revisions.
            # Losing them on restore would leave archived themes pointing at
            # missing files, so they travel with the database snapshot.
            if settings.fonts_dir.exists():
                for path in settings.fonts_dir.rglob("*"):
                    if path.is_file():
                        archive.write(
                            path, str(Path("fonts") / path.relative_to(settings.fonts_dir))
                        )
    _print(f"Backup written: {target} ({target.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    archive_path = Path(args.archive).expanduser().resolve()
    if not archive_path.exists():
        raise SystemExit(f"Backup not found: {archive_path}")
    if not args.force:
        raise SystemExit(
            "Restore replaces the local database and media. Stop the server/worker and re-run with --force."
        )
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        if "backup-manifest.json" not in names or "channel_press.db" not in names:
            raise SystemExit("Archive is not a Channel Press backup (manifest/database missing).")
        manifest = json.loads(archive.read("backup-manifest.json"))
        safety = settings.data_dir / "backups" / f"pre-restore-{utcnow().strftime('%Y%m%d-%H%M%S')}.zip"
        safety.parent.mkdir(parents=True, exist_ok=True)
        if _db_path().exists():
            backup_args = argparse.Namespace(out=str(safety), without_media=False, without_secret=True)
            cmd_backup(backup_args)
        tmp = settings.data_dir / f".restore-{uuid.uuid4().hex[:8]}"
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            archive.extractall(tmp)
            db_target = _db_path()
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(db_target) + suffix)
                if sidecar.exists():
                    sidecar.unlink()
            shutil.copy2(tmp / "channel_press.db", db_target)
            if "secret.key" in names:
                shutil.copy2(tmp / "secret.key", settings.data_dir / "secret.key")
            if (tmp / "media").exists():
                if settings.media_dir.exists():
                    shutil.rmtree(settings.media_dir)
                shutil.copytree(tmp / "media", settings.media_dir)
            if (tmp / "fonts").exists():
                if settings.fonts_dir.exists():
                    shutil.rmtree(settings.fonts_dir)
                shutil.copytree(tmp / "fonts", settings.fonts_dir)
            settings.ensure_dirs()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    _print(f"Restored {archive_path.name} (created {manifest.get('created_at')}).")
    _print(f"Safety backup: {safety}")
    return 0


def cmd_set_password(args: argparse.Namespace) -> int:
    from .security import hash_password

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == args.username.strip().lower()))
        if user is None:
            raise SystemExit(f"Unknown user '{args.username}'.")
        password = _password(args)
        user.password_hash = hash_password(password)
        user.must_change_password = False
        db.commit()
    _print(f"Password updated for '{args.username}'.")
    return 0


def cmd_diagnostics(args: argparse.Namespace) -> int:
    import importlib.metadata

    _print("Channel Press diagnostics")
    _print(f"  Time:      {iso_utc(utcnow())}")
    _print(f"  Platform:  {platform.platform()}")
    _print(f"  Python:    {sys.version.split()[0]} ({sys.executable})")
    _print(f"  Base dir:  {BASE_DIR}")
    _print(f"  Data dir:  {settings.data_dir}")
    _print(f"  Output:    {settings.output_dir}")
    for package in ("fastapi", "sqlalchemy", "playwright", "PyMuPDF", "pillow", "argon2-cffi"):
        try:
            _print(f"  {package:<12} {importlib.metadata.version(package)}")
        except importlib.metadata.PackageNotFoundError:
            _print(f"  {package:<12} MISSING")
    db_path = _db_path()
    _print(f"  Database:  {db_path}")
    if db_path.exists():
        _print(f"  DB size:   {db_path.stat().st_size / 1024:.0f} KB")
        try:
            _print(f"  Revision:  {current_revision()}")
        except Exception as exc:  # noqa: BLE001 - diagnostics must not crash
            _print(f"  Revision:  unavailable ({type(exc).__name__})")
    else:
        _print("  Database:  not created")
    with SessionLocal() as db:
        for label, model in (
            ("Users", User),
            ("Channels", Channel),
            ("Source posts", SourcePost),
            ("Stories", StoryCluster),
            ("Articles", Article),
            ("Editions", Edition),
            ("Jobs", Job),
            ("Open flags", ReviewFlag),
        ):
            try:
                query = select(func.count()).select_from(model)
                if model is ReviewFlag:
                    query = query.where(ReviewFlag.status == "open")
                _print(f"  {label:<12} {db.scalar(query) or 0}")
            except Exception:
                pass
        pending = db.scalar(
            select(func.count()).select_from(Job).where(
                Job.status.in_(["scheduled", "failed_retryable", "resumable"])
            )
        )
        _print(f"  Pending jobs {pending or 0}")
        setup = "yes" if setup_complete(db) else "no"
        _print(f"  Setup done: {setup}")
        schedule = db.scalar(select(EditionSchedule).limit(1))
        if schedule:
            _print(
                f"  Schedule:   {schedule.timezone} cutoff {schedule.cutoff_time}, release "
                f"{schedule.release_time}, enabled={schedule.enabled}"
            )
        _print(f"  Public URL: {settings.public_base_url or '(not configured)'}")
    chrome_candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)") ) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
    ]
    chrome = next((path for path in chrome_candidates if path.exists()), None)
    _print(f"  Chrome:    {chrome or 'NOT FOUND (Playwright PDF output will fail)'}")
    _print(f"  FFmpeg:    {shutil.which('ffmpeg') or 'not found (OpenCV fallback used for video frames)'}")
    try:
        import httpx

        with httpx.Client(timeout=2) as client:
            response = client.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
        _print(f"  Ollama:    reachable (HTTP {response.status_code}) at {settings.ollama_base_url}")
    except Exception as exc:  # noqa: BLE001
        _print(f"  Ollama:    unavailable ({type(exc).__name__}); deterministic composer will be used")
    try:
        import cv2

        _print(f"  OpenCV:    {cv2.__version__}")
    except Exception:
        _print("  OpenCV:    not installed (video still extraction unavailable)")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Validate a built edition's manifest and review state."""
    with SessionLocal() as db:
        edition = db.get(Edition, args.edition_id)
        if edition is None:
            raise SystemExit(f"Edition {args.edition_id} not found.")
        blockers = edition_review_blockers(db, edition.id)
        manifest = json.loads(edition.manifest_json or "{}")
        _print(f"Edition #{edition.id} {edition.title}")
        _print(f"  Status:  {edition.status}")
        _print(f"  PDF:     {edition.pdf_path or '(none)'}")
        _print(f"  E-paper: {edition.epaper_dir or '(none)'}")
        _print(f"  Pages:   {manifest.get('pdf', {}).get('page_count')}")
        _print(f"  QA:      {manifest.get('quality', {}).get('warnings', []) or 'no warnings'}")
        _print(f"  Blockers: {blockers or 'none'}")
        if blockers:
            return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="channel-press",
        description="Authorized source roundup to printable newspaper and offline e-paper.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-db", help="Create or upgrade the local SQLite database.")
    p.set_defaults(func=cmd_init_db)

    p = sub.add_parser("setup", help="Create the first owner and default publication profile.")
    p.add_argument("--username", default="owner")
    p.add_argument("--password", default=None, help="Prefer the interactive prompt or CHANNEL_PRESS_SETUP_PASSWORD.")
    p.add_argument("--display-name", default="")
    p.add_argument("--demo-channel", action="store_true", help="Also create the credential-free fixture channel.")
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("demo", help="Import fixtures and build a complete demonstration edition.")
    p.add_argument("--posts", choices=sorted(FIXTURE_SETS), default="sample")
    p.add_argument("--username", default="owner")
    p.add_argument("--password", default=None)
    p.add_argument("--display-name", default="Demo Editor")
    p.add_argument("--reset", action="store_true", help="Delete prior demo source/editorial records first.")
    p.add_argument("--window-start", type=_parse_dt, default=None)
    p.add_argument("--window-end", type=_parse_dt, default=None)
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("import", help="Import a rights-cleared Telegram Desktop/owner JSON export.")
    p.add_argument("--channel", required=True, help="Channel slug.")
    p.add_argument("--file", required=True)
    p.add_argument("--media-root", default=None)
    p.add_argument("--create", action="store_true", help="Create the channel when it does not exist.")
    p.add_argument("--title", default="")
    p.add_argument("--handle", default="")
    p.add_argument("--public", action="store_true", help="Channel has a public t.me address.")
    p.add_argument("--grantor", default="")
    p.add_argument("--authority-basis", default="")
    p.add_argument("--evidence-ref", default="")
    p.add_argument("--rights-notes", default="")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser(
        "classify-posts",
        help="Re-run deterministic moderation over stored posts (legacy backfill).",
    )
    p.add_argument("--channel", required=True, help="Channel slug.")
    p.add_argument("--window-start", type=_parse_dt, default=None)
    p.add_argument("--window-end", type=_parse_dt, default=None)
    p.add_argument(
        "--force",
        action="store_true",
        help="Reclassify posts already at the current classifier version.",
    )
    p.set_defaults(func=cmd_classify_posts)

    p = sub.add_parser("run-now", help="Queue and immediately process one edition job.")
    p.add_argument("--channel", required=True)
    p.add_argument("--window-start", type=_parse_dt, default=None)
    p.add_argument("--window-end", type=_parse_dt, default=None)
    p.set_defaults(func=cmd_run_now)

    p = sub.add_parser("worker", help="Run the short-lived background worker.")
    p.add_argument("--once", action="store_true")
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--max-jobs", type=int, default=5)
    p.set_defaults(func=cmd_worker)

    p = sub.add_parser("media-cache", help="Inspect or clear unused local media cache.")
    p.add_argument("--dry-run", action="store_true", help="Show deletable files and bytes without deleting.")
    p.add_argument("--confirm", action="store_true", help="Confirm deletion.")
    p.add_argument("--no-derivatives", action="store_true", help="Keep derivative files.")
    p.add_argument("--clear-unused", action="store_true", help="Clear only unused cache files (default safe option).")
    p.add_argument("--clear-edition-working", action="store_true", help="Clear edition working media only.")
    p.add_argument("--clear-derivatives", action="store_true", help="Clear derivative files only.")
    p.add_argument("--clear-all-deletable", action="store_true", help="Clear all deletable media (with active-job protection).")
    p.add_argument("--finalize-edition", type=int, default=0, help="Retry the post-publication media cleanup for one published edition id.")
    p.set_defaults(func=cmd_media_cache)

    p = sub.add_parser("serve", help="Start the local newsroom web UI.")
    p.add_argument("--host", default=settings.host)
    p.add_argument("--port", type=int, default=settings.port)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("channels", help="List channels and rights status.")
    p.set_defaults(func=cmd_channels)

    p = sub.add_parser("backup", help="Write a consistent SQLite + media backup archive.")
    p.add_argument("--out", default="")
    p.add_argument("--without-media", action="store_true")
    p.add_argument("--without-secret", action="store_true")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", help="Restore a backup archive. Stop the app first.")
    p.add_argument("--archive", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("set-password", help="Set an existing user's password.")
    p.add_argument("--username", required=True)
    p.add_argument("--password", default=None)
    p.set_defaults(func=cmd_set_password)

    p = sub.add_parser("diagnostics", help="Show local environment, database and integration status.")
    p.set_defaults(func=cmd_diagnostics)

    p = sub.add_parser("check-edition", help="Verify an edition's PDF manifest and review blockers.")
    p.add_argument("--edition-id", type=int, required=True)
    p.set_defaults(func=cmd_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI should print actionable errors, not tracebacks
        if os.environ.get("CHANNEL_PRESS_DEBUG"):
            raise
        _print(f"Error: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
