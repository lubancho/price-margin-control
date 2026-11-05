"""Настройки приложения из переменных окружения.

Простыми словами: всё, что отличается между компьютерами (адрес базы, уровень логов),
берётся из окружения или файла .env, а не прописывается в коде.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
DEMO_SEED = 20261105
DEMO_NOTE = "Демонстрационные синтетические данные. Не являются показателями реальной компании."


def load_dotenv(path: Path | None = None) -> None:
    """Минимальное чтение .env без дополнительной библиотеки: KEY=VALUE, # — комментарий.
    Уже заданные переменные окружения не перезаписываются."""
    path = path or PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    database_url: str
    log_level: str
    data_dir: Path


def get_settings() -> Settings:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "Не задана переменная DATABASE_URL. Скопируйте .env.example в .env и заполните её."
        )
    return Settings(
        database_url=url,
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        data_dir=Path(os.environ.get("DATA_DIR", PROJECT_ROOT / "data")),
    )
