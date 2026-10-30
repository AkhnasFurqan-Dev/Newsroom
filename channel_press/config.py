from __future__ import annotations

import os
import secrets
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime settings.

    Secret values are never read from tracked files. ``secret_key`` is generated
    into the data directory on first use unless explicitly supplied by the
    environment (for a managed deployment).
    """

    model_config = SettingsConfigDict(
        env_prefix="CHANNEL_PRESS_",
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Channel Press"
    host: str = "127.0.0.1"
    port: int = 8765
    data_dir: Path = Field(default=BASE_DIR / "data")
    output_dir: Path = Field(default=BASE_DIR / "output")
    database_url: str | None = None
    timezone: str = "Asia/Karachi"
    session_hours: int = 12
    cookie_secure: bool = False
    demo_mode: bool = True
    public_base_url: str = ""
    secret_key: str | None = None

    # Automatic story discovery. Google and Brave need their own API
    # credentials; GDELT DOC 2.0 is the keyless fallback so the feature works
    # without a paid provider. No adapter scrapes a search results page.
    google_cse_api_key: str = ""
    google_cse_cx: str = ""
    brave_search_api_key: str = ""
    search_timeout_seconds: float = 10.0
    search_timespan_days: int = 7
    # Comma-separated publisher RSS feeds used by the keyless fallback. Each
    # outlet serves its own feed; no search results page is scraped.
    search_rss_feeds: str = (
        "https://www.dawn.com/feeds/home,"
        "https://feeds.bbci.co.uk/news/world/rss.xml"
    )

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:4b-q4_K_M"
    ollama_model_profile: str = "standard"
    ollama_keep_alive: str = "60s"
    ollama_timeout_seconds: int = 240
    ollama_num_ctx: int = 4096
    ollama_num_predict: int = 1800
    ollama_max_concurrency: int = 1
    ollama_unload_after_job: bool = True

    telegram_api_id: str = ""
    telegram_api_hash: str = ""
    telegram_session_path: str = "./data/telegram.session"
    telegram_source_username: str = ""
    telegram_login_timeout_seconds: int = 300
    telegram_preflight_timeout_seconds: float = 12
    telegram_history_limit: int = 5000
    telegram_download_media: bool = True
    telegram_max_video_mb: int = 120
    telegram_max_media_mb: int = 25
    media_max_assets_per_story: int = 3
    media_download_attempts: int = 2
    media_retention_hours: int = 72

    cutoff_local_time: str = "05:00"
    initial_context_lookback_days: int = 30
    auto_release: bool = False
    max_catchup_windows: int = 7
    disk_quota_mb: int = 4096

    worker_lock_minutes: int = 120
    worker_heartbeat_timeout_seconds: int = 180
    layout_timeout_seconds: int = 600
    layout_browser_timeout_seconds: int = 60
    layout_pagedjs_timeout_seconds: int = 300
    login_max_attempts: int = 5
    login_window_minutes: int = 15
    max_import_bytes: int = 40 * 1024 * 1024

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "media").mkdir(exist_ok=True)
        (self.data_dir / "geodata").mkdir(exist_ok=True)
        (self.data_dir / "fonts").mkdir(exist_ok=True)
        (self.data_dir / "theme-proofs").mkdir(exist_ok=True)

    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'channel_press.db').as_posix()}"

    @property
    def secret(self) -> str:
        if self.secret_key:
            return self.secret_key
        self.ensure_dirs()
        path = self.data_dir / "secret.key"
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        value = secrets.token_urlsafe(48)
        path.write_text(value, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return value

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def fonts_dir(self) -> Path:
        return self.data_dir / "fonts"

    @property
    def theme_proofs_dir(self) -> Path:
        return self.data_dir / "theme-proofs"

    @property
    def telegram_session_file(self) -> Path:
        """Absolute session path; relative values resolve against the project root."""
        raw = Path(self.telegram_session_path).expanduser()
        return raw if raw.is_absolute() else (BASE_DIR / raw).resolve()

    @property
    def env_file(self) -> Path:
        return BASE_DIR / ".env"


settings = Settings()
settings.ensure_dirs()
