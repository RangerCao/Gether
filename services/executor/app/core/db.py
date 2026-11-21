"""PostgreSQL 连接。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import quote

import psycopg
from psycopg.rows import dict_row

from app.core.config import Settings


def build_dsn(settings: Settings) -> str:
    if settings.database_url:
        return settings.database_url
    user = quote(settings.postgres_user, safe="")
    password = quote(settings.postgres_password, safe="")
    return (
        f"postgresql://{user}:{password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    )


class Database:
    def __init__(self, settings: Settings) -> None:
        self.dsn = build_dsn(settings)

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[psycopg.AsyncConnection]:
        conn = await psycopg.AsyncConnection.connect(self.dsn, row_factory=dict_row)
        try:
            yield conn
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
        finally:
            await conn.close()
