"""PostgreSQL LISTEN/NOTIFY transport — durable at-least-once messaging."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Awaitable, Callable

import psycopg

from rasa.bus.envelope import Envelope

_VALID_CHANNEL = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

_BACKING_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS bus_messages (
    id         BIGSERIAL PRIMARY KEY,
    channel    TEXT NOT NULL,
    envelope   JSONB NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bus_status ON bus_messages (channel, status);
"""


def _dsn(name: str = "rasa_orch") -> str:
    return (
        f"host={os.environ.get('RASA_DB_HOST', 'localhost')} "
        f"port={os.environ.get('RASA_DB_PORT', '5432')} "
        f"user={os.environ.get('RASA_DB_USER', 'postgres')} "
        f"password={os.environ.get('RASA_DB_PASSWORD', '')} "
        f"dbname={name} "
        f"sslmode=disable"
    )


class PostgresPublisher:
    """Insert a row into bus_messages and issue NOTIFY in a single transaction."""

    def __init__(self, *, dbname: str = "rasa_orch") -> None:
        self._dsn = _dsn(dbname)

    async def setup(self) -> None:
        async with await psycopg.AsyncConnection.connect(self._dsn, autocommit=True) as conn:
            await conn.execute(_BACKING_TABLE_DDL)

    async def publish(self, channel: str, msg: Envelope) -> None:
        _validate_channel(channel)
        env_json = msg.to_json()
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO bus_messages (channel, envelope) VALUES (%s, %s)",
                    (channel, json.dumps(json.loads(env_json))),
                )
                await conn.execute(f"NOTIFY {channel}")


class PostgresSubscriber:
    """LISTEN on a channel and dispatch notifications to an async handler.

    Uses a dedicated autocommit connection for LISTEN, plus ad-hoc connections
    to fetch rows on notification. The handler receives the full Envelope.
    """

    def __init__(self, *, dbname: str = "rasa_orch") -> None:
        self._dsn = _dsn(dbname)
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._handlers: dict[str, Callable[[Envelope], Awaitable[None]]] = {}
        self._listen_conn: psycopg.AsyncConnection | None = None
        self._task: asyncio.Task[None] | None = None

    async def setup(self) -> None:
        async with await psycopg.AsyncConnection.connect(self._dsn, autocommit=True) as conn:
            await conn.execute(_BACKING_TABLE_DDL)

    async def subscribe(self, channel: str, handler: Callable[[Envelope], Awaitable[None]]) -> None:
        _validate_channel(channel)
        self._handlers[channel] = handler

    async def listen(self, *channels: str) -> None:
        """Issue LISTEN on all subscribed channels and start the dispatch loop."""
        self._listen_conn = await psycopg.AsyncConnection.connect(self._dsn, autocommit=True)
        self._listen_conn.add_notify_handler(self._on_notify)
        for ch in channels:
            await self._listen_conn.execute(f"LISTEN {ch}")
        self._task = asyncio.create_task(self._dispatch_loop())

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._listen_conn is not None:
            await self._listen_conn.close()

    def _on_notify(self, notice: psycopg.Notification) -> None:
        payload = notice.payload or ""
        self._queue.put_nowait((notice.channel, payload))

    async def _dispatch_loop(self) -> None:
        while True:
            channel, message_id = await self._queue.get()
            handler = self._handlers.get(channel)
            if handler is None:
                continue
            try:
                env = await self._fetch_and_mark(channel, message_id)
                if env is not None:
                    await handler(env)
            except Exception:
                pass

    async def _fetch_and_mark(self, channel: str, message_id: str) -> Envelope | None:
        async with await psycopg.AsyncConnection.connect(self._dsn, autocommit=True) as conn:
            cur = await conn.execute(
                "SELECT id, envelope FROM bus_messages WHERE channel = %s AND status = 'pending' ORDER BY id LIMIT 1",
                (channel,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            row_id, envelope = row
            env = Envelope.from_json(json.dumps(envelope))
            await conn.execute(
                "UPDATE bus_messages SET status = 'consumed' WHERE id = %s",
                (row_id,),
            )
            return env


def _validate_channel(channel: str) -> None:
    if not _VALID_CHANNEL.match(channel):
        raise ValueError(
            f"Invalid channel name '{channel}'. Must match: ^[a-zA-Z_][a-zA-Z0-9_]*$"
        )
