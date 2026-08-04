from config.logger_config import get_logger
from routers import llm_inference

from fastapi import FastAPI
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
    return {"status": "ok"}


app.include_router(llm_inference.router)
