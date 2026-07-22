"""
Shared asyncpg connection-pool registry.

One pool is created per database name and reused across all requests handled by
this process, instead of creating (and leaking) a new pool per request. asyncpg
pools are safe to share across concurrent tasks within a single event loop.

Each uvicorn worker is a separate process with its own module state, so it gets
its own registry - this is correct, since asyncpg pools cannot be shared across
processes.

Connection-budget constraint to respect when sizing pools / workers:
    workers x (num_client_dbs + 1 knowledgebase) x DB_MAX_CONN <= Postgres max_connections
For many tenants or high worker counts, raise Postgres max_connections or front
the database with PgBouncer.
"""

import asyncio

from asyncpg import Pool

from config import get_logger
from database.db_connection import connect_to_db

logging = get_logger(__name__)

# Cache of database_name -> live pool, shared for the lifetime of the process.
_pools: dict[str, Pool] = {}

# One lock per database name so concurrent first-requests for the same database
# do not race to create duplicate pools.
_locks: dict[str, asyncio.Lock] = {}

# Guards creation of the per-database locks themselves.
_registry_lock = asyncio.Lock()


async def get_pool(database_name: str) -> Pool:
    """
    Return a cached connection pool for ``database_name``, creating it on first
    use. Subsequent calls return the same pool.
    """
    pool = _pools.get(database_name)
    if pool is not None:
        return pool

    # Get (or create) the lock dedicated to this database name.
    async with _registry_lock:
        lock = _locks.get(database_name)
        if lock is None:
            lock = asyncio.Lock()
            _locks[database_name] = lock

    async with lock:
        # Re-check inside the lock: another task may have created it while we
        # were waiting.
        pool = _pools.get(database_name)
        if pool is None:
            logging.info("Creating shared connection pool for database: %s", database_name)
            pool = await connect_to_db(database_name)
            _pools[database_name] = pool
        return pool


async def close_all_pools() -> None:
    """Close every cached pool. Call once on application shutdown."""
    for database_name, pool in list(_pools.items()):
        try:
            await pool.close()
            logging.info("Closed shared connection pool for database: %s", database_name)
        except Exception as e:
            logging.error("Error closing pool for %s: %s", database_name, str(e))
    _pools.clear()
    _locks.clear()
