import sys
from pathlib import Path

# Add project root to sys.path to allow running this script directly
project_root = str(Path(__file__).resolve().parents[1])
if project_root not in sys.path:
    sys.path.append(project_root)

from fastapi import HTTPException, status
from asyncpg import Pool, create_pool, PostgresError
import asyncio

## Internal Packages
from config import get_logger
from config import DB_HOST, DB_PORT, DB_USERNAME, DB_PASSWORD, DB_MIN_CONN, DB_MAX_CONN, KNOWLEDGEBASE_DATABASE_NAME

logging = get_logger(__name__)


async def connect_to_db(database_name: str) -> Pool:
    """
    Connect to the PostgreSQL database server asynchronously and return a connection pool.
    Args:
        database_name (str): Name of the database to be connected.
    Returns:
        pool (asyncpg.Pool): Database connection pool.
    """

    if not database_name or not isinstance(database_name, str):
        logging.error(
            "database_name must be a non-empty string but given %s", database_name
        )
        raise ValueError(
            f"database_name must be a non-empty string but given {database_name}"
        )

    ## Database connection parameters
    db_config = {
        "database": database_name,
        "user": DB_USERNAME,
        "password": DB_PASSWORD,
        "host": DB_HOST,
        "port": DB_PORT,
        "min_size": DB_MIN_CONN,
        "max_size": DB_MAX_CONN,
    }

    ## Create an async connection pool
    try:
        pool = await create_pool(**db_config)
        return pool

    except PostgresError as e:
        logging.error("Database connection failed for %s: %s", database_name, str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database connection cannot be established",
        )

    except Exception as e:
        logging.error("Database connection failed for %s: %s", database_name, str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database connection cannot be established",
        )


async def validate_database(database_name):
    """
    Validates if the database exists
    """

    if not database_name or not isinstance(database_name, str):
        logging.error(
            f"HTTPException {status.HTTP_400_BAD_REQUEST}: database_name must be a non-empty string but given {database_name}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f" database_name must be a non-empty string but given {database_name}",
        )

    pool = await connect_to_db("postgres")

    try:
        async with pool.acquire() as conn:
            database_exists = (
                await conn.fetchval(
                    "SELECT 1 FROM pg_database WHERE datname = $1;", database_name
                )
                is not None
            )
    finally:
        await pool.close()

    if not database_exists:
        logging.error(
            f"HTTPException {status.HTTP_400_BAD_REQUEST}: Database '{database_name}' does not exist."
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Database '{database_name}' does not exist.",
        )


async def check_db_connection(database_name: str = None) -> bool:
    """
    Checks if the database is reachable by establishing a connection pool,
    acquiring a connection, and executing a simple test query.

    Args:
        database_name (str, optional): Name of the database to check.
                                       Defaults to KNOWLEDGEBASE_DATABASE_NAME.

    Returns:
        bool: True if connection check succeeds, False otherwise.
    """
    db_name = database_name or KNOWLEDGEBASE_DATABASE_NAME
    logging.info("Initiating database connection check for: %s", db_name)
    pool = None
    success = False
    try:
        pool = await connect_to_db(db_name)
        async with pool.acquire() as conn:
            res = await conn.fetchval("SELECT 1;")
            if res == 1:
                logging.info("Database connection check successful for database: %s", db_name)
                success = True
            else:
                logging.error("Database connection check failed: unexpected query result %s", res)
    except Exception as e:
        logging.error("Database connection check failed for database %s: %s", db_name, str(e))
    finally:
        if pool is not None:
            await pool.close()
    return success


if __name__ == "__main__":
    async def main():
        # Validate that the database exists
        print(f"Validating if database '{KNOWLEDGEBASE_DATABASE_NAME}' exists...")
        await validate_database(KNOWLEDGEBASE_DATABASE_NAME)
        print("Database validation succeeded.")

        # Check database connection
        print(f"Checking database connection to '{KNOWLEDGEBASE_DATABASE_NAME}'...")
        success = await check_db_connection(KNOWLEDGEBASE_DATABASE_NAME)
        if success:
            print("Database connection check succeeded!")
        else:
            print("Database connection check failed.")

    asyncio.run(main())
