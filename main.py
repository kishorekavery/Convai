from config.logger_config import get_logger
from routers import get_logs
from routers import llm_inference

from fastapi import FastAPI

from contextlib import asynccontextmanager

logging = get_logger("fastapi")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info("FastAPI app starting...")
    yield
    logging.info("FastAPI app shutting down. Flushing traces...")
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

app.include_router(llm_inference.router)
app.include_router(get_logs.router)