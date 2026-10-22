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
        env_file=".env",
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

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:3b-instruct"
    ollama_keep_alive: str = "60s"

    telegram_api_id: str = ""
    telegram_api_hash: str = ""
    telegram_session_path: str = "./data/telegram.session"

    worker_lock_minutes: int = 120
    login_max_attempts: int = 5
    login_window_minutes: int = 15
    max_import_bytes: int = 40 * 1024 * 1024

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "media").mkdir(exist_ok=True)
        (self.data_dir / "geodata").mkdir(exist_ok=True)

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


settings = Settings()
settings.ensure_dirs()
