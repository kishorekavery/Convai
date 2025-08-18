from routers.llm_inference import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pathlib import Path


router = APIRouter(
    prefix= "/AI",
    tags=["Logs"]
    )

@router.get("/get-log")
async def get_file():
    log_file = Path(__file__).parent.parent / "logs" / "application.log"
    
    if not log_file.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail= "File not found"
        )
    
    return FileResponse(log_file)