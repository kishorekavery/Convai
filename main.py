from config.logger_config import get_logger
from routers import llm_inference

import asyncio
import time

from fastapi import FastAPI, Response, status
from contextlib import asynccontextmanager

logging = get_logger("fastapi")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("FastAPI app starting...")
    yield
    logging.info("FastAPI app shutting down. Closing DB pools and flushing traces...")

    # Close all shared connection pools created during the process lifetime.
    try:
        from database import close_all_pools

        await close_all_pools()
    except Exception as e:
        logging.error(f"Error closing DB pools on shutdown: {e}")

    # Let in-flight Bedrock calls finish before the process exits, so a rolling
    # deploy does not truncate a response mid-generation.
    try:
        from models import shutdown_bedrock_executor

        shutdown_bedrock_executor(wait=True)
    except Exception as e:
        logging.error(f"Error shutting down the Bedrock executor: {e}")

    # pyrefly: ignore [missing-import]
    from routers.llm_inference import tracer_provider

    try:
        res = tracer_provider.force_flush()
        logging.info(f"OpenTelemetry force_flush result: {res}")
    except Exception as e:
        logging.error(f"Error flushing traces on shutdown: {e}")


app = FastAPI(root_path="/convai", lifespan=lifespan)


@app.get("/")
def root():
    return {"message": "Server is up & running"}


@app.get("/health")
def health_check():
    """
    Liveness: is this process alive?

    Deliberately checks nothing external. Docker's HEALTHCHECK uses this, and
    restarting the container because Postgres is unreachable would be wrong -
    the process is fine, its dependency is not. Use /ready for that.
    """
    return {"status": "ok"}


async def _check_knowledgebase() -> dict:
    """
    Can we actually reach the knowledge-base database?

    This is the right dependency to probe: every non-pagination request reads
    few-shot examples from it, so an instance that cannot reach it cannot serve.
    Tenant databases are per-request and there are 43 of them, so they are not
    probed here.

    Reuses the cached pool rather than opening a connection of its own, which
    also keeps that pool warm between requests.
    """
    from config import KNOWLEDGEBASE_DATABASE_NAME, READINESS_TIMEOUT_SECONDS
    from database import get_pool

    started = time.perf_counter()
    try:
        # The timeout wraps acquire() as well as the query: if the pool is
        # exhausted or the host is unreachable, acquire is where it hangs.
        async def _ping():
            pool = await get_pool(KNOWLEDGEBASE_DATABASE_NAME)
            async with pool.acquire() as conn:
                return await conn.fetchval("SELECT 1")

        await asyncio.wait_for(_ping(), timeout=READINESS_TIMEOUT_SECONDS)
        return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000)}
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "error": f"timed out after {READINESS_TIMEOUT_SECONDS}s",
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "latency_ms": round((time.perf_counter() - started) * 1000),
        }


@app.get("/ready")
async def readiness_check(response: Response):
    """
    Readiness: can this instance serve a request right now?

    Returns 503 when it cannot, so a load balancer takes it out of rotation
    instead of sending traffic that is guaranteed to fail. Point the balancer's
    health check here, and Docker's HEALTHCHECK at /health.
    """
    checks = {"knowledgebase_database": await _check_knowledgebase()}
    ready = all(c["ok"] for c in checks.values())

    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        logging.warning("Readiness check failed: %s", checks)

    return {"status": "ready" if ready else "not ready", "checks": checks}


app.include_router(llm_inference.router)
