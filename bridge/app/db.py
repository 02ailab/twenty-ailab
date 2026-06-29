# Postgres access for the bridge: a small asyncpg pool plus the id-mapping tables
# that link Chatwoot records to their Twenty counterparts. This is the bridge's
# own state — it does not touch the Chatwoot or Twenty databases.
from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

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

-- One Twenty Note per Chatwoot conversation (keyed by display_id). On each
-- resolve the body is rewritten with the latest full transcript (variant C).
CREATE TABLE IF NOT EXISTS note_map (
    conversation_display_id BIGINT PRIMARY KEY,
    twenty_note_id          UUID NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
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


@contextlib.asynccontextmanager
async def contact_lock(chatwoot_contact_id: int) -> AsyncIterator[None]:
    # Session-level Postgres advisory lock that serializes the read-then-create of a
    # Twenty Person for ONE Chatwoot contact. Two near-simultaneous webhooks for the
    # same contact (e.g. contact_created + conversation_created) would otherwise both
    # find no mapping and each create a Person — a duplicate. The lock is keyed by the
    # bigint contact id (its own advisory keyspace; nothing else uses it) and held on
    # a dedicated pool connection for the critical section only.
    async with _require_pool().acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", chatwoot_contact_id)
        try:
            yield
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", chatwoot_contact_id)


# --- contact mapping ---

async def get_twenty_person_id(chatwoot_contact_id: int) -> str | None:
    async with _require_pool().acquire() as conn:
        row = await conn.fetchval(
            "SELECT twenty_person_id FROM contact_map WHERE chatwoot_contact_id = $1",
            chatwoot_contact_id,
        )
        return str(row) if row else None


async def get_chatwoot_contact_id(twenty_person_id: str) -> int | None:
    # Reverse lookup for direction B (Twenty -> Chatwoot). Cast to uuid so a string
    # arg matches the UUID column regardless of asyncpg type inference.
    async with _require_pool().acquire() as conn:
        row = await conn.fetchval(
            "SELECT chatwoot_contact_id FROM contact_map WHERE twenty_person_id = $1::uuid",
            twenty_person_id,
        )
        return int(row) if row is not None else None


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


# --- note mapping (conversation display_id <-> Twenty note) ---

async def get_note_id(conversation_display_id: int) -> str | None:
    async with _require_pool().acquire() as conn:
        row = await conn.fetchval(
            "SELECT twenty_note_id FROM note_map WHERE conversation_display_id = $1",
            conversation_display_id,
        )
        return str(row) if row else None


async def set_note_id(conversation_display_id: int, twenty_note_id: str) -> None:
    async with _require_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO note_map (conversation_display_id, twenty_note_id)
            VALUES ($1, $2)
            ON CONFLICT (conversation_display_id)
            DO UPDATE SET twenty_note_id = EXCLUDED.twenty_note_id,
                          updated_at = now()
            """,
            conversation_display_id, twenty_note_id,
        )
