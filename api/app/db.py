"""Database connections, from one small pool per process.

Opening a Postgres connection costs several network round trips: TCP, TLS and
authentication. Against a hosted database that is most of a request's time, and
every request used to open two (one to check who is asking, one for the work).
The pool keeps a few connections open and lends them out.

``connect()`` is a drop-in replacement for ``psycopg.connect(url,
row_factory=dict_row)`` used as a context manager: the block's work is committed
when it ends normally and rolled back when it raises, and the connection goes
back to the pool instead of being closed.

Statements are never prepared on the server (``prepare_threshold=None``), so the
pool also works behind a transaction-mode pooler such as Supabase's on port 6543,
where a prepared statement would not survive to the next transaction.
"""

from __future__ import annotations

import os
import threading
import time
import weakref
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

#: Per process. Render runs two workers, so a deployment holds at most twice this.
POOL_SIZE = int(os.environ.get("DATABASE_POOL_SIZE", "10"))
#: A connection unused this long is closed, before a server or proxy drops it.
MAX_IDLE_SECONDS = 120.0
#: A connection handed back this recently is lent out again without first
#: asking the server whether it is still there. That check is a round trip of
#: its own, and on a busy page every request borrows a connection.
RECENTLY_USED_SECONDS = 10.0

#: When each pooled connection was last handed back.
_returned_at: weakref.WeakKeyDictionary[psycopg.Connection, float] = (
    weakref.WeakKeyDictionary()
)


def _mark_returned(connection: psycopg.Connection) -> None:
    _returned_at[connection] = time.monotonic()


def _check(connection: psycopg.Connection) -> None:
    """Make sure a connection still works before it is lent out.

    A connection the server closed while it sat in the pool is replaced here,
    not discovered mid-request. One used moments ago is trusted as it is.
    """

    returned = _returned_at.get(connection)
    if returned is not None and time.monotonic() - returned < RECENTLY_USED_SECONDS:
        return
    ConnectionPool.check_connection(connection)


_pools: dict[str, ConnectionPool] = {}
_lock = threading.Lock()


def _pool(database_url: str) -> ConnectionPool:
    pool = _pools.get(database_url)
    if pool is not None:
        return pool
    with _lock:
        pool = _pools.get(database_url)
        if pool is None:
            pool = ConnectionPool(
                database_url,
                min_size=1,
                max_size=max(POOL_SIZE, 1),
                max_idle=MAX_IDLE_SECONDS,
                check=_check,
                reset=_mark_returned,
                kwargs={"row_factory": dict_row, "prepare_threshold": None},
                name="rasta",
                open=True,
            )
            _pools[database_url] = pool
    return pool


@contextmanager
def connect(database_url: str) -> Iterator[psycopg.Connection]:
    """Borrow a connection; commit on success, roll back on error, return it."""

    with _pool(database_url).connection() as connection:
        yield connection


def close_pools() -> None:
    """Close every pool; for process shutdown and tests."""

    with _lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        pool.close()


__all__ = ["POOL_SIZE", "close_pools", "connect"]
