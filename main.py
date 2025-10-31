from config.logger_config import get_logger
from routers import get_logs
from routers import llm_inference

from fastapi import FastAPI

logging = get_logger("fastapi")

app = FastAPI(root_path="/convai")

@app.get("/")
def root():
    return {"message": "Server is up & running"}

app.include_router(llm_inference.router)
app.include_router(get_logs.router)