"""Local newsroom web application.

The UI is server-rendered with Jinja and plain HTML forms so it works offline,
without a JavaScript build chain. Every mutating route enforces a session, a
role threshold and CSRF. Builds are persistent jobs processed by the detached
worker, so closing the browser does not cancel an edition.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.middleware.gzip import GZipMiddleware

from ..auth import (
    SESSION_COOKIE,
    active_rights,
    audit,
    authenticate,
    can_access_channel,
    channel_role,
    clear_session_cookie,
    create_first_owner,
    create_login_session,
    current_user,
    get_session_user,
    require_csrf,
    require_role,
    revoke_session,
    set_session_cookie,
    user_count,
)
from ..bootstrap import (
    ensure_defaults,
    get_or_create_demo_channel,
    mark_setup_complete,
    setup_complete,
)
from ..config import BASE_DIR, settings
from ..constants import (
    EDITOR_OVERRIDE_CHOICES,
    EXCLUSION_REASONS,
    ROLE_RANK,
    SECTION_LABELS,
    SECTION_ORDER,
    SECTIONS,
)
from ..collection.service import (
    EditionWindow,
    checkpoint_for,
    commit_edition_window,
    preview_window,
    window_summary,
)
from ..collection.window import local_cutoff_utc, parse_clock, window_for_cutoff
from ..db import SessionLocal, session_dependency
from ..logging_setup import configure_logging, get_logger
from ..editorial.audit import publish_gate
from ..editorial.drafting import reaudit_article, save_editor_edit
from ..editorial.revisions import (
    BLOCKING_STATUSES,
    EDITORIAL_STATUSES,
    STATUS_LABELS,
    RevisionConflict,
    article_media,
    article_snapshot,
    edition_content_fingerprint,
    edition_versions,
    preview_matches_content,
    reorder_article,
    revision_history,
    save_article_revision,
    set_article_status,
    snapshot_hash,
    working_revision,
)
from ..ingestion.adapters import ImportFormatError, normalize_messages
from ..ingestion.service import ensure_import_media_dir, import_payload
from ..migrations import upgrade_database
from ..preflight import build_preflight
from ..publications import (
    PUBLICATION_COOKIE,
    accessible_publications,
    can_access_publication,
    channel_is_unassigned,
    edition_source_channels,
    publication_channels,
    publication_for_channel,
    publication_for_edition,
    resolve_publication,
    unassigned_channels,
    unique_slug,
)
from ..models import (
    Article,
    ArticleMedia,
    ArticleSource,
    AuditEvent,
    CartographicProfile,
    Channel,
    ChannelOwnership,
    Edition,
    EditionArticle,
    EditionPage,
    EditionSchedule,
    ExternalReference,
    FontAsset,
    Job,
    JobStep,
    PostFragment,
    PostMedia,
    PublicationEditor,
    PublicationProfile,
    PublicationSource,
    ReviewFlag,
    RightsGrant,
    SourceImport,
    SourcePost,
    StoryClaim,
    StoryCluster,
    StoryTimelineEvent,
    ThemeRevision,
    User,
)
from ..publishing.service import (
    edition_review_blockers,
    publish_edition,
    purge_unpublished_output,
    render_preview,
)
from ..media.service import (
    apply_media_choice,
    clear_article_media,
    clear_media_cache,
    media_cache_summary,
    rank_media,
    update_media_caption,
)
from ..research import (
    ResearchError,
    accept_reference,
    discover_candidates,
    provider_status,
    reject_reference,
    research_reference,
)
from ..research.service import normalise_url
from ..fonts import (
    FontError,
    MAX_FONT_BYTES,
    catalog as font_catalog,
    import_font,
)
from ..theming import (
    COLOR_FIELDS,
    FONT_ROLES,
    METRIC_LIMITS,
    PRESETS,
    ThemeError,
    available_font_assets,
    contrast_warnings,
    current_theme,
    default_payload,
    describe_theme,
    fonts_used_by_themes,
    normalise_payload,
    payload_hash,
    preset as theme_preset,
    save_theme,
    theme_payload,
)
from ..editorial.moderation import (
    CLASSIFICATION_LABELS,
    CLASSIFIER_VERSION,
    EDITOR_OVERRIDES,
)
from ..scheduling.service import (
    DuplicateWindowError,
    WorkerBusy,
    enqueue_collect_job,
    enqueue_due_jobs,
    enqueue_manual_job,
    latest_due_cutoff,
    progress_snapshot,
    worker_once,
)
from ..security import hash_password, redact, verify_password
from ..telegram.service import telegram_service
from ..telegram.transport import TelegramTransportError, normalize_handle
from ..timeutil import as_utc, human_local, iso_utc, local_boundary_utc, to_local, utcnow

WEB_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

configure_logging("web")
logger = get_logger("web")

app = FastAPI(title="Channel Press", docs_url=None, redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1200)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def _startup() -> None:
    settings.ensure_dirs()
    upgrade_database()


@app.on_event("shutdown")
def _shutdown() -> None:
    telegram_service.close()


from sqlalchemy.exc import OperationalError  # noqa: E402


@app.exception_handler(OperationalError)
async def database_busy_handler(request: Request, exc: OperationalError):
    from ..db import is_busy
    if is_busy(exc):
        return HTMLResponse(
            "Database temporarily busy. Your change was not saved. Retry shortly.",
            status_code=503, headers={"Retry-After": "2"},
        )
    logger.error("database_error", exc_info=(type(exc), exc, exc.__traceback__))
    return HTMLResponse("Database operation failed. Consult the private web log.", status_code=500)


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )

    def localtime(value, tz: str = "Asia/Karachi", fmt: str = "%d %b %Y, %H:%M"):
        return human_local(value, tz, fmt)

    def shortdate(value, tz: str = "Asia/Karachi"):
        return human_local(value, tz, "%d %b %Y")

    def status_label(value: str) -> str:
        return (value or "").replace("_", " ").replace("-", " ").title()

    def json_loads(value, default=None):
        try:
            return json.loads(value or "")
        except (TypeError, ValueError):
            return default if default is not None else {}

    env.filters["localtime"] = localtime
    env.filters["shortdate"] = shortdate
    env.filters["status_label"] = status_label
    env.filters["json_loads"] = json_loads
    return env


_ENV = _env()
_TEMPLATES: dict[str, object] = {}


def render(request: Request, name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    if not name.endswith(".j2"):
        name = f"{name}.j2"
    template = _ENV.get_template(name)
    base = {
        "request": request,
        "app_name": settings.app_name,
        "message": request.query_params.get("msg", ""),
        "error": request.query_params.get("err", ""),
        "today": utcnow(),
        "sections": SECTIONS,
        "section_labels": SECTION_LABELS,
        "role_rank": ROLE_RANK,
    }
    base.update(context)
    return HTMLResponse(template.render(**base), status_code=status_code)


def _redirect(url: str, message: str = "", error: str = "") -> RedirectResponse:
    from urllib.parse import urlencode

    params = {}
    if message:
        params["msg"] = message
    if error:
        params["err"] = error
    if params:
        url = f"{url}{'&' if '?' in url else '?'}{urlencode(params)}"
    return RedirectResponse(url, status_code=303)


def _ctx(db: Session, request: Request, user: User, active: str, **extra) -> dict:
    profile = extra.pop("profile", None) or resolve_publication(db, request, user)
    login = getattr(request.state, "login_session", None)
    return {
        "user": user,
        "profile": profile,
        "publication": profile,
        "publications": accessible_publications(db, user),
        "csrf": login.csrf_token if login else "",
        "active": active,
        "can": {
            "operate": ROLE_RANK.get(user.role, 0) >= ROLE_RANK["operator"],
            "edit": ROLE_RANK.get(user.role, 0) >= ROLE_RANK["editor"],
            "own": user.role == "owner",
        },
        **extra,
    }


def _publication_or_404(
    db: Session, publication_id: int, user: User, minimum: str = "viewer"
) -> PublicationProfile:
    publication = db.get(PublicationProfile, publication_id)
    if publication is None:
        raise HTTPException(404, "Publication not found.")
    if not can_access_publication(db, user, publication, minimum):
        raise HTTPException(403, "You do not have access to this publication.")
    return publication


def _scope_channels(
    db: Session,
    publication: PublicationProfile | None,
    user: User,
    minimum: str = "viewer",
) -> list[Channel]:
    """Channels that belong to the selected newspaper *and* are visible to the user."""
    if publication is None:
        return []
    return [
        channel
        for channel in publication_channels(db, publication, enabled_only=False)
        if can_access_channel(db, user, channel, minimum)
    ]


def _timezone(profile: PublicationProfile | None) -> str:
    return profile.timezone if profile else settings.timezone


def _parse_local(value: str, tz_name: str) -> datetime | None:
    if not value:
        return None
    value = value.replace("T", " ").strip()
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M" if " " in value else "%Y-%m-%d")
    except ValueError:
        return None
    return local_boundary_utc(parsed.date(), parsed.time(), tz_name)


def _latest_cutoff(tz_name: str, cutoff_clock: str, now: datetime | None = None) -> datetime:
    """Most recent local cutoff instant at or before ``now``."""
    now = now or utcnow()
    clock = parse_clock(cutoff_clock)
    local_today = to_local(now, tz_name).date()
    cutoff = local_cutoff_utc(local_today, clock, tz_name)
    if cutoff > now:
        cutoff = local_cutoff_utc(local_today - timedelta(days=1), clock, tz_name)
    return cutoff


def _cutoff_window(
    channel: Channel,
    tz_name: str,
    cutoff_clock: str,
    kind: str,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """The last complete ``[previous_cutoff, current_cutoff)`` interval."""
    cutoff = _latest_cutoff(tz_name, cutoff_clock, now)
    window = window_for_cutoff(
        channel_id=channel.id,
        kind=kind,
        timezone=tz_name,
        cutoff_clock=cutoff_clock,
        weekdays="0,1,2,3,4,5,6",
        cutoff=cutoff,
    )
    return window.start, window.end


def _default_channel(channels: list[Channel]) -> Channel | None:
    """Prefer a live source channel over the synthetic fixture desk."""
    if not channels:
        return None
    return next((channel for channel in channels if not channel.is_demo), channels[0])


def _window_for_channel(db: Session, channel: Channel, tz_name: str) -> tuple[datetime, datetime]:
    row = db.execute(
        select(func.min(SourcePost.posted_at), func.max(SourcePost.posted_at)).where(
            SourcePost.channel_id == channel.id, SourcePost.inclusion_status == "included"
        )
    ).one()
    start, end = row
    if start is None or end is None:
        now_local = to_local(utcnow(), tz_name)
        end_local = now_local.replace(minute=0, second=0, microsecond=0)
        return end_local - timedelta(days=1), end_local
    start = as_utc(start).replace(hour=0, minute=0, second=0, microsecond=0)
    end = as_utc(end).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return start, end


def _launch_worker(background: bool = True) -> str:
    """Start the one-shot worker outside this web request."""
    if os.environ.get("CHANNEL_PRESS_SYNC_JOBS") == "1":
        try:
            with SessionLocal() as db:
                stats = worker_once(db, max_jobs=10)
                db.commit()
            return f"Worker completed: {stats.processed} processed, {stats.published} published."
        except WorkerBusy:
            return "Worker is already running; this job stays queued for that pass."
        except Exception as exc:  # noqa: BLE001
            logger.exception("worker_launch_failed", extra={"kind": "sync"})
            return f"Worker error: {type(exc).__name__}: {str(exc)[:200]}"
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "worker-web.log"
    handle = open(log_path, "a", encoding="utf-8")
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    try:
        subprocess.Popen(
            [sys.executable, "-m", "channel_press.cli", "worker", "--once"],
            cwd=str(BASE_DIR),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    except OSError as exc:
        # The job is already committed. A failed launch must not lose it: the
        # scheduled worker will pick it up, and the operator gets a warning
        # instead of a bare 500 page.
        logger.exception("worker_launch_failed", extra={"kind": "detached"})
        return (
            f"Worker could not start ({type(exc).__name__}: {str(exc)[:160]}). "
            "The job stays queued; the scheduled worker will run it."
        )
    finally:
        handle.close()
    return "Detached worker started; this page can be closed."


def _channel_or_404(db: Session, channel_id: int, user: User, minimum: str = "viewer") -> Channel:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(404, "Channel not found.")
    if not can_access_channel(db, user, channel, minimum):
        raise HTTPException(403, "You do not have access to this channel.")
    return channel


def _edition_or_404(db: Session, edition_id: int, user: User) -> Edition:
    edition = db.get(Edition, edition_id)
    if edition is None:
        raise HTTPException(404, "Edition not found.")
    channel = db.get(Channel, edition.channel_id)
    if channel and not can_access_channel(db, user, channel, "viewer"):
        raise HTTPException(403, "You do not have access to this edition.")
    publication = publication_for_edition(db, edition)
    if publication is not None and not can_access_publication(db, user, publication):
        raise HTTPException(403, "You do not have access to this newspaper.")
    return edition


def _check_job_access(db: Session, job: Job, user: User, minimum: str = "viewer") -> None:
    """Apply the same newspaper boundary to job pages and job actions."""
    if not job.publication_id:
        return
    publication = db.get(PublicationProfile, job.publication_id)
    if publication is not None and not can_access_publication(db, user, publication, minimum):
        raise HTTPException(403, "You do not have access to this newspaper's jobs.")


def _edition_link_or_404(
    db: Session, edition: Edition, article_id: int
) -> tuple[EditionArticle, Article]:
    link = db.scalar(
        select(EditionArticle).where(
            EditionArticle.edition_id == edition.id,
            EditionArticle.article_id == article_id,
        )
    )
    if link is None:
        raise HTTPException(404, "That article is not part of this edition.")
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(404, "Article not found.")
    return link, article


def _edition_links(db: Session, edition: Edition) -> list[tuple[EditionArticle, Article]]:
    links = list(
        db.scalars(
            select(EditionArticle)
            .where(EditionArticle.edition_id == edition.id)
            .order_by(EditionArticle.sort_order.asc(), EditionArticle.id.asc())
        )
    )
    rows: list[tuple[EditionArticle, Article]] = []
    for link in links:
        article = db.get(Article, link.article_id)
        if article is not None:
            rows.append((link, article))
    return rows


def _rerender_preview(db: Session, edition: Edition, *, actor_id: int | None) -> tuple[bool, str]:
    """Render after a committed editorial change. Failure never loses the edit."""
    from ..publishing.service import LayoutQualityError, PublicationError

    try:
        result = render_preview(db, edition, actor_id=actor_id)
        db.commit()
    except (PublicationError, LayoutQualityError, ValueError) as exc:
        db.rollback()
        edition = db.get(Edition, edition.id) or edition
        edition.preview_state = "failed"
        db.commit()
        return False, f"Saved, but the preview could not be rebuilt: {str(exc)[:300]}"
    except Exception as exc:  # noqa: BLE001 - never lose a saved revision
        db.rollback()
        edition = db.get(Edition, edition.id) or edition
        edition.preview_state = "failed"
        db.commit()
        return False, f"Saved, but the preview failed: {type(exc).__name__}: {str(exc)[:240]}"
    warnings = list(result.warnings or [])
    note = f" Preview rebuilt with {len(warnings)} layout warning(s)." if warnings else " Preview is current."
    return True, note


def _safe_child(root: Path, relative: str) -> Path:
    if not root:
        raise HTTPException(404, "File not found.")
    base = Path(root).resolve()
    target = (base / relative).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise HTTPException(404, "File not found.") from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "File not found.")
    return target


def _parse_weekdays(values: Iterable[str]) -> str:
    days = sorted({int(value) for value in values if str(value).isdigit() and 0 <= int(value) <= 6})
    return ",".join(str(value) for value in days) or "0,1,2,3,4,5,6"


def _validate_clock(value: str, fallback: str) -> str:
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value or ""):
        return fallback
    return value


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return cleaned[:80]


def _sync_media(db: Session, uploads: list[UploadFile], target_dir: Path) -> int:
    count = 0
    for upload in uploads:
        if not upload or not upload.filename:
            continue
        name = Path(upload.filename).name
        payload = upload.file.read()
        if not payload:
            continue
        target = target_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        count += 1
    return count


# ---------------------------------------------------------------------------
# authentication
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz() -> JSONResponse:
    with SessionLocal() as db:
        return JSONResponse(
            {
                "ok": True,
                "setup_complete": setup_complete(db),
                "time": iso_utc(utcnow()),
                "database": str(db.bind.url).startswith("sqlite"),
            }
        )


@app.get("/setup")
def setup_page(request: Request, db: Session = Depends(session_dependency)):
    if user_count(db) > 0:
        return _redirect("/login")
    profile = ensure_defaults(db)
    db.commit()
    return render(request, "setup.html", {"profile": profile, "user": None, "csrf": "", "active": ""})


@app.post("/setup")
def setup_submit(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(""),
    password: str = Form(...),
    confirm: str = Form(...),
    demo_channel: str = Form(""),
    db: Session = Depends(session_dependency),
):
    if user_count(db) > 0:
        return _redirect("/login", error="Setup has already been completed.")
    if password != confirm:
        return _redirect("/setup", error="Passwords do not match.")
    try:
        user = create_first_owner(db, username, password, display_name)
    except ValueError as exc:
        return _redirect("/setup", error=str(exc))
    ensure_defaults(db)
    if demo_channel:
        get_or_create_demo_channel(db, user)
    mark_setup_complete(db)
    audit(db, user, "setup.complete", "user", user.id)
    raw, _login = create_login_session(db, user, request.client.host if request.client else "", request.headers.get("user-agent", ""))
    db.commit()
    response = _redirect("/", "Setup complete.")
    set_session_cookie(response, raw)
    return response


@app.get("/login")
def login_page(request: Request, db: Session = Depends(session_dependency)):
    if user_count(db) == 0:
        return _redirect("/setup")
    if get_session_user(request, db):
        return _redirect("/")
    return render(request, "login.html", {"user": None, "csrf": "", "active": ""})


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(session_dependency),
):
    try:
        user = authenticate(db, username, password, request.client.host if request.client else "")
    except PermissionError as exc:
        db.commit()
        return _redirect("/login", error=str(exc))
    raw, login = create_login_session(
        db, user, request.client.host if request.client else "", request.headers.get("user-agent", "")
    )
    db.commit()
    response = _redirect("/")
    set_session_cookie(response, raw)
    return response


@app.post("/logout")
def logout(request: Request, db: Session = Depends(session_dependency), _csrf: None = Depends(require_csrf)):
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        revoke_session(db, raw)
        db.commit()
    response = _redirect("/login", "Signed out.")
    clear_session_cookie(response)
    return response


@app.get("/account")
def account_page(request: Request, user: User = Depends(current_user), db: Session = Depends(session_dependency)):
    return render(request, "account.html", _ctx(db, request, user, "account"))


@app.post("/account")
def account_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm: str = Form(...),
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    if not verify_password(user.password_hash, current_password):
        return _redirect("/account", error="Current password is incorrect.")
    if new_password != confirm:
        return _redirect("/account", error="New passwords do not match.")
    try:
        user.password_hash = hash_password(new_password)
    except ValueError as exc:
        return _redirect("/account", error=str(exc))
    user.must_change_password = False
    audit(db, user, "user.password_change", "user", user.id)
    db.commit()
    return _redirect("/account", "Password updated.")


# ---------------------------------------------------------------------------
# dashboard and sources
# ---------------------------------------------------------------------------


@app.get("/media-cache")
def media_cache_page(
    request: Request,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
):
    active_jobs = len(
        list(
            db.scalars(
                select(Job).where(Job.status.in_(["running", "scheduled", "queued", "resumable"]))
            )
        )
    )
    return render(
        request,
        "media_cache.html",
        _ctx(
            db,
            request,
            user,
            "media_cache",
            summary=media_cache_summary(db, mode="unused"),
            active_jobs=active_jobs,
        ),
    )


@app.post("/media-cache/clear")
def media_cache_clear(
    request: Request,
    mode: str = Form("unused"),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    if mode not in {"unused", "all"}:
        raise HTTPException(400, "Unknown cache clear mode.")
    result = clear_media_cache(db, include_derivatives=True, mode=mode)
    audit(db, user, "media.cache_clear", "media_cache", 0, {"mode": mode, **{k: v for k, v in result.items() if k != "deleted_files"}})
    db.commit()
    message = f"Deleted {result['deleted']} files and freed {result['bytes']} bytes."
    if result["skipped"]:
        message += f" {result['skipped']} media records were protected and kept."
    if result["failed"]:
        message += f" {result['failed']} files could not be removed; they were skipped."
    return _redirect("/media-cache", message)

@app.get("/")
def dashboard(request: Request, user: User = Depends(current_user), db: Session = Depends(session_dependency)):
    publication = resolve_publication(db, request, user)
    channels = _scope_channels(db, publication, user) if publication is not None else []
    if publication is None:
        channels = [channel for channel in db.scalars(select(Channel).order_by(Channel.id)) if can_access_channel(db, user, channel)]
    channel_ids = [channel.id for channel in channels] or [-1]
    story_ids = select(StoryCluster.id).where(StoryCluster.channel_id.in_(channel_ids))
    article_ids = select(Article.id).join(StoryCluster, Article.story_id == StoryCluster.id).where(
        StoryCluster.channel_id.in_(channel_ids)
    )
    edition_scope = Edition.channel_id.in_(channel_ids)
    job_scope = Job.channel_id.in_(channel_ids)
    if publication is not None:
        edition_scope = or_(Edition.publication_id == publication.id, edition_scope)
        job_scope = or_(Job.publication_id == publication.id, job_scope)
    counts = {
        "posts": db.scalar(
            select(func.count()).select_from(SourcePost).where(SourcePost.channel_id.in_(channel_ids))
        )
        or 0,
        "stories": db.scalar(
            select(func.count()).select_from(StoryCluster).where(StoryCluster.channel_id.in_(channel_ids))
        )
        or 0,
        "articles": db.scalar(select(func.count()).select_from(Article).where(Article.id.in_(article_ids)))
        or 0,
        "editions": db.scalar(select(func.count()).select_from(Edition).where(edition_scope)) or 0,
        "review": db.scalar(
            select(func.count())
            .select_from(ReviewFlag)
            .where(
                ReviewFlag.status == "open",
                or_(
                    ReviewFlag.article_id.in_(article_ids),
                    ReviewFlag.story_id.in_(story_ids),
                ),
            )
        )
        or 0,
        "jobs": db.scalar(
            select(func.count())
            .select_from(Job)
            .where(
                job_scope,
                Job.status.in_(["scheduled", "failed_retryable", "running", "resumable"]),
            )
        )
        or 0,
    }
    editions = list(
        db.scalars(select(Edition).where(edition_scope).order_by(Edition.created_at.desc()).limit(6))
    )
    jobs = list(db.scalars(select(Job).where(job_scope).order_by(Job.id.desc()).limit(6)))
    recent_flags = list(
        db.scalars(
            select(ReviewFlag)
            .where(
                ReviewFlag.status == "open",
                or_(
                    ReviewFlag.article_id.in_(article_ids),
                    ReviewFlag.story_id.in_(story_ids),
                ),
            )
            .order_by(ReviewFlag.created_at.desc())
            .limit(6)
        )
    )
    return render(
        request,
        "dashboard.html",
        _ctx(
            db,
            request,
            user,
            "dashboard",
            counts=counts,
            channels=channels,
            editions=editions,
            jobs=jobs,
            flags=recent_flags,
            ready=setup_complete(db),
        ),
    )


@app.get("/sources")
def sources_page(request: Request, user: User = Depends(current_user), db: Session = Depends(session_dependency)):
    channels = list(db.scalars(select(Channel).order_by(Channel.id)))
    rows = []
    for channel in channels:
        if not can_access_channel(db, user, channel):
            continue
        rows.append(
            {
                "channel": channel,
                "rights": active_rights(db, channel.id),
                "posts": db.scalar(
                    select(func.count()).select_from(SourcePost).where(SourcePost.channel_id == channel.id)
                )
                or 0,
                "included": db.scalar(
                    select(func.count())
                    .select_from(SourcePost)
                    .where(SourcePost.channel_id == channel.id, SourcePost.inclusion_status == "included")
                )
                or 0,
                "stories": db.scalar(
                    select(func.count()).select_from(StoryCluster).where(StoryCluster.channel_id == channel.id)
                )
                or 0,
            }
        )
    return render(request, "sources.html", _ctx(db, request, user, "sources", rows=rows))


@app.post("/sources/new")
def source_create(
    request: Request,
    title: str = Form(...),
    slug: str = Form(""),
    handle: str = Form(""),
    public: str = Form(""),
    grantor: str = Form(""),
    authority_basis: str = Form(""),
    evidence_ref: str = Form(""),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    slug_value = _slug(slug or title)
    if not slug_value:
        return _redirect("/sources", error="A channel slug is required.")
    if db.scalar(select(Channel).where(Channel.slug == slug_value)):
        return _redirect("/sources", error="That channel slug already exists.")
    if not grantor.strip() or not authority_basis.strip():
        return _redirect(
            "/sources",
            error="Record the grantor and authority basis before registering a live channel.",
        )
    channel = Channel(
        slug=slug_value,
        title=title.strip()[:160],
        handle=handle.strip(),
        is_public=bool(public),
        is_demo=False,
    )
    db.add(channel)
    db.flush()
    publication = resolve_publication(db, request, user)
    if publication is not None and can_access_publication(db, user, publication, "editor"):
        next_priority = (
            db.scalar(
                select(func.max(PublicationSource.priority)).where(
                    PublicationSource.publication_id == publication.id
                )
            )
            or 0
        ) + 1
        db.add(
            PublicationSource(
                publication_id=publication.id,
                channel_id=channel.id,
                priority=next_priority,
                weight=1.0,
                is_enabled=True,
            )
        )
        db.flush()
        audit(
            db,
            user,
            "publication.source",
            "publication_profile",
            publication.id,
            {"channel": channel.id, "priority": next_priority, "source": "created"},
        )
    db.add(
        ChannelOwnership(
            channel_id=channel.id,
            owner_user_id=user.id,
            granted_by_id=user.id,
            note="Channel registered by an editor; owner confirmed in the rights declaration.",
        )
    )
    db.add(
        RightsGrant(
            channel_id=channel.id,
            grantor_name=grantor.strip()[:160],
            authority_basis=authority_basis.strip()[:240],
            scope_json=json.dumps(["ingest", "ai_process", "publish"]),
            evidence_ref=evidence_ref.strip()[:500],
            created_by=user.id,
            notes="Declared in the newsroom UI. This declaration is the legal basis for processing.",
        )
    )
    audit(db, user, "channel.create", "channel", channel.id, {"slug": channel.slug})
    db.commit()
    return _redirect(f"/sources/{channel.id}", "Channel and rights declaration recorded.")


@app.get("/sources/{channel_id}")
def source_detail(
    channel_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    channel = _channel_or_404(db, channel_id, user)
    posts = list(
        db.scalars(
            select(SourcePost)
            .where(SourcePost.channel_id == channel.id)
            .order_by(SourcePost.posted_at.desc())
            .limit(100)
        )
    )
    imports = list(
        db.scalars(
            select(SourceImport)
            .where(SourceImport.channel_id == channel.id)
            .order_by(SourceImport.id.desc())
            .limit(10)
        )
    )
    grants = list(
        db.scalars(select(RightsGrant).where(RightsGrant.channel_id == channel.id).order_by(RightsGrant.id.desc()))
    )
    schedules = list(
        db.scalars(select(EditionSchedule).where(EditionSchedule.channel_id == channel.id).order_by(EditionSchedule.id))
    )
    stories = list(
        db.scalars(
            select(StoryCluster)
            .where(StoryCluster.channel_id == channel.id)
            .order_by(StoryCluster.importance.desc())
            .limit(20)
        )
    )
    media_counts = dict(
        db.execute(
            select(PostMedia.post_id, func.count(PostMedia.id))
            .where(PostMedia.post_id.in_([post.id for post in posts] or [-1]))
            .group_by(PostMedia.post_id)
        ).all()
    )
    return render(
        request,
        "source.html",
        _ctx(
            db,
            request,
            user,
            "sources",
            channel=channel,
            posts=posts,
            imports=imports,
            grants=grants,
            schedules=schedules,
            stories=stories,
            rights=active_rights(db, channel.id),
            exclusion_reasons=EXCLUSION_REASONS,
            classification_labels=CLASSIFICATION_LABELS,
            override_choices=EDITOR_OVERRIDE_CHOICES,
            media_counts=media_counts,
            window=_window_for_channel(db, channel, _timezone(db.get(PublicationProfile, 1))),
            channel_permission=channel_role(db, user, channel),
            live_handle=_live_handle(channel),
            telegram_status=telegram_service.status(probe=False) if not channel.is_demo else None,
            windows=window_summary(db, channel.id, limit=8),
        ),
    )


@app.post("/sources/{channel_id}/rights")
def source_rights(
    channel_id: int,
    request: Request,
    grantor_name: str = Form(...),
    authority_basis: str = Form(...),
    evidence_ref: str = Form(""),
    notes: str = Form(""),
    scope: list[str] = Form(["ingest", "ai_process", "publish"]),
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    channel = _channel_or_404(db, channel_id, user, "owner")
    if not grantor_name.strip() or not authority_basis.strip():
        return _redirect(f"/sources/{channel.id}", error="Grantor and authority basis are required.")
    grant = RightsGrant(
        channel_id=channel.id,
        grantor_name=grantor_name.strip()[:160],
        authority_basis=authority_basis.strip()[:240],
        scope_json=json.dumps(sorted(set(scope))),
        evidence_ref=evidence_ref.strip()[:500],
        created_by=user.id,
        notes=notes.strip(),
    )
    db.add(grant)
    audit(db, user, "rights.grant", "channel", channel.id, {"grantor": grant.grantor_name})
    db.commit()
    return _redirect(f"/sources/{channel.id}", "Rights declaration recorded.")


@app.post("/sources/{channel_id}/rights/{grant_id}/revoke")
def source_rights_revoke(
    channel_id: int,
    grant_id: int,
    request: Request,
    reason: str = Form(...),
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    channel = _channel_or_404(db, channel_id, user, "owner")
    grant = db.get(RightsGrant, grant_id)
    if grant is None or grant.channel_id != channel.id:
        raise HTTPException(404, "Rights declaration not found.")
    grant.revoked_at = utcnow()
    grant.revoked_reason = reason.strip()[:1000]
    audit(db, user, "rights.revoke", "channel", channel.id, {"grant": grant.id, "reason": reason[:300]})
    db.commit()
    return _redirect(
        f"/sources/{channel.id}",
        "Rights declaration revoked. New imports and AI processing are blocked.",
    )


@app.post("/sources/{channel_id}/import")
async def source_import(
    channel_id: int,
    request: Request,
    file: UploadFile = File(...),
    media: list[UploadFile] = File(default=[]),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    channel = _channel_or_404(db, channel_id, user, "editor")
    rights = active_rights(db, channel.id)
    if not channel.is_demo and rights is None:
        return _redirect(
            f"/sources/{channel.id}",
            error="Import blocked: record an active rights declaration first.",
        )
    payload_bytes = await file.read()
    if len(payload_bytes) > settings.max_import_bytes:
        return _redirect(f"/sources/{channel.id}", error="Import exceeds the 40 MB limit.")
    try:
        payload = json.loads(payload_bytes.decode("utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("Top-level JSON must be an object.")
        messages = normalize_messages(payload)
    except (ValueError, UnicodeDecodeError, ImportFormatError) as exc:
        return _redirect(f"/sources/{channel.id}", error=f"Invalid import: {exc}")
    if not messages:
        return _redirect(f"/sources/{channel.id}", error="No messages were found in the import.")
    temp_dir = settings.data_dir / "imports" / uuid.uuid4().hex[:12]
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        _sync_media(db, media, temp_dir)
        ensure_import_media_dir(db, settings.media_dir)
        stats = import_payload(
            db,
            channel,
            payload,
            imported_by=user.id,
            media_root=temp_dir,
            source_kind="json",
            original_name=file.filename or "import.json",
            rights_grant=rights,
        )
        audit(
            db,
            user,
            "source.import_ui",
            "channel",
            channel.id,
            {"import": stats.import_id, "raw": stats.raw_count, "rejected": stats.rejected},
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        return _redirect(f"/sources/{channel.id}", error=f"Import failed: {type(exc).__name__}: {str(exc)[:300]}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
    return _redirect(
        f"/sources/{channel.id}",
        f"Imported {stats.raw_count} messages: {stats.created} new, {stats.updated} updated, "
        f"{stats.rejected} excluded, {stats.media} media.",
    )


@app.post("/sources/{channel_id}/posts/{post_id}/override")
def source_post_override(
    channel_id: int,
    post_id: int,
    request: Request,
    decision: str = Form(...),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    """Record an editor decision that survives re-imports and rebuilds."""
    channel = _channel_or_404(db, channel_id, user, "editor")
    post = db.get(SourcePost, post_id)
    if post is None or post.channel_id != channel.id:
        raise HTTPException(404, "Source post not found.")
    if decision not in EDITOR_OVERRIDES:
        raise HTTPException(400, "Unknown editor decision.")
    status, reason, classification = EDITOR_OVERRIDES[decision]
    previous = {
        "override": post.editor_override,
        "inclusion": post.inclusion_status,
        "classification": post.classification,
    }
    post.editor_override = decision
    post.inclusion_status = status
    post.exclusion_reason = reason
    post.classification = classification
    post.classification_reason = f"editor_override:{decision}"
    post.classification_version = CLASSIFIER_VERSION
    post.classified_at = utcnow()
    audit(db, user, "source.override", "source_post", post.id, {"decision": decision, "previous": previous})
    db.commit()
    return _redirect(
        f"/sources/{channel.id}",
        "Editor decision recorded; it survives re-imports and edition rebuilds.",
    )


@app.post("/sources/{channel_id}/posts/{post_id}/withdraw")
def source_withdraw(
    channel_id: int,
    post_id: int,
    request: Request,
    reason: str = Form("rights_withdrawn"),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    channel = _channel_or_404(db, channel_id, user, "editor")
    post = db.get(SourcePost, post_id)
    if post is None or post.channel_id != channel.id:
        raise HTTPException(404, "Source post not found.")
    post.inclusion_status = "excluded"
    post.exclusion_reason = reason[:80]
    post.deleted_at = post.deleted_at or utcnow()
    audit(db, user, "source.withdraw", "source_post", post.id, {"reason": reason[:200]})
    db.commit()
    return _redirect(
        f"/sources/{channel.id}",
        "Source post withdrawn. Rebuild the affected edition before further release.",
    )


# ---------------------------------------------------------------------------
# authorized Telegram connection and cutoff collection
# ---------------------------------------------------------------------------


def _live_handle(channel: Channel) -> str:
    return (channel.handle or settings.telegram_source_username or "").strip().lstrip("@")


def _telegram_error(exc: Exception) -> str:
    """Actionable, redacted copy; Telegram errors already explain the remedy."""
    if isinstance(exc, TelegramTransportError):
        return redact(str(exc))[:400]
    return redact(f"{type(exc).__name__}: {exc}")[:400]


def _telegram_ready(db: Session, channel: Channel) -> str:
    """Bounded read-only preflight; return '' when Telegram is Ready.

    The database write transaction is committed first so the web never holds
    the SQLite writer across the network call.
    """
    db.commit()
    try:
        telegram_service.preflight(
            channel.handle, expected_id=str(channel.telegram_channel_id or "")
        )
    except TelegramTransportError as exc:
        return _telegram_error(exc)
    return ""


def _daily_schedule(db: Session, channel: Channel) -> EditionSchedule | None:
    return db.scalar(
        select(EditionSchedule)
        .where(EditionSchedule.channel_id == channel.id, EditionSchedule.kind == "daily")
        .order_by(EditionSchedule.id)
    )


@app.get("/sources/{channel_id}/telegram")
def telegram_page(
    channel_id: int,
    request: Request,
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
):
    channel = _channel_or_404(db, channel_id, user, "owner")
    schedule = _daily_schedule(db, channel)
    preview = preview_window(db, schedule) if schedule else None
    checkpoints = {}
    for row in window_summary(db, channel.id, limit=20):
        checkpoint = checkpoint_for(
            db,
            EditionWindow(
                channel_id=channel.id,
                kind=row["record"].kind,
                start=row["record"].window_start,
                end=row["record"].window_end,
            ),
        )
        checkpoints[row["record"].id] = checkpoint
    return render(
        request,
        "telegram.html",
        _ctx(
            db,
            request,
            user,
            "sources",
            channel=channel,
            schedule=schedule,
            status=telegram_service.status(probe=False),
            login=telegram_service.login_state(),
            handle=_live_handle(channel),
            preview=preview,
            windows=window_summary(db, channel.id, limit=20),
            checkpoints=checkpoints,
            lookback=settings.initial_context_lookback_days,
            rights=active_rights(db, channel.id),
        ),
    )


@app.post("/sources/{channel_id}/telegram/handle")
def telegram_handle(
    channel_id: int,
    request: Request,
    handle: str = Form(...),
    public: str = Form(""),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    channel = _channel_or_404(db, channel_id, user, "editor")
    try:
        normalized = normalize_handle(handle)
    except TelegramTransportError as exc:
        return _redirect(f"/sources/{channel.id}/telegram", error=str(exc))
    channel.handle = f"@{normalized}"
    channel.is_public = bool(public)
    audit(db, user, "channel.handle", "channel", channel.id, {"handle": channel.handle})
    db.commit()
    return _redirect(
        f"/sources/{channel.id}/telegram",
        "Source handle saved. Run Test source to resolve the stable channel identity.",
    )


@app.post("/sources/{channel_id}/telegram/test")
def telegram_test(
    channel_id: int,
    request: Request,
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    channel = _channel_or_404(db, channel_id, user, "owner")
    handle = _live_handle(channel)
    if not handle:
        return _redirect(f"/sources/{channel.id}/telegram", error="Set the channel handle first.")
    rights = active_rights(db, channel.id)
    if rights is None:
        return _redirect(
            f"/sources/{channel.id}/telegram",
            error="Record the rights declaration before connecting to a live source.",
        )
    try:
        db.commit()
        info = telegram_service.preflight(handle, expected_id=str(channel.telegram_channel_id or ""))
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator
        return _redirect(f"/sources/{channel.id}/telegram", error=_telegram_error(exc))
    channel.telegram_channel_id = int(info["id"])
    if not channel.title or channel.title.startswith("@"):
        channel.title = str(info.get("title") or channel.title)[:160]
    channel.handle = f"@{info.get('username') or handle}"
    audit(
        db,
        user,
        "telegram.resolve",
        "channel",
        channel.id,
        {"telegram_id": channel.telegram_channel_id, "username": channel.handle},
    )
    db.commit()
    return _redirect(
        f"/sources/{channel.id}/telegram",
        f"Telegram Ready: {channel.handle}, verified {info['checked_at']}.",
    )


@app.post("/sources/{channel_id}/telegram/login")
def telegram_login(
    channel_id: int,
    request: Request,
    phone: str = Form(...),
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    channel = _channel_or_404(db, channel_id, user, "owner")
    try:
        telegram_service.start_login(phone)
    except Exception as exc:  # noqa: BLE001
        return _redirect(f"/sources/{channel.id}/telegram", error=_telegram_error(exc))
    audit(db, user, "telegram.login_start", "channel", channel.id)
    db.commit()
    return _redirect(
        f"/sources/{channel.id}/telegram",
        "Login code requested. Enter the code Telegram sent to the account.",
    )


@app.post("/sources/{channel_id}/telegram/code")
def telegram_code(
    channel_id: int,
    request: Request,
    code: str = Form(...),
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    channel = _channel_or_404(db, channel_id, user, "owner")
    try:
        state = telegram_service.submit_code(code)
    except Exception as exc:  # noqa: BLE001
        return _redirect(f"/sources/{channel.id}/telegram", error=_telegram_error(exc))
    audit(db, user, "telegram.login_code", "channel", channel.id, {"stage": state["stage"]})
    db.commit()
    message = (
        "Signed in. Run Test source next."
        if state["stage"] == "authorized"
        else "Two-step verification is enabled: enter the account password."
    )
    return _redirect(f"/sources/{channel.id}/telegram", message)


@app.post("/sources/{channel_id}/telegram/password")
def telegram_password(
    channel_id: int,
    request: Request,
    password: str = Form(...),
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    channel = _channel_or_404(db, channel_id, user, "owner")
    try:
        telegram_service.submit_password(password)
    except Exception as exc:  # noqa: BLE001
        return _redirect(f"/sources/{channel.id}/telegram", error=_telegram_error(exc))
    audit(db, user, "telegram.login_2fa", "channel", channel.id)
    db.commit()
    return _redirect(f"/sources/{channel.id}/telegram", "Signed in with two-step verification.")


@app.post("/sources/{channel_id}/telegram/cancel")
def telegram_cancel(
    channel_id: int,
    request: Request,
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    channel = _channel_or_404(db, channel_id, user, "owner")
    telegram_service.cancel_login()
    audit(db, user, "telegram.login_cancel", "channel", channel.id)
    db.commit()
    return _redirect(f"/sources/{channel.id}/telegram", "Login cancelled. No session was created.")


@app.post("/sources/{channel_id}/telegram/logout")
def telegram_logout(
    channel_id: int,
    request: Request,
    confirm: str = Form(""),
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    channel = _channel_or_404(db, channel_id, user, "owner")
    if not confirm:
        return _redirect(
            f"/sources/{channel.id}/telegram",
            error="Confirm the sign-out checkbox. This deletes the local session file.",
        )
    try:
        telegram_service.logout()
    except Exception as exc:  # noqa: BLE001
        return _redirect(f"/sources/{channel.id}/telegram", error=_telegram_error(exc))
    audit(db, user, "telegram.logout", "channel", channel.id)
    db.commit()
    return _redirect(f"/sources/{channel.id}/telegram", "Signed out; the local session was removed.")


@app.post("/sources/{channel_id}/telegram/context")
def telegram_context(
    channel_id: int,
    request: Request,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    channel = _channel_or_404(db, channel_id, user, "editor")
    if channel.is_demo:
        return _redirect(f"/sources/{channel.id}/telegram", error="Demo channels use fixtures.")
    if not _live_handle(channel):
        return _redirect(f"/sources/{channel.id}/telegram", error="Set the channel handle first.")
    if active_rights(db, channel.id) is None:
        return _redirect(
            f"/sources/{channel.id}/telegram",
            error="Context import blocked: record the content-rights declaration first.",
        )
    blocked = _telegram_ready(db, channel)
    if blocked:
        return _redirect(
            f"/sources/{channel.id}/telegram",
            error=f"Context import not queued. {blocked}",
        )
    now = utcnow()
    window = EditionWindow(
        channel_id=channel.id,
        kind="context",
        start=now - timedelta(days=max(1, settings.initial_context_lookback_days)),
        end=now,
        timezone=_timezone(db.get(PublicationProfile, 1)),
        cutoff_local=settings.cutoff_local_time,
    )
    job = enqueue_collect_job(
        db,
        channel,
        window=window,
        mode="collect_context",
        actor_id=user.id,
        limit=settings.telegram_history_limit,
    )
    audit(db, user, "telegram.context_import", "channel", channel.id, {"job": job.id})
    db.commit()
    launched = _launch_worker()
    return _redirect(
        f"/jobs/{job.id}",
        f"Context backfill queued for the last {settings.initial_context_lookback_days} days; "
        f"it will not enter the next daily edition. {launched}",
    )


@app.post("/sources/{channel_id}/telegram/sync")
def telegram_sync(
    channel_id: int,
    request: Request,
    user: User = Depends(require_role("operator")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    channel = _channel_or_404(db, channel_id, user, "operator")
    if channel.is_demo:
        return _redirect(f"/sources/{channel.id}/telegram", error="Demo channels use fixtures.")
    handle = _live_handle(channel)
    if not handle:
        return _redirect(f"/sources/{channel.id}/telegram", error="Set the channel handle first.")
    if active_rights(db, channel.id) is None:
        return _redirect(
            f"/sources/{channel.id}/telegram",
            error="Sync blocked: record the content-rights declaration first.",
        )
    schedule = _daily_schedule(db, channel)
    if schedule is not None:
        cutoff = latest_due_cutoff(schedule, utcnow()) or utcnow()
        from ..collection.service import window_for_schedule

        window = window_for_schedule(schedule, cutoff)
    else:
        now = utcnow()
        window = EditionWindow(
            channel_id=channel.id,
            kind="daily",
            start=now - timedelta(days=1),
            end=now,
            timezone=_timezone(db.get(PublicationProfile, 1)),
            cutoff_local=settings.cutoff_local_time,
        )
    blocked = _telegram_ready(db, channel)
    if blocked:
        existing = db.scalar(select(Job).where(Job.key == f"sync_window:{window.key}"))
        if existing is not None:
            return _redirect(
                f"/jobs/{existing.id}",
                f"This window already has job #{existing.id}; no new collection was queued. {blocked}",
            )
        return _redirect(
            f"/sources/{channel.id}/telegram",
            error=f"Sync not queued. {blocked}",
        )
    job = enqueue_collect_job(
        db,
        channel,
        window=window,
        mode="sync_window",
        actor_id=user.id,
        limit=settings.telegram_history_limit,
    )
    audit(db, user, "telegram.sync", "channel", channel.id, {"job": job.id, "window": window.key})
    db.commit()
    launched = _launch_worker()
    return _redirect(
        f"/jobs/{job.id}",
        f"Sync queued for {window.label()}. This stores source posts only; no edition is built. {launched}",
    )


# ---------------------------------------------------------------------------
# editorial review
# ---------------------------------------------------------------------------


@app.get("/media/{media_id}/preview")
def media_preview(
    media_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    media = db.get(PostMedia, media_id)
    if media is None:
        raise HTTPException(404, "Media not found.")
    post = db.get(SourcePost, media.post_id)
    if post is None:
        raise HTTPException(404, "Source post not found.")
    _channel_or_404(db, post.channel_id, user)
    candidate = media.derivative_path if media.derivative_path and Path(media.derivative_path).exists() else media.original_path
    if not candidate or not Path(candidate).exists():
        raise HTTPException(404, "The local preview is not available.")
    suffix = Path(candidate).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        raise HTTPException(404, "No image preview is available for this asset.")
    return FileResponse(Path(candidate))


@app.get("/media/{media_id}/original")
def media_original(
    media_id: int,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
):
    """Download the stored original for editorial inspection.

    Preview routes compress and can substitute a derivative; this route serves
    the exact file the channel published so an editor can verify crop, print
    sharpness and caption against the source. It is not a public endpoint: the
    caller needs editorial access to the channel and, for a real channel, a
    current rights grant. Generated derivative files are never exposed as
    "originals".
    """
    media = db.get(PostMedia, media_id)
    if media is None:
        raise HTTPException(404, "Media not found.")
    post = db.get(SourcePost, media.post_id)
    if post is None:
        raise HTTPException(404, "Source post not found.")
    channel = _channel_or_404(db, post.channel_id, user, "editor")
    if not channel.is_demo:
        grant = active_rights(db, channel.id)
        if grant is None or grant.revoked_at is not None:
            raise HTTPException(403, "No active content-rights grant covers this asset.")
    original = (media.original_path or "").strip()
    if not original:
        raise HTTPException(404, "No stored original is available. Prepare the media cache first.")
    path = Path(original)
    try:
        resolved = path.resolve()
        root = settings.media_dir.resolve()
    except OSError:
        raise HTTPException(404, "The stored original is not readable.") from None
    if root not in resolved.parents:
        raise HTTPException(403, "Stored media outside the managed cache is not downloadable.")
    if not resolved.is_file():
        raise HTTPException(404, "The stored original is missing from the local cache.")
    safe_name = f"media-{media.id}{resolved.suffix.lower() or '.bin'}"
    return FileResponse(
        resolved,
        filename=safe_name,
        media_type="application/octet-stream",
        headers={"Cache-Control": "private, no-store"},
    )


@app.get("/stories")
def stories_page(
    request: Request,
    channel: int | None = None,
    section: str = "",
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    publication = resolve_publication(db, request, user)
    channels = _scope_channels(db, publication, user)
    if publication is None:
        channels = [c for c in db.scalars(select(Channel).order_by(Channel.id)) if can_access_channel(db, user, c)]
    stmt = select(StoryCluster).order_by(StoryCluster.importance.desc(), StoryCluster.id.desc())
    allowed_ids = [c.id for c in channels]
    stmt = stmt.where(StoryCluster.channel_id.in_(allowed_ids or [-1]))
    if channel:
        stmt = stmt.where(StoryCluster.channel_id == channel)
    if section:
        stmt = stmt.where(StoryCluster.section == section)
    stories = list(db.scalars(stmt.limit(200)))
    return render(
        request,
        "stories.html",
        _ctx(db, request, user, "stories", stories=stories, channels=channels, channel_id=channel, section=section),
    )


@app.get("/stories/{story_id}")
def story_detail(
    story_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    story = db.get(StoryCluster, story_id)
    if story is None:
        raise HTTPException(404, "Story not found.")
    _channel_or_404(db, story.channel_id, user)
    claims = list(db.scalars(select(StoryClaim).where(StoryClaim.story_id == story.id).order_by(StoryClaim.event_at.asc(), StoryClaim.id.asc())))
    timeline = list(db.scalars(select(StoryTimelineEvent).where(StoryTimelineEvent.story_id == story.id).order_by(StoryTimelineEvent.event_at.asc())))
    rows = db.execute(
        select(PostFragment, SourcePost)
        .join(SourcePost, PostFragment.post_id == SourcePost.id)
        .where(PostFragment.story_id == story.id)
        .order_by(SourcePost.posted_at.asc())
    ).all()
    article = db.scalar(select(Article).where(Article.story_id == story.id).order_by(Article.revision.desc()))
    external = list(
        db.scalars(
            select(ExternalReference)
            .where(ExternalReference.story_id == story.id)
            .order_by(ExternalReference.accessed_at.desc(), ExternalReference.id.desc())
        )
    )
    publication = publication_for_channel(db, db.get(Channel, story.channel_id))
    search_state = provider_status(publication.research_provider if publication else "auto")
    return render(
        request,
        "story.html",
        _ctx(
            db,
            request,
            user,
            "stories",
            story=story,
            claims=claims,
            timeline=timeline,
            fragments=rows,
            article=article,
            external=external,
            research_policy=publication.research_policy if publication else "editor_triggered",
            research_provider=search_state,
        ),
    )


@app.post("/stories/{story_id}/research")
def story_research_add(
    story_id: int,
    url: str = Form(...),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    """Fetch one editor-supplied public page as a candidate reference."""
    story = db.get(StoryCluster, story_id)
    if story is None:
        raise HTTPException(404, "Story not found.")
    _channel_or_404(db, story.channel_id, user, "editor")
    try:
        reference, created = research_reference(db, story, url)
    except ResearchError as exc:
        db.rollback()
        return _redirect(f"/stories/{story.id}", error=str(exc))
    audit(
        db,
        user,
        "research.candidate_added" if created else "research.candidate_reused",
        object_type="external_reference",
        object_id=reference.id,
        detail={"story": story.id, "status": reference.review_status, "host": reference.publisher},
    )
    db.commit()
    return _redirect(
        f"/stories/{story.id}",
        message="Research candidate fetched. It stays out of print until an editor accepts it."
        if created
        else "That page is already recorded for this story.",
    )


@app.post("/stories/{story_id}/research/discover")
def story_research_discover(
    story_id: int,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    """Ask the configured search provider for independent candidate sources."""
    story = db.get(StoryCluster, story_id)
    if story is None:
        raise HTTPException(404, "Story not found.")
    _channel_or_404(db, story.channel_id, user, "editor")
    publication = publication_for_channel(db, db.get(Channel, story.channel_id))
    if publication is None:
        return _redirect(f"/stories/{story.id}", error="No newspaper is configured for this source.")
    if (publication.research_policy or "editor_triggered") == "off":
        return _redirect(
            f"/stories/{story.id}",
            error="Automatic search is turned off for this newspaper. Add a URL manually or enable it in settings.",
        )
    # Release the read transaction before the provider and page fetches.
    db.commit()
    try:
        result = discover_candidates(db, story, publication)
    except Exception as exc:  # provider/network failures are editorial state
        db.rollback()
        return _redirect(f"/stories/{story.id}", error=f"Search failed: {type(exc).__name__}: {str(exc)[:160]}")
    audit(
        db,
        user,
        "research.discover",
        object_type="story",
        object_id=story.id,
        detail={
            "provider": result.provider,
            "status": result.status,
            "queries": result.queries,
            "created": len(result.created),
            "skipped": result.skipped,
        },
    )
    db.commit()
    if result.status in {"unavailable", "error"}:
        return _redirect(
            f"/stories/{story.id}",
            error=f"Search unavailable: {result.detail or 'configure a provider or add a URL manually.'}",
        )
    if result.status == "disabled":
        return _redirect(f"/stories/{story.id}", error=result.detail)
    if not result.created:
        return _redirect(
            f"/stories/{story.id}",
            message=result.detail or "No new independent candidates were found. Add a URL manually if you have one.",
        )
    return _redirect(
        f"/stories/{story.id}",
        message=(
            f"Found {len(result.created)} candidate source(s) via {result.provider}. "
            "They stay out of the paper until an editor accepts them."
        ),
    )


@app.post("/stories/{story_id}/research/{reference_id}/review")
def story_research_review(
    story_id: int,
    reference_id: int,
    decision: str = Form(...),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    story = db.get(StoryCluster, story_id)
    if story is None:
        raise HTTPException(404, "Story not found.")
    _channel_or_404(db, story.channel_id, user, "editor")
    reference = db.get(ExternalReference, reference_id)
    if reference is None or reference.story_id != story.id:
        raise HTTPException(404, "Research reference not found for this story.")
    if decision not in {"accept", "reject"}:
        return _redirect(f"/stories/{story.id}", error="Unknown review decision.")
    if decision == "accept":
        accept_reference(db, reference)
        action = "research.accepted"
    else:
        reject_reference(db, reference)
        action = "research.rejected"
    audit(
        db,
        user,
        action,
        object_type="external_reference",
        object_id=reference.id,
        detail={"story": story.id, "host": reference.publisher},
    )
    db.commit()
    return _redirect(
        f"/stories/{story.id}",
        message="Reference accepted as editorial context."
        if decision == "accept"
        else "Reference rejected and excluded from print.",
    )


@app.get("/articles")
def articles_page(
    request: Request,
    status: str = "",
    channel: int | None = None,
    q: str = "",
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    publication = resolve_publication(db, request, user)
    channels = _scope_channels(db, publication, user)
    if publication is None:
        channels = [c for c in db.scalars(select(Channel).order_by(Channel.id)) if can_access_channel(db, user, c)]
    allowed_ids = [c.id for c in channels]
    stmt = select(Article).join(StoryCluster, Article.story_id == StoryCluster.id).where(
        StoryCluster.channel_id.in_(allowed_ids or [-1])
    )
    if status:
        stmt = stmt.where(Article.status == status)
    if channel:
        stmt = stmt.where(StoryCluster.channel_id == channel)
    if q:
        stmt = stmt.where(or_(Article.title.contains(q), Article.body_text.contains(q)))
    articles = list(db.scalars(stmt.order_by(Article.updated_at.desc()).limit(200)))
    return render(
        request,
        "articles.html",
        _ctx(db, request, user, "articles", articles=articles, channels=channels, status=status, channel_id=channel, q=q),
    )


@app.get("/articles/{article_id}")
def article_detail(
    article_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(404, "Article not found.")
    story = db.get(StoryCluster, article.story_id)
    if story:
        _channel_or_404(db, story.channel_id, user)
    flags = list(db.scalars(select(ReviewFlag).where(ReviewFlag.article_id == article.id).order_by(ReviewFlag.id.desc())))
    versions = revision_history(db, article)
    audit_data = json.loads(article.audit_json or "{}")
    gate, blockers = publish_gate(db, article.id)
    media = list(db.scalars(select(ArticleMedia).where(ArticleMedia.article_id == article.id)))
    alternatives = rank_media(db, story) if story is not None else []
    from ..models import ExternalReference

    external = (
        list(
            db.scalars(
                select(ExternalReference)
                .where(ExternalReference.story_id == article.story_id)
                .order_by(ExternalReference.accessed_at.desc())
            )
        )
        if story is not None
        else []
    )
    editions = list(
        db.scalars(
            select(Edition)
            .join(EditionArticle, EditionArticle.edition_id == Edition.id)
            .where(EditionArticle.article_id == article.id)
            .order_by(Edition.id.desc())
        )
    )
    article_sources = list(
        db.execute(
            select(ArticleSource, Channel)
            .join(Channel, ArticleSource.channel_id == Channel.id)
            .where(ArticleSource.article_id == article.id)
            .order_by(ArticleSource.first_posted_at.asc(), ArticleSource.id.asc())
        ).all()
    )
    return render(
        request,
        "article.html",
        _ctx(
            db,
            request,
            user,
            "articles",
            article=article,
            story=story,
            flags=flags,
            versions=versions,
            audit_data=audit_data,
            gate=gate,
            blockers=blockers,
            media=media,
            alternatives=alternatives,
            external=external,
            editions=editions,
            article_sources=article_sources,
            editorial_statuses=EDITORIAL_STATUSES,
            status_labels=STATUS_LABELS,
        ),
    )


@app.post("/articles/{article_id}/edit")
def article_edit(
    article_id: int,
    request: Request,
    title: str = Form(...),
    deck: str = Form(""),
    standfirst: str = Form(""),
    body_text: str = Form(...),
    byline: str = Form(""),
    section: str = Form(""),
    genre: str = Form(""),
    expected_revision: str = Form(""),
    lock: str = Form(""),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(404, "Article not found.")
    story = db.get(StoryCluster, article.story_id)
    if story:
        _channel_or_404(db, story.channel_id, user, "editor")
    try:
        expected = int(expected_revision) if str(expected_revision).strip().isdigit() else article.revision
        save_editor_edit(
            db,
            article,
            title=title,
            deck=deck,
            standfirst=standfirst,
            body_text=body_text,
            actor_id=user.id,
            lock=bool(lock),
            expected_revision=expected,
            byline=byline,
        )
        if section and section != article.section:
            article.section = section
        if genre and genre != article.genre:
            article.genre = genre
        from ..editorial.revisions import mark_preview_stale

        affected = list(
            db.scalars(
                select(Edition)
                .join(EditionArticle, EditionArticle.edition_id == Edition.id)
                .where(EditionArticle.article_id == article.id)
            )
        )
        for edition in affected:
            mark_preview_stale(db, edition)
        audit(db, user, "article.edit", "article", article.id, {"revision": article.revision})
        db.commit()
    except RevisionConflict as exc:
        db.rollback()
        return _redirect(f"/articles/{article.id}", error=str(exc))
    except ValueError as exc:
        db.rollback()
        return _redirect(f"/articles/{article.id}", error=str(exc))
    return _redirect(
        f"/articles/{article.id}",
        "Article saved as a new revision. Rebuild the edition preview from the edition workspace.",
    )


@app.post("/articles/{article_id}/reaudit")
def article_reaudit(
    article_id: int,
    request: Request,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(404, "Article not found.")
    story = db.get(StoryCluster, article.story_id)
    if story:
        _channel_or_404(db, story.channel_id, user, "editor")
    result = reaudit_article(db, article)
    audit(db, user, "article.reaudit", "article", article.id, {"unsupported": result.get("unsupported_count", 0)})
    db.commit()
    return _redirect(
        f"/articles/{article.id}",
        f"Audit complete: {result.get('supported', 0)}/{result.get('fact_sentences', 0)} factual sentences supported.",
    )


@app.post("/articles/{article_id}/flags/{flag_id}")
def article_flag_resolve(
    article_id: int,
    flag_id: int,
    request: Request,
    decision: str = Form(...),
    note: str = Form(""),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    article = db.get(Article, article_id)
    flag = db.get(ReviewFlag, flag_id)
    if article is None or flag is None or flag.article_id != article.id:
        raise HTTPException(404, "Review item not found.")
    story = db.get(StoryCluster, article.story_id)
    if story:
        _channel_or_404(db, story.channel_id, user, "editor")
    if decision not in {"approved", "rejected"}:
        return _redirect(f"/articles/{article.id}", error="Decision must be approved or rejected.")
    flag.status = decision
    flag.decision = decision
    flag.resolved_by = user.id
    flag.resolved_at = utcnow()
    if note.strip():
        flag.message = f"{flag.message}\nEditor note: {note.strip()[:500]}"
    audit(db, user, "review.resolve", "review_flag", flag.id, {"decision": decision, "article": article.id})
    db.commit()
    return _redirect(f"/articles/{article.id}", f"Review item {decision}.")


@app.post("/articles/{article_id}/status")
def article_status(
    article_id: int,
    request: Request,
    status: str = Form(...),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(404, "Article not found.")
    story = db.get(StoryCluster, article.story_id)
    if story:
        _channel_or_404(db, story.channel_id, user, "editor")
    if status not in EDITORIAL_STATUSES:
        return _redirect(f"/articles/{article.id}", error="Unknown article status.")
    gate, blockers = publish_gate(db, article.id)
    if status == "approved" and not gate:
        return _redirect(
            f"/articles/{article.id}",
            error="Cannot approve while blocking review items remain: " + ", ".join(blockers),
        )
    set_article_status(db, article, status, actor_id=user.id)
    from ..editorial.revisions import mark_preview_stale

    for edition in db.scalars(
        select(Edition)
        .join(EditionArticle, EditionArticle.edition_id == Edition.id)
        .where(EditionArticle.article_id == article.id)
    ):
        mark_preview_stale(db, edition)
    audit(db, user, "article.status", "article", article.id, {"status": status, "revision": article.revision})
    db.commit()
    return _redirect(
        f"/articles/{article.id}",
        f"Article marked {STATUS_LABELS.get(status, status).lower()}.",
    )


@app.post("/articles/{article_id}/lock")
def article_lock(
    article_id: int,
    request: Request,
    locked: str = Form(""),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    article = db.get(Article, article_id)
    if article is None:
        raise HTTPException(404, "Article not found.")
    article.locked = bool(locked)
    audit(db, user, "article.lock", "article", article.id, {"locked": article.locked})
    db.commit()
    return _redirect(f"/articles/{article.id}", "Article lock updated.")


# ---------------------------------------------------------------------------
# build, editions and page studio
# ---------------------------------------------------------------------------


@app.get("/build")
def build_page(
    request: Request,
    channel: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    profile = resolve_publication(db, request, user)
    channels = [
        c
        for c in (
            publication_channels(db, profile, enabled_only=True)
            if profile is not None
            else []
        )
        if can_access_channel(db, user, c, "operator")
    ]
    if profile is not None:
        channels.extend(
            channel
            for channel in unassigned_channels(db, user, "operator")
            if channel.id not in {item.id for item in channels}
        )
    if not channels and profile is None:
        channels = [
            c
            for c in db.scalars(select(Channel).order_by(Channel.id))
            if can_access_channel(db, user, c, "operator")
        ]
    selected = db.get(Channel, channel) if channel else _default_channel(channels)
    if selected is not None and selected not in channels:
        selected = _default_channel(channels)
    tz_name = _timezone(profile)
    schedule = _daily_schedule(db, selected) if selected else None
    cutoff_clock = schedule.cutoff_time if schedule else settings.cutoff_local_time
    preview = preview_window(db, schedule) if schedule and selected and not selected.is_demo else None
    if selected is None:
        window = (utcnow() - timedelta(days=1), utcnow())
        window_source = "none"
    elif selected.is_demo:
        window = _window_for_channel(db, selected, tz_name)
        window_source = "fixture"
    elif preview:
        window = (
            datetime.fromisoformat(preview["window"]["start_utc"]),
            datetime.fromisoformat(preview["window"]["end_utc"]),
        )
        window_source = "schedule"
    else:
        window = _cutoff_window(selected, tz_name, cutoff_clock, "manual")
        window_source = "default_cutoff"
    rights = active_rights(db, selected.id) if selected else None
    return render(
        request,
        "build.html",
        _ctx(
            db,
            request,
            user,
            "build",
            channels=channels,
            selected=selected,
            window_start_local=to_local(window[0], tz_name).strftime("%Y-%m-%dT%H:%M"),
            window_end_local=to_local(window[1], tz_name).strftime("%Y-%m-%dT%H:%M"),
            rights=rights,
            is_demo=bool(selected and selected.is_demo),
            schedule=schedule,
            preview=preview,
            cutoff_clock=cutoff_clock,
            window_source=window_source,
            source_count=len(channels),
        ),
    )


@app.post("/build")
def build_submit(
    request: Request,
    channel_id: int = Form(...),
    window_start: str = Form(...),
    window_end: str = Form(...),
    kind: str = Form("manual"),
    title: str = Form(""),
    publish: str = Form("publish"),
    collect: str = Form(""),
    all_sources: str = Form("1"),
    user: User = Depends(require_role("operator")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    channel = _channel_or_404(db, channel_id, user, "operator")
    profile = resolve_publication(db, request, user)
    if profile is None:
        raise HTTPException(404, "Publication profile missing.")
    if not can_access_publication(db, user, profile, "operator"):
        raise HTTPException(403, "You do not have operational access to this publication.")
    publication_sources = publication_channels(db, profile, enabled_only=True)
    linked_ids = {item.id for item in publication_sources}
    unassigned = channel_is_unassigned(db, channel.id)
    if publication_sources and channel.id not in linked_ids and not unassigned:
        return _redirect(
            f"/build?channel={channel.id}",
            error="That channel is not an enabled source of the selected newspaper.",
        )
    if unassigned:
        channels = [channel]
    else:
        channels = publication_sources if (all_sources == "1" and publication_sources) else [channel]
    tz_name = _timezone(profile)
    schedule = _daily_schedule(db, channel)
    cutoff_clock = schedule.cutoff_time if schedule else settings.cutoff_local_time
    start = _parse_local(window_start, tz_name)
    end = _parse_local(window_end, tz_name)
    if not start or not end or start >= end:
        return _redirect(
            f"/build?channel={channel.id}",
            error="The reporting window is invalid: enter a start before the cutoff.",
        )
    if end - start > timedelta(days=31):
        return _redirect(
            f"/build?channel={channel.id}",
            error="The reporting window is longer than 31 days. Use a cutoff-to-cutoff window.",
        )
    for source in channels:
        if not source.is_demo and active_rights(db, source.id) is None:
            return _redirect(
                f"/build?channel={channel.id}",
                error=(
                    f"Build blocked: {source.title} has no active rights declaration. "
                    "Record a grant or disable that source for this newspaper."
                ),
            )
    ready = None
    if not channel.is_demo and collect == "1":
        db.commit()
        try:
            ready = telegram_service.preflight(
                channel.handle, expected_id=str(channel.telegram_channel_id or "")
            )
        except TelegramTransportError as exc:
            return _redirect(f"/build?channel={channel.id}", error=str(exc))
    try:
        job = enqueue_manual_job(
            db,
            channel,
            window_start=start,
            window_end=end,
            kind=kind if kind in {"manual", "special"} else "manual",
            actor_id=user.id,
            title=title.strip()[:240],
            collect=(any(not item.is_demo for item in channels)) and collect == "1",
            timezone=tz_name,
            cutoff_local=cutoff_clock,
            publication=profile,
            channels=channels,
        )
    except DuplicateWindowError as exc:
        db.rollback()
        return _redirect(f"/build?channel={channel.id}", error=str(exc))
    reused = bool(getattr(job, "reused_existing", False))
    audit(
        db,
        user,
        "job.manual_ui_reused" if reused else "job.manual_ui",
        "job",
        job.id,
        {
            "channel": channel.id,
            "publication": profile.id,
            "sources": [item.id for item in channels],
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "collect": collect == "1",
            "cutoff_local": cutoff_clock,
        },
    )
    db.commit()
    if reused:
        launched = f"This window already has job #{job.id} ({job.status.replace('_', ' ')}); no duplicate was created."
    else:
        launched = _launch_worker()
    preflight_note = (f"Telegram Ready: @{ready['channel']} ({ready['checked_at']}). " if ready else "")
    return _redirect(f"/jobs/{job.id}", f"{preflight_note}Build queued. {launched}")


@app.get("/editions")
def editions_page(request: Request, user: User = Depends(current_user), db: Session = Depends(session_dependency)):
    publication = resolve_publication(db, request, user)
    allowed = [c.id for c in _scope_channels(db, publication, user)]
    scope = Edition.channel_id.in_(allowed or [-1])
    if publication is not None:
        scope = or_(Edition.publication_id == publication.id, scope)
    editions = list(
        db.scalars(
            select(Edition)
            .where(scope)
            .order_by(Edition.created_at.desc())
            .limit(100)
        )
    )
    return render(
        request,
        "editions.html",
        _ctx(db, request, user, "editions", editions=editions, publication=publication),
    )


@app.get("/editions/{edition_id}")
def edition_detail(
    edition_id: int,
    request: Request,
    article: int | None = None,
    page: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    edition = _edition_or_404(db, edition_id, user)
    channel = db.get(Channel, edition.channel_id)
    rows = _edition_links(db, edition)
    selected_link: EditionArticle | None = None
    selected: Article | None = None
    if article is not None:
        selected_link, selected = _edition_link_or_404(db, edition, article)
    elif rows:
        selected_link, selected = rows[0]
    pages = list(
        db.scalars(
            select(EditionPage)
            .where(EditionPage.edition_id == edition.id)
            .order_by(EditionPage.page_number)
        )
    )
    blockers = edition_review_blockers(db, edition.id)
    held_rows = [(link, item) for link, item in rows if item.status in BLOCKING_STATUSES]
    flags = list(
        db.scalars(
            select(ReviewFlag)
            .where(
                ReviewFlag.status == "open",
                ReviewFlag.article_id.in_([item.id for _, item in rows] or [-1]),
            )
            .order_by(ReviewFlag.id.desc())
        )
    )
    family = edition_versions(db, edition)
    manifest = json.loads(edition.manifest_json or "{}")
    preview_pages = bool(edition.preview_dir) or edition.status == "published"
    fingerprint = edition_content_fingerprint(db, edition)
    preview_current = preview_matches_content(db, edition) or (
        edition.status == "published" and bool(edition.pdf_path)
    )
    selected_context: dict = {}
    if selected is not None:
        story = db.get(StoryCluster, selected.story_id)
        evidence_rows = db.execute(
            select(PostFragment, SourcePost)
            .join(SourcePost, PostFragment.post_id == SourcePost.id)
            .where(PostFragment.story_id == selected.story_id)
            .order_by(SourcePost.posted_at.asc())
        ).all()
        claims = list(
            db.scalars(
                select(StoryClaim)
                .where(StoryClaim.story_id == selected.story_id)
                .order_by(StoryClaim.event_at.asc(), StoryClaim.id.asc())
            )
        )
        timelines = list(
            db.scalars(
                select(StoryTimelineEvent)
                .where(StoryTimelineEvent.story_id == selected.story_id)
                .order_by(StoryTimelineEvent.event_at.asc())
            )
        )
        from ..models import ExternalReference

        external = list(
            db.scalars(
                select(ExternalReference)
                .where(ExternalReference.story_id == selected.story_id)
                .order_by(ExternalReference.accessed_at.desc())
            )
        )
        media_rows = article_media(db, selected.id)
        lead = next((row for row in media_rows if row.role == "lead" and row.selected), None)
        supporting = next((row for row in media_rows if row.role == "supporting" and row.selected), None)
        alternatives = rank_media(db, story) if story is not None else []
        if story is not None:
            allowed = {choice.media.id for choice in alternatives}
            alternatives = [choice for choice in alternatives if choice.media.id in allowed][:12]
        history = revision_history(db, selected)
        published_snapshot: dict = {}
        try:
            published_snapshot = json.loads(selected_link.snapshot_json or "{}") if selected_link else {}
        except json.JSONDecodeError:
            published_snapshot = {}
        current_snapshot = article_snapshot(db, selected)
        selected_context = {
            "story": story,
            "evidence_rows": evidence_rows,
            "claims": claims,
            "timelines": timelines,
            "external": external,
            "media_rows": media_rows,
            "lead_media": lead,
            "supporting_media": supporting,
            "alternatives": alternatives,
            "history": history,
            "published_snapshot": published_snapshot,
            "current_snapshot": current_snapshot,
            "current_hash": snapshot_hash(current_snapshot),
            "published_hash": snapshot_hash(published_snapshot) if published_snapshot else "",
            "is_dirty": bool(published_snapshot)
            and snapshot_hash(current_snapshot) != snapshot_hash(published_snapshot),
        }
    epaper_exists = bool(edition.epaper_dir and (Path(edition.epaper_dir) / "index.html").exists())
    pdf_exists = bool(edition.pdf_path and Path(edition.pdf_path).exists())
    working_pdf = edition.preview_pdf_path if edition.preview_pdf_path else edition.pdf_path
    working_pdf_exists = bool(working_pdf and Path(working_pdf).exists())
    page_previews = {row.page_number: row.preview_path for row in pages}
    article_states: dict[int, dict] = {}
    for link, item in rows:
        try:
            frozen = json.loads(link.snapshot_json or "{}")
        except json.JSONDecodeError:
            frozen = {}
        article_states[item.id] = {
            "has_snapshot": bool(frozen),
            "frozen_revision": link.article_revision,
            "dirty": bool(frozen)
            and snapshot_hash(article_snapshot(db, item)) != snapshot_hash(frozen),
        }
    by_article = {item.id: item for _, item in rows}
    page_articles: dict[int, list[Article]] = {}
    for page_row in pages:
        try:
            story_map = json.loads(page_row.story_map_json or "[]")
        except json.JSONDecodeError:
            story_map = []
        seen: set[int] = set()
        linked: list[Article] = []
        for entry in story_map if isinstance(story_map, list) else []:
            try:
                article_id = int(entry.get("article_id") or 0)
            except (AttributeError, TypeError, ValueError):
                continue
            if article_id in by_article and article_id not in seen:
                seen.add(article_id)
                linked.append(by_article[article_id])
        page_articles[page_row.page_number] = linked
    return render(
        request,
        "edition.html",
        _ctx(
            db,
            request,
            user,
            "editions",
            edition=edition,
            channel=channel,
            manifest=manifest,
            pages=pages,
            page_previews=page_previews,
            blockers=blockers,
            held_rows=held_rows,
            job=db.scalar(select(Job).where(Job.edition_id == edition.id).order_by(Job.id.desc())),
            rows=rows,
            articles=[item for _, item in rows],
            selected_link=selected_link,
            selected=selected,
            selected_context=selected_context,
            source_channels=edition_source_channels(db, edition),
            flags=flags,
            family=family,
            fingerprint=fingerprint,
            preview_current=preview_current,
            preview_pages=preview_pages,
            working_pdf_exists=working_pdf_exists,
            epaper_exists=epaper_exists,
            pdf_exists=pdf_exists,
            editorial_statuses=EDITORIAL_STATUSES,
            status_labels=STATUS_LABELS,
            active_page=page,
            article_states=article_states,
            page_articles=page_articles,
        ),
    )


@app.post("/editions/{edition_id}/articles/{article_id}/save")
def edition_article_save(
    edition_id: int,
    article_id: int,
    request: Request,
    title: str = Form(...),
    deck: str = Form(""),
    standfirst: str = Form(""),
    body_text: str = Form(...),
    byline: str = Form(""),
    section: str = Form("national"),
    genre: str = Form("news"),
    expected_revision: str = Form(""),
    lead_media_id: str = Form(""),
    media_caption: str = Form(""),
    media_credit: str = Form(""),
    alt_caption: str = Form(""),
    alt_credit: str = Form(""),
    remove_media: str = Form(""),
    supporting_media_id: str = Form(""),
    supporting_caption: str = Form(""),
    supporting_credit: str = Form(""),
    alt_supporting_caption: str = Form(""),
    alt_supporting_credit: str = Form(""),
    remove_supporting: str = Form(""),
    lock: str = Form(""),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    """Save an article revision and rebuild the edition preview from it."""
    edition = _edition_or_404(db, edition_id, user)
    _channel_or_404(db, edition.channel_id, user, "editor")
    original_edition_id = edition.id
    try:
        if edition.status == "published":
            edition = working_revision(db, edition, actor_id=user.id)
            db.flush()
        link, article = _edition_link_or_404(db, edition, article_id)
        expected = int(expected_revision) if str(expected_revision).strip().isdigit() else article.revision
        save_article_revision(
            db,
            article,
            title=title,
            deck=deck,
            standfirst=standfirst,
            body_text=body_text,
            actor_id=user.id,
            expected_revision=expected,
            byline=byline,
            section=section,
            genre=genre,
            lock=bool(lock) or article.locked,
        )
        story = db.get(StoryCluster, article.story_id)
        if remove_media == "1":
            clear_article_media(db, article_id=article.id)
        elif str(lead_media_id).strip().isdigit() and story is not None:
            apply_media_choice(
                db,
                article_id=article.id,
                story=story,
                media_id=int(lead_media_id),
                target_dir=settings.media_dir,
                caption=(alt_caption or media_caption) or None,
                credit=(alt_credit or media_credit) or None,
            )
        elif media_caption or media_credit:
            update_media_caption(
                db,
                article_id=article.id,
                caption=media_caption,
                credit=media_credit,
            )
        if remove_supporting == "1":
            clear_article_media(db, article_id=article.id, role="supporting")
        elif str(supporting_media_id).strip().isdigit() and story is not None:
            apply_media_choice(
                db,
                article_id=article.id,
                story=story,
                media_id=int(supporting_media_id),
                target_dir=settings.media_dir,
                caption=(alt_supporting_caption or supporting_caption) or None,
                credit=(alt_supporting_credit or supporting_credit) or None,
                role="supporting",
            )
        elif supporting_caption or supporting_credit:
            update_media_caption(
                db,
                article_id=article.id,
                caption=supporting_caption,
                credit=supporting_credit,
                role="supporting",
            )
        from ..editorial.revisions import mark_preview_stale

        mark_preview_stale(db, edition)
        audit(
            db,
            user,
            "article.workspace_save",
            "article",
            article.id,
            {
                "edition": edition.id,
                "revision": article.revision,
                "working_version": edition.version,
            },
        )
        db.commit()
    except RevisionConflict as exc:
        db.rollback()
        return _redirect(
            f"/editions/{original_edition_id}?article={article_id}",
            error=str(exc),
        )
    except ValueError as exc:
        db.rollback()
        return _redirect(
            f"/editions/{original_edition_id}?article={article_id}",
            error=str(exc),
        )
    ok, note = _rerender_preview(db, edition, actor_id=user.id)
    target = f"/editions/{edition.id}?article={article.id}"
    if not ok:
        return _redirect(target, error=note)
    return _redirect(target, f"Saved revision {article.revision}.{note}")


@app.post("/editions/{edition_id}/articles/{article_id}/status")
def edition_article_status(
    edition_id: int,
    article_id: int,
    request: Request,
    status: str = Form(...),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    edition = _edition_or_404(db, edition_id, user)
    _channel_or_404(db, edition.channel_id, user, "editor")
    original_edition_id = edition.id
    if status not in EDITORIAL_STATUSES:
        return _redirect(f"/editions/{edition.id}?article={article_id}", error="Unknown article status.")
    if edition.status == "published":
        edition = working_revision(db, edition, actor_id=user.id)
        db.flush()
    link, article = _edition_link_or_404(db, edition, article_id)
    if status == "approved":
        gate, blockers = publish_gate(db, article.id)
        if not gate:
            db.rollback()
            return _redirect(
                f"/editions/{original_edition_id}?article={article_id}",
                error="Cannot approve while blocking review items remain: " + ", ".join(blockers),
            )
    from ..editorial.revisions import mark_preview_stale

    set_article_status(db, article, status, actor_id=user.id)
    mark_preview_stale(db, edition)
    audit(
        db,
        user,
        "article.status",
        "article",
        article.id,
        {"status": status, "edition": edition.id, "revision": article.revision},
    )
    db.commit()
    ok, note = _rerender_preview(db, edition, actor_id=user.id)
    target = f"/editions/{edition.id}?article={article.id}"
    label = STATUS_LABELS.get(status, status)
    if not ok:
        return _redirect(target, error=note)
    return _redirect(target, f"Article marked {label}.{note}")


@app.post("/editions/{edition_id}/articles/{article_id}/order")
def edition_article_order(
    edition_id: int,
    article_id: int,
    request: Request,
    direction: int = Form(...),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    edition = _edition_or_404(db, edition_id, user)
    _channel_or_404(db, edition.channel_id, user, "editor")
    if edition.status == "published":
        edition = working_revision(db, edition, actor_id=user.id)
        db.flush()
    link, article = _edition_link_or_404(db, edition, article_id)
    reorder_article(db, edition, link, 1 if direction > 0 else -1)
    audit(db, user, "edition.article_order", "edition", edition.id, {"article": article.id, "direction": direction})
    db.commit()
    ok, note = _rerender_preview(db, edition, actor_id=user.id)
    target = f"/editions/{edition.id}?article={article.id}"
    if not ok:
        return _redirect(target, error=note)
    return _redirect(target, f"Article order updated.{note}")


@app.post("/editions/{edition_id}/rename")
def edition_rename(
    edition_id: int,
    request: Request,
    display_name: str = Form(""),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    """Editor-defined edition name; workflow kind stays internal."""
    edition = _edition_or_404(db, edition_id, user)
    _channel_or_404(db, edition.channel_id, user, "editor")
    if edition.status == "published":
        return _redirect(
            f"/editions/{edition.id}",
            error="Published versions keep their released name. Create a new revision to rename it.",
        )
    edition.display_name = display_name.strip()[:160]
    edition.updated_at = utcnow()
    audit(
        db,
        user,
        "edition.rename",
        "edition",
        edition.id,
        {"display_name": edition.display_name},
    )
    db.commit()
    return _redirect(f"/editions/{edition.id}", "Edition name saved.")


@app.post("/editions/{edition_id}/preview")
def edition_preview_refresh(
    edition_id: int,
    request: Request,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    edition = _edition_or_404(db, edition_id, user)
    _channel_or_404(db, edition.channel_id, user, "editor")
    if edition.status == "published":
        edition = working_revision(db, edition, actor_id=user.id)
        db.commit()
        return _redirect(
            f"/editions/{edition.id}",
            f"Version {edition.version} opened as a working draft. Release it when the changes are approved.",
        )
    ok, note = _rerender_preview(db, edition, actor_id=user.id)
    target = f"/editions/{edition.id}"
    if not ok:
        return _redirect(target, error=note)
    return _redirect(target, f"Preview rebuilt from the latest saved revisions.{note}")


@app.post("/editions/{edition_id}/revision")
def edition_create_revision(
    edition_id: int,
    request: Request,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    edition = _edition_or_404(db, edition_id, user)
    _channel_or_404(db, edition.channel_id, user, "editor")
    if edition.status != "published":
        return _redirect(f"/editions/{edition.id}", "This edition is already a working draft.")
    revision = working_revision(db, edition, actor_id=user.id)
    audit(db, user, "edition.revision_open", "edition", revision.id, {"source": edition.id})
    db.commit()
    return _redirect(
        f"/editions/{revision.id}",
        f"Version {revision.version} created from the published version {edition.version}. V{edition.version} remains unchanged.",
    )


@app.get("/editions/{edition_id}/preview/{page_number}")
def edition_preview(
    edition_id: int,
    page_number: int,
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    edition = _edition_or_404(db, edition_id, user)
    page = db.scalar(
        select(EditionPage).where(
            EditionPage.edition_id == edition.id, EditionPage.page_number == page_number
        )
    )
    if page is None or not page.preview_path:
        raise HTTPException(404, "Page preview not found.")
    path = Path(page.preview_path)
    if not path.exists():
        raise HTTPException(404, "Page preview file is missing.")
    return FileResponse(path, media_type="image/png")


@app.get("/editions/{edition_id}/pdf")
def edition_pdf(
    edition_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    edition = _edition_or_404(db, edition_id, user)
    if not edition.pdf_path:
        raise HTTPException(404, "PDF not generated.")
    path = Path(edition.pdf_path)
    if not path.exists():
        raise HTTPException(404, "PDF file is missing.")
    return FileResponse(path, media_type="application/pdf", filename=f"edition-{edition.id}-v{edition.version}.pdf")


@app.get("/editions/{edition_id}/sample_issue.pdf")
def edition_epaper_pdf(
    edition_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    # The e-paper bundle links to ../sample_issue.pdf so a copied folder still works.
    # Serving that exact path keeps the in-app viewer's "Print PDF" link truthful.
    _edition_or_404(db, edition_id, user)
    return RedirectResponse(url=f"/editions/{edition_id}/pdf", status_code=307)


@app.get("/editions/{edition_id}/epaper")
def edition_epaper_redirect(
    edition_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    # The bundle uses relative links so it also works from a copied folder.
    # A trailing slash makes those links resolve inside /epaper/, not one level up.
    _edition_or_404(db, edition_id, user)
    return RedirectResponse(url=f"/editions/{edition_id}/epaper/", status_code=307)


@app.get("/editions/{edition_id}/epaper/")
def edition_epaper_index(
    edition_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    edition = _edition_or_404(db, edition_id, user)
    path = _safe_child(Path(edition.epaper_dir or ""), "index.html")
    return FileResponse(path, media_type="text/html")


@app.get("/editions/{edition_id}/epaper/{asset:path}")
def edition_epaper_asset(
    edition_id: int,
    asset: str,
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    edition = _edition_or_404(db, edition_id, user)
    path = _safe_child(Path(edition.epaper_dir or ""), asset)
    media_type = None
    if path.suffix.lower() == ".css":
        media_type = "text/css"
    elif path.suffix.lower() == ".js":
        media_type = "text/javascript"
    elif path.suffix.lower() == ".jpg":
        media_type = "image/jpeg"
    elif path.suffix.lower() == ".html":
        media_type = "text/html"
    elif path.suffix.lower() == ".json":
        media_type = "application/json"
    return FileResponse(path, media_type=media_type)


@app.post("/editions/{edition_id}/publish")
def edition_publish(
    edition_id: int,
    request: Request,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    edition = _edition_or_404(db, edition_id, user)
    _channel_or_404(db, edition.channel_id, user, "editor")
    if edition.status == "published":
        return _redirect(
            f"/editions/{edition.id}",
            "This version is already published and immutable. Open a working revision to make changes.",
        )
    blockers = edition_review_blockers(db, edition.id)
    rows = _edition_links(db, edition)
    withheld = [
        f"{article.title[:70]} ({STATUS_LABELS.get(article.status, article.status)})"
        for _, article in rows
        if article.status in BLOCKING_STATUSES
    ]
    if withheld:
        blockers.append("Held or awaiting research: " + "; ".join(withheld[:4]))
    if blockers:
        return _redirect(f"/editions/{edition.id}", error="Cannot release: " + "; ".join(blockers[:4]))
    # Release always renders the latest saved revisions. A stale draft bundle is
    # discarded first; a published bundle is never touched.
    purge_unpublished_output(edition)
    db.commit()
    try:
        publish_edition(
            db,
            edition,
            actor_id=user.id,
            mark_published=True,
            allow_quality_warnings=False,
        )
    except Exception as exc:  # noqa: BLE001 - keep the edition editable
        db.rollback()
        edition = db.get(Edition, edition_id) or edition
        edition.preview_state = "failed"
        db.commit()
        return _redirect(
            f"/editions/{edition.id}",
            error=f"Release failed; the edition is still a draft: {type(exc).__name__}: {str(exc)[:300]}",
        )
    commit_edition_window(db, edition)
    audit(
        db,
        user,
        "edition.publish",
        "edition",
        edition.id,
        {
            "version": edition.version,
            "pdf_sha256": (json.loads(edition.manifest_json or "{}").get("pdf", {}) or {}).get("sha256", ""),
        },
    )
    db.commit()
    return _redirect(
        f"/editions/{edition.id}",
        f"Version {edition.version} published as an immutable release. Earlier versions are unchanged.",
    )


@app.post("/editions/{edition_id}/republish")
def edition_republish(
    edition_id: int,
    request: Request,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    """Open the next version from the latest saved revisions.

    This never publishes automatically: it renders a working preview, and the
    configured release action is required before the new version becomes V(n).
    """
    edition = _edition_or_404(db, edition_id, user)
    _channel_or_404(db, edition.channel_id, user, "editor")
    working = working_revision(db, edition, actor_id=user.id)
    audit(db, user, "edition.republish", "edition", working.id, {"source": edition.id, "version": working.version})
    db.commit()
    ok, note = _rerender_preview(db, working, actor_id=user.id)
    target = f"/editions/{working.id}"
    if not ok:
        return _redirect(target, error=note)
    return _redirect(
        target,
        f"Working version {working.version} rendered from the latest saved revisions. Release it to publish.{note}",
    )


# ---------------------------------------------------------------------------
# schedules and jobs
# ---------------------------------------------------------------------------


@app.get("/schedules")
def schedules_page(request: Request, user: User = Depends(current_user), db: Session = Depends(session_dependency)):
    publication = resolve_publication(db, request, user)
    allowed = _scope_channels(db, publication, user)
    if publication is None:
        allowed = [c for c in db.scalars(select(Channel).order_by(Channel.id)) if can_access_channel(db, user, c)]
    rows = []
    for channel in allowed:
        schedules = list(
            db.scalars(
                select(EditionSchedule)
                .where(EditionSchedule.channel_id == channel.id)
                .order_by(EditionSchedule.id)
            )
        )
        for schedule in schedules:
            rows.append(
                {
                    "channel": channel,
                    "schedule": schedule,
                    "next_cutoff": latest_due_cutoff(schedule, utcnow() + timedelta(days=1)),
                }
            )
    return render(request, "schedules.html", _ctx(db, request, user, "schedules", rows=rows, channels=allowed))


@app.post("/schedules/{schedule_id}")
def schedule_update(
    schedule_id: int,
    request: Request,
    cutoff_time: str = Form(...),
    release_time: str = Form(...),
    timezone: str = Form(...),
    weekdays: list[str] = Form(default=[]),
    enabled: str = Form(""),
    requires_review: str = Form(""),
    auto_publish_nonrisk: str = Form(""),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    schedule = db.get(EditionSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(404, "Schedule not found.")
    channel = _channel_or_404(db, schedule.channel_id, user, "editor")
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(timezone)
    except Exception:
        return _redirect("/schedules", error="Unknown timezone.")
    if auto_publish_nonrisk and requires_review:
        return _redirect(
            "/schedules",
            error="Auto-publish for non-risk editions is only allowed when review is not required.",
        )
    schedule.cutoff_time = _validate_clock(cutoff_time, schedule.cutoff_time)
    schedule.release_time = _validate_clock(release_time, schedule.release_time)
    schedule.timezone = timezone
    schedule.weekdays = _parse_weekdays(weekdays)
    schedule.enabled = bool(enabled)
    schedule.requires_review = bool(requires_review)
    schedule.auto_publish_nonrisk = bool(auto_publish_nonrisk)
    schedule.updated_at = utcnow()
    audit(db, user, "schedule.update", "schedule", schedule.id, {"channel": channel.id})
    db.commit()
    return _redirect("/schedules", "Schedule saved.")


@app.post("/schedules/new")
def schedule_create(
    request: Request,
    channel_id: int = Form(...),
    kind: str = Form("daily"),
    cutoff_time: str = Form("05:00"),
    release_time: str = Form("08:00"),
    timezone: str = Form("Asia/Karachi"),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    channel = _channel_or_404(db, channel_id, user, "editor")
    publication = resolve_publication(db, request, user)
    if publication is not None:
        if not can_access_publication(db, user, publication, "editor"):
            raise HTTPException(403, "You do not have editorial access to this publication.")
        linked = {item.id for item in publication_channels(db, publication, enabled_only=False)}
        if linked and channel.id not in linked:
            return _redirect(
                "/schedules", error="That channel is not a source of the selected newspaper."
            )
    if kind not in {"daily", "weekly_roundup", "monthly_roundup"}:
        return _redirect("/schedules", error="Unknown schedule kind.")
    exists = db.scalar(
        select(EditionSchedule).where(
            EditionSchedule.channel_id == channel.id, EditionSchedule.kind == kind
        )
    )
    if exists:
        return _redirect("/schedules", error="That schedule already exists for the channel.")
    schedule = EditionSchedule(
        channel_id=channel.id,
        publication_id=publication.id if publication is not None else None,
        kind=kind,
        timezone=timezone,
        cutoff_time=_validate_clock(cutoff_time, "05:00"),
        release_time=_validate_clock(release_time, "08:00"),
        enabled=False,
        requires_review=True,
    )
    db.add(schedule)
    audit(db, user, "schedule.create", "schedule", channel.id, {"kind": kind})
    db.commit()
    return _redirect("/schedules", "Schedule created. Review its settings, then enable it.")


@app.post("/schedules/{schedule_id}/catchup")
def schedule_catchup(
    schedule_id: int,
    request: Request,
    user: User = Depends(require_role("operator")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    schedule = db.get(EditionSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(404, "Schedule not found.")
    _channel_or_404(db, schedule.channel_id, user, "operator")
    jobs = enqueue_due_jobs(db, max_catchup=3)
    audit(db, user, "schedule.catchup", "schedule", schedule.id, {"jobs": [job.id for job in jobs]})
    db.commit()
    launched = _launch_worker()
    return _redirect("/jobs", f"Enqueued {len(jobs)} due job(s). {launched}")


@app.post("/schedules/{schedule_id}/run")
def schedule_run(
    schedule_id: int,
    request: Request,
    user: User = Depends(require_role("operator")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    schedule = db.get(EditionSchedule, schedule_id)
    if schedule is None:
        raise HTTPException(404, "Schedule not found.")
    channel = _channel_or_404(db, schedule.channel_id, user, "operator")
    publication = (
        db.get(PublicationProfile, schedule.publication_id)
        if schedule.publication_id
        else resolve_publication(db, request, user)
    )
    if publication is not None and not can_access_publication(db, user, publication, "operator"):
        raise HTTPException(403, "You do not have operational access to this publication.")
    sources = (
        publication_channels(db, publication, enabled_only=True)
        if publication is not None
        else [channel]
    )
    if not sources:
        sources = [channel]
    for source in sources:
        if not source.is_demo and active_rights(db, source.id) is None:
            return _redirect(
                "/schedules",
                error=f"Build blocked: {source.title} has no active rights declaration.",
            )
    cutoff = latest_due_cutoff(schedule, utcnow()) or utcnow()
    from ..scheduling.service import previous_enabled_day

    start = local_boundary_utc(
        previous_enabled_day(schedule, to_local(cutoff, schedule.timezone).date()),
        datetime.strptime(schedule.cutoff_time, "%H:%M").time(),
        schedule.timezone,
    )
    job = enqueue_manual_job(
        db,
        channel,
        window_start=start,
        window_end=cutoff,
        kind=schedule.kind if schedule.kind in {"daily", "special"} else "manual",
        actor_id=user.id,
        collect=any(not item.is_demo for item in sources),
        timezone=schedule.timezone,
        cutoff_local=schedule.cutoff_time,
        publication=publication,
        channels=sources,
    )
    audit(
        db,
        user,
        "schedule.run_now",
        "schedule",
        schedule.id,
        {"job": job.id, "sources": [item.id for item in sources]},
    )
    db.commit()
    launched = _launch_worker()
    return _redirect(f"/jobs/{job.id}", f"Build queued. {launched}")


@app.get("/jobs")
def jobs_page(request: Request, user: User = Depends(current_user), db: Session = Depends(session_dependency)):
    publication = resolve_publication(db, request, user)
    allowed = [c.id for c in _scope_channels(db, publication, user)]
    if publication is None:
        allowed = [c.id for c in db.scalars(select(Channel)) if can_access_channel(db, user, c)]
    scope = or_(Job.channel_id.in_(allowed or [-1]), Job.channel_id.is_(None))
    if publication is not None:
        scope = or_(Job.publication_id == publication.id, scope)
    jobs = list(
        db.scalars(
            select(Job)
            .where(scope)
            .order_by(Job.id.desc())
            .limit(100)
        )
    )
    job_progress = {}
    for job in jobs:
        job_progress[job.id] = progress_snapshot(job)
    return render(
        request,
        "jobs.html",
        _ctx(db, request, user, "jobs", jobs=jobs, job_progress=job_progress),
    )


@app.get("/jobs/{job_id}")
def job_detail(
    job_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    if job.channel_id:
        _channel_or_404(db, job.channel_id, user)
    _check_job_access(db, job, user)
    steps = list(db.scalars(select(JobStep).where(JobStep.job_id == job.id).order_by(JobStep.id)))
    result = json.loads(job.result_json or "{}")
    edition = db.get(Edition, job.edition_id) if job.edition_id else None
    return render(
        request,
        "job.html",
        _ctx(
            db,
            request,
            user,
            "jobs",
            job=job,
            progress=progress_snapshot(job),
            steps=steps,
            result=result,
            edition=edition,
            log_dir=str(settings.data_dir / "logs"),
        ),
    )


@app.get("/api/jobs/{job_id}")
def job_api(
    job_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    if job.channel_id:
        _channel_or_404(db, job.channel_id, user)
    _check_job_access(db, job, user)
    result = json.loads(job.result_json or "{}")
    progress = progress_snapshot(job)
    return JSONResponse(
        {
            "id": job.id,
            "status": job.status,
            "stage": job.stage,
            "attempt": job.attempt,
            "error": redact(job.error or ""),
            "edition_id": job.edition_id,
            "progress": progress,
            "result": result,
        }
    )


@app.post("/jobs/{job_id}/retry")
def job_retry(
    job_id: int,
    request: Request,
    user: User = Depends(require_role("operator")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    if job.channel_id:
        _channel_or_404(db, job.channel_id, user, "operator")
    _check_job_access(db, job, user, "operator")
    if job.status not in {"failed_retryable", "failed_terminal"}:
        return _redirect(f"/jobs/{job.id}", error="Retry is only for failed jobs. Use Continue for a paused job.")
    job.status = "scheduled"
    job.stage = "queued"
    job.attempt = 0
    job.error = ""
    job.scheduled_for = utcnow()
    job.finished_at = None
    payload = json.loads(job.result_json or "{}")
    payload["progress"] = {"stage": "queued", "percent": 0, "detail": "Queued for retry."}
    job.result_json = json.dumps(payload, ensure_ascii=False)
    job.lock_owner = ""
    job.lock_expires_at = None
    audit(db, user, "job.retry", "job", job.id)
    db.commit()
    launched = _launch_worker()
    return _redirect(f"/jobs/{job.id}", f"Job rescheduled. {launched}")


@app.post("/jobs/{job_id}/continue")
def job_continue(
    job_id: int,
    request: Request,
    user: User = Depends(require_role("operator")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    if job.channel_id:
        _channel_or_404(db, job.channel_id, user, "operator")
    _check_job_access(db, job, user, "operator")
    if job.status not in {"paused", "resumable"}:
        return _redirect(f"/jobs/{job.id}", error="Continue is only available for paused jobs.")
    job.status = "scheduled"
    job.scheduled_for = utcnow()
    job.finished_at = None
    job.error = ""
    job.lock_owner = ""
    job.lock_expires_at = None
    payload = json.loads(job.result_json or "{}")
    payload["progress"] = {
        "stage": job.stage,
        "percent": progress_snapshot(job)["percent"],
        "detail": "Queued to continue from the saved checkpoint.",
    }
    job.result_json = json.dumps(payload, ensure_ascii=False)
    audit(db, user, "job.continue", "job", job.id)
    db.commit()
    launched = _launch_worker()
    return _redirect(f"/jobs/{job.id}", f"Job continuation queued. {launched}")


@app.post("/jobs/{job_id}/stop")
def job_stop(
    job_id: int,
    request: Request,
    user: User = Depends(require_role("operator")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    if job.channel_id:
        _channel_or_404(db, job.channel_id, user, "operator")
    _check_job_access(db, job, user, "operator")
    if job.status == "running":
        job.status = "stop_requested"
        audit(db, user, "job.stop_requested", "job", job.id)
        db.commit()
        return _redirect(f"/jobs/{job.id}", "Stop requested; the worker will pause at the next safe checkpoint.")
    if job.status in {"scheduled", "failed_retryable", "resumable"}:
        job.status = "paused"
        job.stage = "paused"
        job.finished_at = utcnow()
        job.scheduled_for = None
        audit(db, user, "job.stop", "job", job.id)
        db.commit()
        return _redirect(f"/jobs/{job.id}", "Job paused.")
    return _redirect(f"/jobs/{job.id}", error="This job cannot be stopped in its current state.")


@app.post("/jobs/{job_id}/restart")
def job_restart(
    job_id: int,
    request: Request,
    user: User = Depends(require_role("operator")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    if job.channel_id:
        _channel_or_404(db, job.channel_id, user, "operator")
    _check_job_access(db, job, user, "operator")
    if job.status == "running":
        return _redirect(f"/jobs/{job.id}", error="Stop the running job before restarting it.")
    if job.status in {"published", "completed", "no_content", "awaiting_review"}:
        return _redirect(f"/jobs/{job.id}", error="Completed or reviewed jobs cannot be restarted.")
    job.status = "scheduled"
    job.stage = "queued"
    job.attempt = 0
    job.error = ""
    job.scheduled_for = utcnow()
    job.started_at = None
    job.finished_at = None
    job.heartbeat_at = None
    job.edition_id = None
    job.lock_owner = ""
    job.lock_expires_at = None
    job.result_json = json.dumps(
        {"progress": {"stage": "queued", "percent": 0, "detail": "Queued for a clean restart."}},
        ensure_ascii=False,
    )
    audit(db, user, "job.restart", "job", job.id)
    db.commit()
    launched = _launch_worker()
    return _redirect(f"/jobs/{job.id}", f"Clean restart queued. {launched}")


@app.post("/jobs/{job_id}/cancel")
def job_cancel(
    job_id: int,
    request: Request,
    user: User = Depends(require_role("operator")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    if job.channel_id:
        _channel_or_404(db, job.channel_id, user, "operator")
    _check_job_access(db, job, user, "operator")
    if job.status == "running":
        job.status = "cancel_requested"
        audit(db, user, "job.cancel_requested", "job", job.id)
        db.commit()
        return _redirect(f"/jobs/{job.id}", "Cancellation requested; the worker will stop at a safe checkpoint.")
    if job.status in {"cancel_requested", "canceled"}:
        return _redirect(f"/jobs/{job.id}", "Cancellation is already in progress.")
    if job.status not in {"scheduled", "failed_retryable", "failed_terminal", "paused", "resumable", "stop_requested"}:
        return _redirect(f"/jobs/{job.id}", error="This job cannot be cancelled in its current state.")
    job.status = "canceled"
    job.stage = "canceled"
    job.finished_at = utcnow()
    audit(db, user, "job.cancel", "job", job.id)
    db.commit()
    return _redirect(f"/jobs/{job.id}", "Job cancelled.")


# ---------------------------------------------------------------------------
# branding, users, audit and diagnostics
# ---------------------------------------------------------------------------


@app.post("/publications/select")
def publication_select(
    publication_id: int = Form(...),
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    publication = _publication_or_404(db, publication_id, user)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        PUBLICATION_COOKIE,
        str(publication.id),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 365,
        path="/",
    )
    return response


@app.post("/publications/new")
def publication_create(
    name: str = Form(...),
    city: str = Form(""),
    timezone: str = Form("Asia/Karachi"),
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    """Create another newspaper title. Sources and editors are added explicitly."""
    from zoneinfo import ZoneInfo

    clean_name = name.strip()[:160]
    if not clean_name:
        return _redirect("/branding", error="A publication needs a name.")
    try:
        ZoneInfo(timezone)
    except Exception:
        return _redirect("/branding", error="Unknown timezone.")
    publication = PublicationProfile(
        slug=unique_slug(db, clean_name),
        name=clean_name,
        city=city.strip()[:120],
        timezone=timezone,
        channel_handle="",
        byline="Editorial Desk",
        slogan="A source-transparent roundup",
        footer_text="",
        source_disclosure="",
        accent="#7b1f24",
        paper_size="tabloid",
        map_profile="neutral",
        qr_strategy="appendix",
        issue_volume=1,
        section_order_json=json.dumps(
            ["national", "regional", "international", "defence", "conflict", "analysis", "opinion", "briefs"]
        ),
        is_active=True,
    )
    db.add(publication)
    db.flush()
    db.add(PublicationEditor(publication_id=publication.id, user_id=user.id, role="owner"))
    db.flush()
    audit(db, user, "publication.create", "publication_profile", publication.id, {"name": publication.name})
    db.commit()
    return _redirect("/branding", f"Created {publication.name}. Add its authorized source channels and editors.")


@app.post("/publications/{publication_id}/activate")
def publication_activate(
    publication_id: int,
    active: str = Form(""),
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    publication = _publication_or_404(db, publication_id, user, "owner")
    publication.is_active = bool(active)
    audit(
        db,
        user,
        "publication.activate" if publication.is_active else "publication.deactivate",
        "publication_profile",
        publication.id,
        {"active": publication.is_active},
    )
    db.commit()
    return _redirect("/branding", "Publication availability updated.")


@app.post("/publications/{publication_id}/sources")
def publication_source_save(
    publication_id: int,
    channel_id: int = Form(...),
    priority: int = Form(100),
    weight: str = Form("1"),
    enabled: str = Form(""),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    """Attach an authorized channel or update its priority/weight/enabled state."""
    publication = _publication_or_404(db, publication_id, user, "editor")
    channel = _channel_or_404(db, channel_id, user, "operator")
    try:
        numeric_weight = max(0.1, min(5.0, float(weight or 1)))
    except ValueError:
        return _redirect("/branding", error="Source weight must be a number between 0.1 and 5.")
    link = db.scalar(
        select(PublicationSource).where(
            PublicationSource.publication_id == publication.id,
            PublicationSource.channel_id == channel.id,
        )
    )
    if link is None:
        link = PublicationSource(
            publication_id=publication.id,
            channel_id=channel.id,
            priority=max(1, int(priority)),
            weight=numeric_weight,
            is_enabled=True,
        )
        db.add(link)
    else:
        link.priority = max(1, int(priority))
        link.weight = numeric_weight
    link.is_enabled = bool(enabled)
    audit(
        db,
        user,
        "publication.source",
        "publication_profile",
        publication.id,
        {"channel": channel.id, "priority": link.priority, "weight": link.weight, "enabled": link.is_enabled},
    )
    db.commit()
    return _redirect("/branding", f"{channel.title} linked to {publication.name}.")


@app.post("/publications/{publication_id}/sources/{link_id}/remove")
def publication_source_remove(
    publication_id: int,
    link_id: int,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    publication = _publication_or_404(db, publication_id, user, "editor")
    link = db.get(PublicationSource, link_id)
    if link is None or link.publication_id != publication.id:
        raise HTTPException(404, "Source link not found.")
    if db.scalar(
        select(func.count()).select_from(PublicationSource).where(
            PublicationSource.publication_id == publication.id
        )
    ) <= 1:
        return _redirect("/branding", error="A publication keeps at least one source channel. Disable it instead.")
    channel_id = link.channel_id
    db.delete(link)
    audit(db, user, "publication.source_remove", "publication_profile", publication.id, {"channel": channel_id})
    db.commit()
    return _redirect("/branding", "Source removed from this publication. Telegram access is unchanged.")


@app.post("/publications/{publication_id}/editors")
def publication_editor_add(
    publication_id: int,
    username: str = Form(...),
    role: str = Form("editor"),
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    publication = _publication_or_404(db, publication_id, user, "owner")
    target = db.scalar(select(User).where(User.username == username.strip().lower()))
    if target is None:
        return _redirect("/branding", error="No user has that username.")
    if role not in {"editor", "operator", "viewer"}:
        return _redirect("/branding", error="Publication role must be editor, operator or viewer.")
    if target.role == "owner":
        return _redirect("/branding", error="Owners already manage every publication.")
    membership = db.scalar(
        select(PublicationEditor).where(
            PublicationEditor.publication_id == publication.id,
            PublicationEditor.user_id == target.id,
        )
    )
    if membership is None:
        membership = PublicationEditor(
            publication_id=publication.id, user_id=target.id, role=role
        )
        db.add(membership)
    else:
        membership.role = role
    audit(
        db,
        user,
        "publication.editor",
        "publication_profile",
        publication.id,
        {"user": target.id, "role": role},
    )
    db.commit()
    return _redirect("/branding", f"{target.display_name} now edits {publication.name}.")


@app.post("/publications/{publication_id}/editors/{membership_id}/remove")
def publication_editor_remove(
    publication_id: int,
    membership_id: int,
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    publication = _publication_or_404(db, publication_id, user, "owner")
    membership = db.get(PublicationEditor, membership_id)
    if membership is None or membership.publication_id != publication.id:
        raise HTTPException(404, "Editor membership not found.")
    db.delete(membership)
    audit(
        db,
        user,
        "publication.editor_remove",
        "publication_profile",
        publication.id,
        {"user": membership.user_id},
    )
    db.commit()
    return _redirect("/branding", "Editor access removed.")


@app.get("/branding")
def branding_page(request: Request, user: User = Depends(current_user), db: Session = Depends(session_dependency)):
    maps = list(db.scalars(select(CartographicProfile).order_by(CartographicProfile.id)))
    publication = resolve_publication(db, request, user)
    links: list[dict] = []
    editors: list[dict] = []
    available_channels: list[Channel] = []
    available_users: list[User] = []
    if publication is not None and can_access_publication(db, user, publication):
        source_rows = db.execute(
            select(PublicationSource, Channel)
            .join(Channel, PublicationSource.channel_id == Channel.id)
            .where(PublicationSource.publication_id == publication.id)
            .order_by(PublicationSource.priority.asc(), PublicationSource.id.asc())
        ).all()
        links = [{"link": link, "channel": channel} for link, channel in source_rows]
        linked_channels = {channel.id for _link, channel in source_rows}
        available_channels = [
            channel
            for channel in db.scalars(select(Channel).order_by(Channel.id))
            if channel.id not in linked_channels and can_access_channel(db, user, channel, "operator")
        ]
        editor_rows = db.execute(
            select(PublicationEditor, User)
            .join(User, PublicationEditor.user_id == User.id)
            .where(PublicationEditor.publication_id == publication.id)
            .order_by(PublicationEditor.id.asc())
        ).all()
        editors = [{"membership": membership, "user": member} for membership, member in editor_rows]
        if user.role == "owner":
            available_users = [
                member
                for member in db.scalars(select(User).order_by(User.username))
                if member.role != "owner" and member.id not in {row["user"].id for row in editors}
            ]
    return render(
        request,
        "branding.html",
        _ctx(
            db,
            request,
            user,
            "branding",
            maps=maps,
            source_links=links,
            publication_editors=editors,
            available_channels=available_channels,
            available_users=available_users,
            can_manage_publication=bool(
                publication is not None
                and can_access_publication(db, user, publication, "editor")
            ),
        ),
    )


@app.post("/branding")
def branding_save(
    request: Request,
    name: str = Form(...),
    channel_handle: str = Form(""),
    byline: str = Form("Editorial Desk"),
    source_disclosure: str = Form(""),
    slogan: str = Form(""),
    show_slogan: str = Form(""),
    city: str = Form(""),
    timezone: str = Form("Asia/Karachi"),
    accent: str = Form("#7b1f24"),
    paper_size: str = Form("tabloid"),
    contact_url: str = Form(""),
    public_base_url: str = Form(""),
    public_base_url_verified: str = Form(""),
    map_profile: str = Form("neutral"),
    qr_strategy: str = Form("story"),
    show_chronology: str = Form("auto"),
    research_policy: str = Form("editor_triggered"),
    research_provider: str = Form("auto"),
    research_queries_per_story: int = Form(3),
    research_max_per_story: int = Form(5),
    research_auto_accept: str = Form("review"),
    issue_volume: int = Form(1),
    section_order: list[str] = Form(default=[]),
    allow_closing_light: str = Form(""),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    profile = resolve_publication(db, request, user)
    if profile is None:
        raise HTTPException(404, "Publication profile missing.")
    if not can_access_publication(db, user, profile, "editor"):
        raise HTTPException(403, "You do not have editorial access to this publication.")
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(timezone)
    except Exception:
        return _redirect("/branding", error="Unknown timezone.")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        return _redirect("/branding", error="Accent must be a six-digit hex colour.")
    if paper_size not in {"tabloid", "a3"}:
        return _redirect("/branding", error="Paper size must be tabloid or a3.")
    if db.scalar(select(CartographicProfile).where(CartographicProfile.key == map_profile)) is None:
        return _redirect("/branding", error="Unknown cartographic profile.")
    if qr_strategy not in {"story", "verified_only", "none"}:
        return _redirect("/branding", error="Unknown QR strategy.")
    if show_chronology not in {"auto", "on", "off"}:
        return _redirect("/branding", error="Chronology must be auto, on or off.")
    if research_policy not in {"off", "editor_triggered", "automatic_substantial", "automatic_all"}:
        return _redirect("/branding", error="Unknown research policy.")
    if research_provider not in {"auto", "google_cse", "brave", "gdelt", "rss"}:
        return _redirect("/branding", error="Unknown search provider.")
    if research_auto_accept not in {"review", "auto_context"}:
        return _redirect("/branding", error="Unknown research acceptance policy.")
    if public_base_url.strip():
        try:
            base = normalise_url(public_base_url.strip())
        except ResearchError as exc:
            return _redirect("/branding", error=str(exc))
        profile.public_base_url = base[:500]
    else:
        profile.public_base_url = ""
        profile.public_base_url_verified = False
    profile.name = name.strip()[:160]
    profile.channel_handle = channel_handle.strip()[:120]
    profile.byline = (byline.strip()[:120] or "Editorial Desk")
    profile.source_disclosure = source_disclosure.strip()[:600]
    profile.slogan = slogan.strip()[:240]
    profile.show_slogan = bool(show_slogan)
    profile.city = city.strip()[:120]
    profile.timezone = timezone
    profile.accent = accent
    profile.paper_size = paper_size
    profile.contact_url = contact_url.strip()[:500]
    profile.public_base_url_verified = bool(public_base_url_verified and profile.public_base_url)
    profile.map_profile = map_profile
    profile.qr_strategy = qr_strategy
    profile.show_chronology = show_chronology
    profile.research_policy = research_policy
    profile.research_provider = research_provider
    profile.research_queries_per_story = max(1, min(5, int(research_queries_per_story)))
    profile.research_max_per_story = max(1, min(10, int(research_max_per_story)))
    profile.research_auto_accept = research_auto_accept
    profile.issue_volume = max(1, int(issue_volume))
    ordered = [value for value in section_order if value in dict(SECTIONS)]
    profile.section_order_json = json.dumps(ordered or ["national", "regional", "international", "defence", "conflict", "analysis", "opinion", "briefs"])
    profile.allow_closing_light = bool(allow_closing_light)
    profile.updated_at = utcnow()
    audit(db, user, "branding.update", "publication_profile", profile.id, {"name": profile.name})
    db.commit()
    return _redirect("/branding", "Publication profile saved.")


# ---------------------------------------------------------------------------
# print appearance: palette, fonts and real proofs
# ---------------------------------------------------------------------------


def _appearance_publication(db: Session, request: Request, user: User) -> PublicationProfile:
    publication = resolve_publication(db, request, user)
    if publication is None:
        raise HTTPException(404, "Publication profile missing.")
    if not can_access_publication(db, user, publication, "editor"):
        raise HTTPException(403, "You do not have editorial access to this publication.")
    return publication


def _font_choice(value: str, fallback: dict) -> dict:
    raw = (value or "").strip()
    if not raw:
        return dict(fallback)
    if raw.startswith("asset:"):
        try:
            asset_id = int(raw.split(":", 1)[1])
        except (TypeError, ValueError) as exc:
            raise ThemeError("Invalid imported font selection.") from exc
        return {"asset_id": asset_id}
    if raw.startswith("system:"):
        return {"family": raw.split(":", 1)[1], "asset_id": None}
    return {"family": raw, "asset_id": None}


def _appearance_payload_from_form(form, base: dict) -> dict:
    payload = {
        "colors": dict(base["colors"]),
        "fonts": {key: dict(value) for key, value in base["fonts"].items()},
        "metrics": dict(base["metrics"]),
        "sections": dict(base.get("sections") or {}),
    }
    for item in COLOR_FIELDS:
        key = item["key"]
        value = str(form.get(f"color_{key}") or "").strip()
        if value:
            payload["colors"][key] = value
    assets = {}
    for role in FONT_ROLES:
        key = role["key"]
        current = payload["fonts"][key]
        choice = _font_choice(str(form.get(f"font_{key}") or ""), current)
        if "asset_id" in choice and choice.get("asset_id") is not None:
            current["asset_id"] = choice["asset_id"]
            if choice.get("family"):
                current["family"] = choice["family"]
        elif choice.get("family"):
            current["family"] = choice["family"]
            current["asset_id"] = None
        try:
            weight = int(str(form.get(f"weight_{key}") or current["weight"]))
        except (TypeError, ValueError) as exc:
            raise ThemeError(f"{role['label']}: font weight must be a number.") from exc
        current["weight"] = weight
        style = str(form.get(f"style_{key}") or current["style"]).lower()
        if style not in {"normal", "italic"}:
            raise ThemeError(f"{role['label']}: font style must be normal or italic.")
        current["style"] = style
    for key in payload["metrics"]:
        raw = str(form.get(f"metric_{key}") or "").strip()
        if raw:
            payload["metrics"][key] = raw
    for section in SECTION_ORDER:
        raw = str(form.get(f"section_{section}") or "").strip()
        if raw:
            payload["sections"][section] = raw
        else:
            payload["sections"].pop(section, None)
    return normalise_payload(payload)


def _validate_font_assets(db: Session, payload: dict) -> None:
    available = {asset.id: asset for asset in available_font_assets(db)}
    for role in FONT_ROLES:
        selection = payload["fonts"][role["key"]]
        asset_id = selection.get("asset_id")
        if asset_id is None:
            continue
        asset = available.get(int(asset_id))
        if asset is None:
            raise ThemeError(f"{role['label']}: the selected imported font no longer exists.")
        selection["family"] = asset.family
        selection["weight"] = int(asset.weight or selection["weight"])
        selection["style"] = asset.style or selection["style"]


def _appearance_font_options(db: Session, payload: dict) -> list[dict]:
    assets = available_font_assets(db)
    options: list[dict] = []
    seen: set[str] = set()
    for entry in font_catalog(assets):
        if entry.source == "asset":
            for face in entry.faces:
                if face.asset_id is None:
                    continue
                value = f"asset:{face.asset_id}"
                label = f"{entry.family} — imported {face.weight} {face.style}"
                options.append(
                    {
                        "value": value,
                        "label": label,
                        "group": "imported",
                        "unavailable": face.unavailable,
                    }
                )
                seen.add(value)
        else:
            value = f"system:{entry.family}"
            if value in seen:
                continue
            options.append(
                {
                    "value": value,
                    "label": f"{entry.family} — installed",
                    "group": "installed",
                    "unavailable": all(face.unavailable for face in entry.faces),
                }
            )
            seen.add(value)
    for role in FONT_ROLES:
        selection = payload["fonts"][role["key"]]
        asset_id = selection.get("asset_id")
        value = f"asset:{asset_id}" if asset_id else f"system:{selection['family']}"
        if value not in seen:
            options.append(
                {
                    "value": value,
                    "label": f"{selection['family']} — not currently available",
                    "group": "missing",
                    "unavailable": True,
                }
            )
            seen.add(value)
    return options


def _appearance_roles(db: Session, payload: dict) -> list[dict]:
    described = describe_theme(payload, available_font_assets(db))
    options = _appearance_font_options(db, payload)
    roles = []
    for role in described["roles"]:
        selection = role["selection"]
        selected = (
            f"asset:{selection['asset_id']}"
            if selection.get("asset_id")
            else f"system:{selection['family']}"
        )
        roles.append({**role, "selected": selected, "options": options})
    return roles


@app.get("/appearance")
def appearance_page(
    request: Request,
    proof: str = "",
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
):
    publication = _appearance_publication(db, request, user)
    theme = current_theme(db, publication)
    db.commit()
    payload = theme_payload(theme)
    assets = available_font_assets(db)
    revisions = list(
        db.scalars(
            select(ThemeRevision)
            .where(ThemeRevision.publication_id == publication.id)
            .order_by(ThemeRevision.revision.desc())
        )
    )
    proof_file = ""
    from ..layout.proof import proof_dir

    if re.fullmatch(r"theme-proof-[a-f0-9]{16}\.png", proof or ""):
        candidate = proof_dir(publication) / proof
        if candidate.is_file():
            proof_file = proof
    return render(
        request,
        "appearance.html",
        _ctx(
            db,
            request,
            user,
            "appearance",
            publication=publication,
            theme=theme,
            payload=payload,
            colors=COLOR_FIELDS,
            roles=_appearance_roles(db, payload),
            metrics={key: payload["metrics"][key] for key in payload["metrics"]},
            metric_limits=METRIC_LIMITS,
            warnings=[*contrast_warnings(payload), *describe_theme(payload, assets)["warnings"]],
            presets=PRESETS,
            revisions=revisions,
            assets=assets,
            proof_file=proof_file,
            section_order=[(key, label) for key, label in SECTIONS if key != "front"],
            section_accents=payload.get("sections") or {},
        ),
    )


@app.post("/appearance")
async def appearance_save(
    request: Request,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    publication = _appearance_publication(db, request, user)
    form = await request.form()
    base = theme_payload(current_theme(db, publication))
    try:
        payload = _appearance_payload_from_form(form, base)
        _validate_font_assets(db, payload)
        name = str(form.get("theme_name") or "").strip()[:160]
        row = save_theme(
            db,
            publication,
            payload,
            actor_id=user.id,
            name=name,
            notes=str(form.get("theme_notes") or "").strip()[:400],
        )
        audit(
            db,
            user,
            "theme.save",
            "theme_revision",
            row.id,
            {"publication": publication.id, "revision": row.revision, "hash": payload_hash(payload)},
        )
        db.commit()
    except ThemeError as exc:
        db.rollback()
        return _redirect("/appearance", error=str(exc))
    return _redirect(
        "/appearance",
        f"Saved theme revision {row.revision}. Unpublished previews are now marked stale.",
    )


@app.post("/appearance/preset")
async def appearance_preset(
    request: Request,
    preset: str = Form(...),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    publication = _appearance_publication(db, request, user)
    chosen = theme_preset(preset)
    if chosen is None:
        return _redirect("/appearance", error="Unknown palette preset.")
    base = theme_payload(current_theme(db, publication))
    payload = {**base, "colors": {**base["colors"], **chosen["colors"]}}
    try:
        row = save_theme(
            db,
            publication,
            normalise_payload(payload),
            actor_id=user.id,
            name=f"{publication.name} — {chosen['name']}",
            notes=chosen["description"],
        )
        audit(
            db,
            user,
            "theme.preset",
            "theme_revision",
            row.id,
            {"preset": preset, "revision": row.revision},
        )
        db.commit()
    except ThemeError as exc:
        db.rollback()
        return _redirect("/appearance", error=str(exc))
    return _redirect("/appearance", f"Applied the {chosen['name']} palette as revision {row.revision}.")


@app.post("/appearance/reset")
def appearance_reset(
    request: Request,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    publication = _appearance_publication(db, request, user)
    try:
        row = save_theme(
            db,
            publication,
            default_payload(),
            actor_id=user.id,
            name=f"{publication.name} standard",
            notes="Reset to the standard print theme.",
        )
        audit(
            db,
            user,
            "theme.reset",
            "theme_revision",
            row.id,
            {"publication": publication.id, "revision": row.revision},
        )
        db.commit()
    except ThemeError as exc:
        db.rollback()
        return _redirect("/appearance", error=str(exc))
    return _redirect("/appearance", f"Reset to the standard theme as revision {row.revision}.")


@app.post("/appearance/proof")
async def appearance_proof(
    request: Request,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    publication = _appearance_publication(db, request, user)
    form = await request.form()
    base = theme_payload(current_theme(db, publication))
    try:
        payload = _appearance_payload_from_form(form, base)
        _validate_font_assets(db, payload)
        from starlette.concurrency import run_in_threadpool

        from ..layout.proof import render_theme_proof

        # Playwright's synchronous API cannot run inside the request's asyncio
        # loop, so the renderer runs in a worker thread.
        image_path, theme = await run_in_threadpool(
            render_theme_proof, db, publication, payload=payload
        )
        audit(
            db,
            user,
            "theme.proof",
            "publication_profile",
            publication.id,
            {"theme": theme.hash, "file": image_path.name},
        )
        db.commit()
    except ThemeError as exc:
        db.rollback()
        return _redirect("/appearance", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - report the renderer failure honestly
        db.rollback()
        return _redirect("/appearance", error=f"The print proof could not be rendered: {exc}")
    return _redirect(
        f"/appearance?proof={image_path.name}",
        "Print proof rendered from the current form values.",
    )


@app.get("/appearance/proof/{filename}")
def appearance_proof_image(
    filename: str,
    request: Request,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
):
    publication = _appearance_publication(db, request, user)
    if not re.fullmatch(r"theme-proof-[a-f0-9]{16}\.png", filename):
        raise HTTPException(404, "Proof not found.")
    from ..layout.proof import proof_dir

    path = proof_dir(publication) / filename
    if not path.is_file():
        raise HTTPException(404, "Proof not found.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/appearance/fonts")
async def appearance_font_upload(
    request: Request,
    file: UploadFile = File(...),
    license_note: str = Form(""),
    license_acknowledged: str = Form(""),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    publication = _appearance_publication(db, request, user)
    data = await file.read(MAX_FONT_BYTES + 1)
    if len(data) > MAX_FONT_BYTES:
        return _redirect("/appearance", error="Font files must be at most 12 MB.")
    try:
        metadata = import_font(
            data=data,
            filename=file.filename or "font.ttf",
            license_note=license_note,
            license_acknowledged=license_acknowledged in {"1", "true", "on"},
            uploaded_by=user.id,
        )
    except FontError as exc:
        return _redirect("/appearance", error=str(exc))
    existing = db.scalar(select(FontAsset).where(FontAsset.sha256 == metadata["sha256"]))
    if existing is not None:
        return _redirect(
            "/appearance",
            message=f"{existing.family} is already in the font library.",
        )
    asset = FontAsset(**metadata)
    db.add(asset)
    audit(
        db,
        user,
        "theme.font_import",
        "publication_profile",
        publication.id,
        {"family": metadata["family"], "sha256": metadata["sha256"]},
    )
    db.commit()
    return _redirect(
        "/appearance",
        f"Imported {asset.family} {asset.weight} {asset.style}. Select it for a font role.",
    )


@app.post("/appearance/fonts/{font_id}/delete")
def appearance_font_delete(
    font_id: int,
    request: Request,
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    publication = _appearance_publication(db, request, user)
    asset = db.get(FontAsset, font_id)
    if asset is None:
        raise HTTPException(404, "Font asset not found.")
    used_by = fonts_used_by_themes(db, asset.id)
    if used_by:
        return _redirect(
            "/appearance",
            error="This font is referenced by a saved theme revision. Keep it, or save a theme that no longer uses it before deleting.",
        )
    stored_name = asset.stored_name
    db.delete(asset)
    audit(
        db,
        user,
        "theme.font_delete",
        "publication_profile",
        publication.id,
        {"family": asset.family, "sha256": asset.sha256},
    )
    db.commit()
    target = settings.fonts_dir / stored_name
    if target.is_file() and target.parent.resolve() == settings.fonts_dir.resolve():
        target.unlink(missing_ok=True)
    return _redirect("/appearance", f"Removed {asset.family} from the font library.")


@app.get("/users")
def users_page(request: Request, user: User = Depends(require_role("owner")), db: Session = Depends(session_dependency)):
    users = list(db.scalars(select(User).order_by(User.id)))
    channels = list(db.scalars(select(Channel).order_by(Channel.id)))
    return render(request, "users.html", _ctx(db, request, user, "users", users=users, channels=channels))


@app.post("/users")
def user_create(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(""),
    role: str = Form("viewer"),
    password: str = Form(...),
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    username = username.strip().lower()
    if role not in ROLE_RANK:
        return _redirect("/users", error="Unknown role.")
    if db.scalar(select(User).where(User.username == username)):
        return _redirect("/users", error="Username already exists.")
    try:
        record = User(
            username=username,
            password_hash=hash_password(password),
            display_name=display_name.strip() or username,
            role=role,
            must_change_password=True,
        )
    except ValueError as exc:
        return _redirect("/users", error=str(exc))
    db.add(record)
    db.flush()
    audit(db, user, "user.create", "user", record.id, {"role": role})
    db.commit()
    return _redirect("/users", f"User '{record.username}' created.")


@app.post("/users/{user_id}/role")
def user_role(
    user_id: int,
    request: Request,
    role: str = Form(...),
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    record = db.get(User, user_id)
    if record is None:
        raise HTTPException(404, "User not found.")
    if role not in ROLE_RANK:
        return _redirect("/users", error="Unknown role.")
    owners = db.scalar(select(func.count()).select_from(User).where(User.role == "owner", User.is_active.is_(True)))
    if record.role == "owner" and role != "owner" and (owners or 0) <= 1:
        return _redirect("/users", error="The last active owner cannot be demoted.")
    record.role = role
    audit(db, user, "user.role", "user", record.id, {"role": role})
    db.commit()
    return _redirect("/users", "Role updated.")


@app.post("/users/{user_id}/active")
def user_active(
    user_id: int,
    request: Request,
    active: str = Form(""),
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    record = db.get(User, user_id)
    if record is None:
        raise HTTPException(404, "User not found.")
    if record.id == user.id and not active:
        return _redirect("/users", error="You cannot deactivate your own account.")
    record.is_active = bool(active)
    audit(db, user, "user.active", "user", record.id, {"active": record.is_active})
    db.commit()
    return _redirect("/users", "User status updated.")


@app.post("/users/{user_id}/password")
def user_password(
    user_id: int,
    request: Request,
    password: str = Form(...),
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    record = db.get(User, user_id)
    if record is None:
        raise HTTPException(404, "User not found.")
    try:
        record.password_hash = hash_password(password)
    except ValueError as exc:
        return _redirect("/users", error=str(exc))
    record.must_change_password = True
    audit(db, user, "user.password_reset", "user", record.id)
    db.commit()
    return _redirect("/users", "Password reset; the user must change it after sign-in.")


@app.get("/audit")
def audit_page(request: Request, user: User = Depends(require_role("editor")), db: Session = Depends(session_dependency)):
    events = list(db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(300)))
    return render(request, "audit.html", _ctx(db, request, user, "audit", events=events))


@app.get("/diagnostics")
def diagnostics_page(request: Request, user: User = Depends(require_role("owner")), db: Session = Depends(session_dependency)):
    counts = {}
    for label, model in (
        ("users", User),
        ("channels", Channel),
        ("posts", SourcePost),
        ("stories", StoryCluster),
        ("articles", Article),
        ("editions", Edition),
        ("jobs", Job),
        ("flags", ReviewFlag),
    ):
        counts[label] = db.scalar(select(func.count()).select_from(model)) or 0
    report = build_preflight(probe_ollama=True)
    checks = [(check.label, check.detail, check.status == "ok", check.action) for check in report.checks]
    pending = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.status.in_(["scheduled", "running", "failed_retryable"]))
    ) or 0
    return render(
        request,
        "diagnostics.html",
        _ctx(
            db,
            request,
            user,
            "diagnostics",
            counts=counts,
            checks=checks,
            telegram=report.telegram,
            ollama=report.ollama,
            pending_jobs=int(pending),
            env_file=str(settings.env_file),
            session_file=str(settings.telegram_session_file),
        ),
    )


@app.exception_handler(HTTPException)
def http_error(request: Request, exc: HTTPException):
    if exc.status_code in {301, 302, 303, 307, 308} and exc.headers and "Location" in exc.headers:
        return RedirectResponse(exc.headers["Location"], status_code=exc.status_code)
    try:
        with SessionLocal() as db:
            user = get_session_user(request, db)
            if user:
                context = _ctx(db, request, user, "")
            else:
                context = {"request": request, "user": None, "csrf": "", "active": ""}
    except Exception:
        context = {"request": request, "user": None, "csrf": "", "active": ""}
    return render(
        request,
        "error.html",
        {**context, "code": exc.status_code, "detail": exc.detail},
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    """Log the full traceback and return a safe page with a reference id.

    The operator-facing message never contains exception text, form values or
    session material; the traceback goes to ``data/logs/web-YYYYMMDD.log``.
    """
    reference = uuid.uuid4().hex[:12]
    log_path = configure_logging("web")
    user_id = None
    context: dict = {
        "request": request,
        "user": None,
        "csrf": "",
        "active": "",
        "profile": None,
        "can": {"operate": False, "edit": False, "own": False},
    }
    try:
        with SessionLocal() as db:
            user = get_session_user(request, db)
            user_id = user.id if user else None
            if user:
                context = _ctx(db, request, user, "")
    except Exception:  # noqa: BLE001 - the error page must render even if the DB is down
        pass
    logger.error(
        "unhandled_exception",
        extra={
            "event": "unhandled_exception",
            "request_id": reference,
            "method": request.method,
            "path": request.url.path,
            "user_id": user_id,
        },
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return render(
        request,
        "error.html",
        {
            **context,
            "code": 500,
            "detail": (
                "An unexpected error occurred. Reference "
                f"{reference}. The full traceback was written to {log_path}."
            ),
        },
        status_code=500,
    )
def _health_rows(
    db: Session,
    report,
    publication: PublicationProfile | None,
    channel: Channel | None,
    telegram_result: dict | None,
    telegram_error: str,
) -> list[dict]:
    """Readable status rows. No PIDs, raw JSON, paths or credentials."""
    from zoneinfo import ZoneInfo

    rows: list[dict] = [
        {
            "label": "Application",
            "state": "ready",
            "state_label": "Ready",
            "detail": f"Web server responding · {settings.app_name}",
            "action_url": "",
            "action": "",
        }
    ]
    database = next((check for check in report.checks if check.key == "database"), None)
    if database is not None:
        rows.append(
            {
                "label": "Database",
                "state": "ready" if database.status == "ok" else "attention",
                "state_label": "Ready" if database.status == "ok" else "Attention required",
                "detail": database.detail,
                "action_url": "",
                "action": database.action,
            }
        )

    if telegram_error:
        rows.append(
            {
                "label": "Telegram account",
                "state": "not_connected",
                "state_label": "Not connected",
                "detail": telegram_error,
                "action_url": f"/sources/{channel.id}/telegram" if channel else "/sources",
                "action": "Open Telegram connection",
            }
        )
    elif telegram_result:
        rows.append(
            {
                "label": "Telegram account",
                "state": "ready",
                "state_label": "Connected",
                "detail": f"{telegram_result.get('title') or 'Account'} · session verified just now",
                "action_url": "",
                "action": "",
            }
        )
    elif report.telegram.get("session_present"):
        rows.append(
            {
                "label": "Telegram account",
                "state": "degraded",
                "state_label": "Session found",
                "detail": "A local session exists but was not contacted in this check.",
                "action_url": f"/sources/{channel.id}/telegram" if channel else "/sources",
                "action": "Test source",
            }
        )
    else:
        rows.append(
            {
                "label": "Telegram account",
                "state": "not_connected",
                "state_label": "Not connected",
                "detail": "No authorized Telegram session is available; fixtures and imports still work.",
                "action_url": f"/sources/{channel.id}/telegram" if channel else "/sources",
                "action": "Connect Telegram",
            }
        )

    if channel is None:
        rows.append(
            {
                "label": "Selected source channel",
                "state": "not_connected",
                "state_label": "Not selected",
                "detail": "No source channel is linked to this newspaper.",
                "action_url": "/sources",
                "action": "Choose a source",
            }
        )
    else:
        rights = active_rights(db, channel.id)
        rows.append(
            {
                "label": "Selected source channel",
                "state": "ready" if rights else "attention",
                "state_label": "Ready" if rights else "Attention required",
                "detail": (
                    f"{channel.title} ({channel.handle}) · content rights recorded"
                    if rights
                    else f"{channel.title} ({channel.handle}) · no active content-rights grant"
                ),
                "action_url": "" if rights else f"/sources/{channel.id}",
                "action": "" if rights else "Record authorization",
            }
        )

    ollama = report.ollama or {}
    if ollama.get("model_present"):
        rows.append(
            {
                "label": "Local AI",
                "state": "ready",
                "state_label": "Ready",
                "detail": f"{settings.ollama_model} responded",
                "action_url": "",
                "action": "",
            }
        )
    else:
        rows.append(
            {
                "label": "Local AI",
                "state": "not_connected" if not ollama.get("reachable") else "attention",
                "state_label": "Not connected" if not ollama.get("reachable") else "Attention required",
                "detail": ollama.get("detail") or "Ollama is not reachable.",
                "action_url": "/diagnostics",
                "action": "Open diagnostics",
            }
        )
    rows.append(
        {
            "label": "Configured model",
            "state": "ready" if ollama.get("model_present") else "degraded",
            "state_label": settings.ollama_model,
            "detail": "Installed and available for drafting." if ollama.get("model_present") else "Not confirmed installed.",
            "action_url": "",
            "action": "",
        }
    )

    running = db.scalar(select(func.count()).select_from(Job).where(Job.status == "running")) or 0
    queued = db.scalar(
        select(func.count()).select_from(Job).where(Job.status.in_(["scheduled", "failed_retryable"]))
    ) or 0
    failed = db.scalar(select(func.count()).select_from(Job).where(Job.status == "failed_terminal")) or 0
    if failed:
        worker_state, worker_label = "attention", "Attention required"
    elif running:
        worker_state, worker_label = "ready", "Working"
    elif queued:
        worker_state, worker_label = "ready", "Queued"
    else:
        worker_state, worker_label = "paused", "Idle"
    rows.append(
        {
            "label": "Edition worker",
            "state": worker_state,
            "state_label": worker_label,
            "detail": (
                f"{running} running · {queued} queued · {failed} failed"
                if (running or queued or failed)
                else "No active jobs"
            ),
            "action_url": "/jobs",
            "action": "Open jobs",
        }
    )

    search = provider_status(publication.research_provider if publication else "auto")
    rows.append(
        {
            "label": "Source discovery",
            "state": "ready" if search.get("status") == "ready" else "attention",
            "state_label": "Ready" if search.get("status") == "ready" else "Unavailable",
            "detail": (
                f"{search.get('label') or search.get('provider')} · policy "
                f"{(publication.research_policy if publication else 'editor_triggered').replace('_', ' ')}"
                if search.get("status") == "ready"
                else search.get("detail", "Configure a provider or add a URL manually.")
            ),
            "action_url": "/branding",
            "action": "Research settings",
        }
    )
    timezone = publication.timezone if publication else settings.timezone
    checked = datetime.now(ZoneInfo(timezone)).strftime("%d %b %Y, %I:%M %p")
    for row in rows:
        row["checked"] = checked
    return rows


@app.post("/system/check")
def system_check(
    request: Request,
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    """Browser action: a readable status view, never a raw JSON dump."""
    report = build_preflight(probe_ollama=True)
    publication = resolve_publication(db, request, user)
    channels = publication_channels(db, publication) if publication else []
    channel = channels[0] if channels else db.scalar(
        select(Channel).where(Channel.is_demo.is_(False)).limit(1)
    )
    telegram_result: dict | None = None
    telegram_error = ""
    if channel and active_rights(db, channel.id):
        db.commit()
        try:
            telegram_result = telegram_service.preflight(
                channel.handle, expected_id=str(channel.telegram_channel_id or "")
            )
        except TelegramTransportError as exc:
            telegram_error = str(exc)
        except Exception as exc:  # a transport crash must not hide the rest
            telegram_error = f"Telegram check failed: {type(exc).__name__}"
    rows = _health_rows(db, report, publication, channel, telegram_result, telegram_error)
    template = _ENV.get_template("health.html.j2")
    return HTMLResponse(
        template.render(
            request=request,
            rows=rows,
            checked_at=rows[0]["checked"] if rows else "",
        )
    )


@app.post("/system/check.json")
def system_check_json(
    user: User = Depends(require_role("owner")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    """Programmatic health endpoint retained for scripts and diagnostics."""
    from ..operations import health

    result = health()
    channel = db.scalar(select(Channel).where(Channel.is_demo.is_(False)).limit(1))
    result["web"] = "Connected and tested"
    if channel and active_rights(db, channel.id):
        db.commit()
        try:
            result["source_channel"] = telegram_service.preflight(
                channel.handle, expected_id=str(channel.telegram_channel_id or "")
            )
            result["telegram"] = telegram_service.status(probe=False)
        except TelegramTransportError as exc:
            result["source_channel"] = str(exc)
    else:
        result["source_channel"] = "Source authorization missing"
    return result


@app.post("/system/restart")
def system_restart(confirm: str = Form(""),
                   user: User = Depends(require_role("owner")),
                   db: Session = Depends(session_dependency),
                   _csrf: None = Depends(require_csrf)):
    if confirm != "restart":
        raise HTTPException(400, "Confirm the service restart first.")
    if os.name != "nt":
        raise HTTPException(409, "Use the service scripts on the Windows host.")
    audit(db, user, "services.restart", "system", 0)
    db.commit()
    log = settings.data_dir / "logs" / "service-restart.log"
    with log.open("ab") as output:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(BASE_DIR / "scripts" / "restart-services.ps1")],
            cwd=BASE_DIR, stdout=output, stderr=output,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        )
    return HTMLResponse("Restart requested. Reopen the dashboard shortly. Paused jobs can be resumed in Jobs.")
