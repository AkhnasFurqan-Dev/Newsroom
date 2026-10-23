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
from ..constants import EXCLUSION_REASONS, ROLE_RANK, SECTION_LABELS, SECTIONS
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
from ..ingestion.adapters import ImportFormatError, normalize_messages
from ..ingestion.service import ensure_import_media_dir, import_payload
from ..migrations import upgrade_database
from ..preflight import build_preflight
from ..models import (
    Article,
    ArticleMedia,
    ArticleVersion,
    AuditEvent,
    CartographicProfile,
    Channel,
    ChannelOwnership,
    Edition,
    EditionArticle,
    EditionPage,
    EditionSchedule,
    Job,
    JobStep,
    PostFragment,
    PostMedia,
    PublicationAttempt,
    PublicationProfile,
    ReviewFlag,
    RightsGrant,
    SourceImport,
    SourcePost,
    StoryClaim,
    StoryCluster,
    StoryTimelineEvent,
    User,
)
from ..publishing.service import (
    edition_review_blockers,
    record_manifest_release,
)
from ..scheduling.service import (
    DuplicateWindowError,
    WorkerBusy,
    WorkerLease,
    enqueue_collect_job,
    enqueue_due_jobs,
    enqueue_manual_job,
    enqueue_republish_job,
    latest_due_cutoff,
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
    profile = db.get(PublicationProfile, 1) or db.scalar(select(PublicationProfile).limit(1))
    login = getattr(request.state, "login_session", None)
    return {
        "user": user,
        "profile": profile,
        "csrf": login.csrf_token if login else "",
        "active": active,
        "can": {
            "operate": ROLE_RANK.get(user.role, 0) >= ROLE_RANK["operator"],
            "edit": ROLE_RANK.get(user.role, 0) >= ROLE_RANK["editor"],
            "own": user.role == "owner",
        },
        **extra,
    }


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
            with WorkerLease():
                with SessionLocal() as db:
                    stats = worker_once(db, max_jobs=10)
                    db.commit()
                return f"Worker completed: {stats.processed} processed, {stats.published} published."
        except WorkerBusy:
            return "Worker is already running."
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
    return edition


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


@app.get("/")
def dashboard(request: Request, user: User = Depends(current_user), db: Session = Depends(session_dependency)):
    channels = list(db.scalars(select(Channel).order_by(Channel.id)))
    accessible = [channel for channel in channels if can_access_channel(db, user, channel)]
    counts = {
        "posts": db.scalar(select(func.count()).select_from(SourcePost)) or 0,
        "stories": db.scalar(select(func.count()).select_from(StoryCluster)) or 0,
        "articles": db.scalar(select(func.count()).select_from(Article)) or 0,
        "editions": db.scalar(select(func.count()).select_from(Edition)) or 0,
        "review": db.scalar(
            select(func.count()).select_from(ReviewFlag).where(ReviewFlag.status == "open")
        )
        or 0,
        "jobs": db.scalar(
            select(func.count()).select_from(Job).where(
                Job.status.in_(["scheduled", "failed_retryable", "running", "resumable"])
            )
        )
        or 0,
    }
    editions = list(db.scalars(select(Edition).order_by(Edition.created_at.desc()).limit(6)))
    jobs = list(db.scalars(select(Job).order_by(Job.id.desc()).limit(6)))
    recent_flags = list(
        db.scalars(
            select(ReviewFlag)
            .where(ReviewFlag.status == "open")
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
            channels=accessible,
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
    return redact(f"{type(exc).__name__}: {exc}")[:400]


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
        info = telegram_service.resolve_channel(handle)
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
        f"Resolved {channel.handle} (stable id {channel.telegram_channel_id}).",
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


@app.get("/stories")
def stories_page(
    request: Request,
    channel: int | None = None,
    section: str = "",
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
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
        ),
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
    versions = list(db.scalars(select(ArticleVersion).where(ArticleVersion.article_id == article.id).order_by(ArticleVersion.revision.desc())))
    audit_data = json.loads(article.audit_json or "{}")
    gate, blockers = publish_gate(db, article.id)
    media = list(db.scalars(select(ArticleMedia).where(ArticleMedia.article_id == article.id)))
    editions = list(
        db.scalars(
            select(Edition)
            .join(EditionArticle, EditionArticle.edition_id == Edition.id)
            .where(EditionArticle.article_id == article.id)
            .order_by(Edition.id.desc())
        )
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
            editions=editions,
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
        save_editor_edit(
            db,
            article,
            title=title,
            deck=deck,
            standfirst=standfirst,
            body_text=body_text,
            actor_id=user.id,
            lock=bool(lock),
        )
        audit(db, user, "article.edit", "article", article.id, {"revision": article.revision})
        db.commit()
    except ValueError as exc:
        db.rollback()
        return _redirect(f"/articles/{article.id}", error=str(exc))
    return _redirect(f"/articles/{article.id}", "Article saved as a new revision.")


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
    if status not in {"draft", "needs_review", "approved", "rejected"}:
        return _redirect(f"/articles/{article.id}", error="Unknown article status.")
    gate, blockers = publish_gate(db, article.id)
    if status == "approved" and not gate:
        return _redirect(
            f"/articles/{article.id}",
            error="Cannot approve while blocking review items remain: " + ", ".join(blockers),
        )
    article.status = status
    audit(db, user, "article.status", "article", article.id, {"status": status})
    db.commit()
    return _redirect(f"/articles/{article.id}", f"Article marked {status.replace('_', ' ')}.")


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
    channels = [c for c in db.scalars(select(Channel).order_by(Channel.id)) if can_access_channel(db, user, c, "operator")]
    selected = db.get(Channel, channel) if channel else _default_channel(channels)
    profile = db.get(PublicationProfile, 1) or db.scalar(select(PublicationProfile).limit(1))
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
    user: User = Depends(require_role("operator")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    channel = _channel_or_404(db, channel_id, user, "operator")
    profile = db.get(PublicationProfile, 1) or db.scalar(select(PublicationProfile).limit(1))
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
    if not channel.is_demo and active_rights(db, channel.id) is None:
        return _redirect(
            f"/build?channel={channel.id}",
            error="Build blocked: this channel has no active rights declaration.",
        )
    try:
        job = enqueue_manual_job(
            db,
            channel,
            window_start=start,
            window_end=end,
            kind=kind if kind in {"manual", "special"} else "manual",
            actor_id=user.id,
            title=title.strip()[:240],
            collect=(not channel.is_demo) and collect == "1",
            timezone=tz_name,
            cutoff_local=cutoff_clock,
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
    return _redirect(f"/jobs/{job.id}", f"Build queued. {launched}")


@app.get("/editions")
def editions_page(request: Request, user: User = Depends(current_user), db: Session = Depends(session_dependency)):
    allowed = [c.id for c in db.scalars(select(Channel)) if can_access_channel(db, user, c)]
    editions = list(
        db.scalars(
            select(Edition)
            .where(Edition.channel_id.in_(allowed or [-1]))
            .order_by(Edition.created_at.desc())
            .limit(100)
        )
    )
    return render(request, "editions.html", _ctx(db, request, user, "editions", editions=editions))


@app.get("/editions/{edition_id}")
def edition_detail(
    edition_id: int,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(session_dependency),
):
    edition = _edition_or_404(db, edition_id, user)
    manifest = json.loads(edition.manifest_json or "{}")
    pages = list(db.scalars(select(EditionPage).where(EditionPage.edition_id == edition.id).order_by(EditionPage.page_number)))
    blockers = edition_review_blockers(db, edition.id)
    job = db.scalar(select(Job).where(Job.edition_id == edition.id).order_by(Job.id.desc()))
    articles = list(
        db.scalars(
            select(Article)
            .join(EditionArticle, EditionArticle.article_id == Article.id)
            .where(EditionArticle.edition_id == edition.id)
            .order_by(EditionArticle.sort_order)
        )
    )
    flags = list(
        db.scalars(
            select(ReviewFlag).where(
                ReviewFlag.status == "open",
                ReviewFlag.article_id.in_([a.id for a in articles] or [-1]),
            )
        )
    )
    epaper_exists = bool(edition.epaper_dir and (Path(edition.epaper_dir) / "index.html").exists())
    pdf_exists = bool(edition.pdf_path and Path(edition.pdf_path).exists())
    return render(
        request,
        "edition.html",
        _ctx(
            db,
            request,
            user,
            "editions",
            edition=edition,
            manifest=manifest,
            pages=pages,
            blockers=blockers,
            job=job,
            articles=articles,
            flags=flags,
            epaper_exists=epaper_exists,
            pdf_exists=pdf_exists,
        ),
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
    blockers = edition_review_blockers(db, edition.id)
    if blockers:
        return _redirect(f"/editions/{edition.id}", error="Cannot release: " + "; ".join(blockers[:4]))
    if not edition.pdf_path or not Path(edition.pdf_path).exists():
        return _redirect(f"/editions/{edition.id}", error="Cannot release: the PDF is missing.")
    edition.status = "published"
    edition.published_at = utcnow()
    record_manifest_release(edition)
    commit_edition_window(db, edition)
    db.add(
        PublicationAttempt(
            edition_id=edition.id,
            destination=edition.output_dir or "local",
            status="succeeded",
            checksum=(json.loads(edition.manifest_json or "{}").get("pdf", {}) or {}).get("sha256", ""),
            completed_at=utcnow(),
        )
    )
    audit(db, user, "edition.publish", "edition", edition.id)
    db.commit()
    return _redirect(f"/editions/{edition.id}", "Edition released.")


@app.post("/editions/{edition_id}/republish")
def edition_republish(
    edition_id: int,
    request: Request,
    user: User = Depends(require_role("operator")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    edition = _edition_or_404(db, edition_id, user)
    job = enqueue_republish_job(db, edition, actor_id=user.id)
    audit(db, user, "edition.republish", "edition", edition.id, {"job": job.id})
    db.commit()
    launched = _launch_worker()
    return _redirect(f"/jobs/{job.id}", f"Re-render queued as a new version. {launched}")


# ---------------------------------------------------------------------------
# schedules and jobs
# ---------------------------------------------------------------------------


@app.get("/schedules")
def schedules_page(request: Request, user: User = Depends(current_user), db: Session = Depends(session_dependency)):
    allowed = [c for c in db.scalars(select(Channel).order_by(Channel.id)) if can_access_channel(db, user, c)]
    rows = []
    for channel in allowed:
        schedules = list(db.scalars(select(EditionSchedule).where(EditionSchedule.channel_id == channel.id).order_by(EditionSchedule.id)))
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
    if not channel.is_demo and active_rights(db, channel.id) is None:
        return _redirect("/schedules", error="Build blocked: no active rights declaration.")
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
        collect=not channel.is_demo,
        timezone=schedule.timezone,
        cutoff_local=schedule.cutoff_time,
    )
    audit(db, user, "schedule.run_now", "schedule", schedule.id, {"job": job.id})
    db.commit()
    launched = _launch_worker()
    return _redirect(f"/jobs/{job.id}", f"Build queued. {launched}")


@app.get("/jobs")
def jobs_page(request: Request, user: User = Depends(current_user), db: Session = Depends(session_dependency)):
    allowed = [c.id for c in db.scalars(select(Channel)) if can_access_channel(db, user, c)]
    jobs = list(
        db.scalars(
            select(Job)
            .where(or_(Job.channel_id.in_(allowed or [-1]), Job.channel_id.is_(None)))
            .order_by(Job.id.desc())
            .limit(100)
        )
    )
    return render(request, "jobs.html", _ctx(db, request, user, "jobs", jobs=jobs))


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
    return JSONResponse(
        {
            "id": job.id,
            "status": job.status,
            "stage": job.stage,
            "attempt": job.attempt,
            "error": redact(job.error or ""),
            "edition_id": job.edition_id,
            "result": json.loads(job.result_json or "{}"),
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
    if job.status not in {"failed_retryable", "failed_terminal", "awaiting_review"}:
        return _redirect(f"/jobs/{job.id}", error="Only failed or awaiting-review jobs can be retried.")
    if job.status == "awaiting_review":
        return _redirect(f"/jobs/{job.id}", error="Use the edition page to release or re-render a reviewed edition.")
    job.status = "scheduled"
    job.stage = "queued"
    job.attempt = 0
    job.error = ""
    job.scheduled_for = utcnow()
    job.finished_at = None
    job.lock_owner = ""
    job.lock_expires_at = None
    audit(db, user, "job.retry", "job", job.id)
    db.commit()
    launched = _launch_worker()
    return _redirect(f"/jobs/{job.id}", f"Job rescheduled. {launched}")


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
    if job.status not in {"scheduled", "failed_retryable", "failed_terminal"}:
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


@app.get("/branding")
def branding_page(request: Request, user: User = Depends(current_user), db: Session = Depends(session_dependency)):
    maps = list(db.scalars(select(CartographicProfile).order_by(CartographicProfile.id)))
    return render(request, "branding.html", _ctx(db, request, user, "branding", maps=maps))


@app.post("/branding")
def branding_save(
    request: Request,
    name: str = Form(...),
    channel_handle: str = Form(""),
    slogan: str = Form(""),
    footer_text: str = Form(""),
    city: str = Form(""),
    timezone: str = Form("Asia/Karachi"),
    accent: str = Form("#7b1f24"),
    paper_size: str = Form("tabloid"),
    contact_url: str = Form(""),
    map_profile: str = Form("neutral"),
    qr_strategy: str = Form("appendix"),
    issue_volume: int = Form(1),
    section_order: list[str] = Form(default=[]),
    allow_closing_light: str = Form(""),
    user: User = Depends(require_role("editor")),
    db: Session = Depends(session_dependency),
    _csrf: None = Depends(require_csrf),
):
    profile = db.get(PublicationProfile, 1) or db.scalar(select(PublicationProfile).limit(1))
    if profile is None:
        raise HTTPException(404, "Publication profile missing.")
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
    if qr_strategy not in {"appendix", "verified_only", "none"}:
        return _redirect("/branding", error="Unknown QR strategy.")
    profile.name = name.strip()[:160]
    profile.channel_handle = channel_handle.strip()[:120]
    profile.slogan = slogan.strip()[:240]
    profile.footer_text = footer_text.strip()
    profile.city = city.strip()[:120]
    profile.timezone = timezone
    profile.accent = accent
    profile.paper_size = paper_size
    profile.contact_url = contact_url.strip()[:500]
    profile.map_profile = map_profile
    profile.qr_strategy = qr_strategy
    profile.issue_volume = max(1, int(issue_volume))
    ordered = [value for value in section_order if value in dict(SECTIONS)]
    profile.section_order_json = json.dumps(ordered or ["national", "regional", "international", "defence", "conflict", "analysis", "opinion", "briefs"])
    profile.allow_closing_light = bool(allow_closing_light)
    profile.updated_at = utcnow()
    audit(db, user, "branding.update", "publication_profile", profile.id, {"name": profile.name})
    db.commit()
    return _redirect("/branding", "Publication profile saved.")


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
