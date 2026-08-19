"""Shared asyncpg connection-pool registry."""

import asyncio

from asyncpg import Pool

from config import get_logger
from database.db_connection import connect_to_db

logging = get_logger(__name__)

# Cache of database_name -> live pool
_pools: dict[str, Pool] = {}
# Per-database locks to prevent concurrent duplicate pool creation
_locks: dict[str, asyncio.Lock] = {}
# Global lock to safely create per-database locks
_registry_lock = asyncio.Lock()


async def get_pool(database_name: str) -> Pool:
    """Return a cached connection pool for ``database_name``."""
    pool = _pools.get(database_name)
    if pool is not None:
        return pool

    async with _registry_lock:
        lock = _locks.get(database_name)
        if lock is None:
            lock = asyncio.Lock()
            _locks[database_name] = lock

    async with lock:
        # Double-checked locking: check if pool was created while waiting for the lock
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
