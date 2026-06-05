from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy.engine import Engine
from sqlmodel import Session

from planer.adapters.db import create_tables, make_engine
from planer.config import get_settings


@lru_cache(maxsize=1)
def _engine() -> Engine:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    create_tables(engine)
    return engine


def get_db() -> Generator[Session, None, None]:
    with Session(_engine()) as sess:
        yield sess
