# Postgres access for the bridge: a small asyncpg pool plus the id-mapping tables
# that link Chatwoot records to their Twenty counterparts. This is the bridge's
# own state — it does not touch the Chatwoot or Twenty databases.
from __future__ import annotations

import asyncpg

_pool: asyncpg.Pool | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS contact_map (
    chatwoot_contact_id BIGINT PRIMARY KEY,
    twenty_person_id    UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Companies are keyed by a normalized identity (domain preferred, else name)
-- so several contacts from the same company resolve to one Twenty Company.
CREATE TABLE IF NOT EXISTS company_map (
    company_key        TEXT PRIMARY KEY,
    twenty_company_id  UUID NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def init_pool(dsn: str) -> None:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
        async with _pool.acquire() as conn:
            await conn.execute(SCHEMA)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _require_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_pool() first")
    return _pool


async def ping() -> bool:
    async with _require_pool().acquire() as conn:
        return await conn.fetchval("SELECT 1") == 1


# --- contact mapping ---

async def get_twenty_person_id(chatwoot_contact_id: int) -> str | None:
    async with _require_pool().acquire() as conn:
        row = await conn.fetchval(
            "SELECT twenty_person_id FROM contact_map WHERE chatwoot_contact_id = $1",
            chatwoot_contact_id,
        )
        return str(row) if row else None


async def set_twenty_person_id(chatwoot_contact_id: int, twenty_person_id: str) -> None:
    async with _require_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO contact_map (chatwoot_contact_id, twenty_person_id)
            VALUES ($1, $2)
            ON CONFLICT (chatwoot_contact_id)
            DO UPDATE SET twenty_person_id = EXCLUDED.twenty_person_id,
                          updated_at = now()
            """,
            chatwoot_contact_id, twenty_person_id,
        )


# --- company mapping ---

async def get_twenty_company_id(company_key: str) -> str | None:
    async with _require_pool().acquire() as conn:
        row = await conn.fetchval(
            "SELECT twenty_company_id FROM company_map WHERE company_key = $1",
            company_key,
        )
        return str(row) if row else None


async def set_twenty_company_id(company_key: str, twenty_company_id: str) -> None:
    async with _require_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO company_map (company_key, twenty_company_id)
            VALUES ($1, $2)
            ON CONFLICT (company_key)
            DO UPDATE SET twenty_company_id = EXCLUDED.twenty_company_id,
                          updated_at = now()
            """,
            company_key, twenty_company_id,
        )
