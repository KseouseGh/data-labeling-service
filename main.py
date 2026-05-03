import asyncio
import logging
import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from db.db import init_db
from backend.routers import router as annotation_router, auth_router
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
@asynccontextmanager
async def service(app: FastAPI):
    await init_db()
    logger.info("Database initialized succsessfuly!")
    yield
    logger.info("Application turn-off.")

app = FastAPI(
    title="Data labeling AI Service",
    description="(Интеллектуальная система разметки данных)",
    version="0.1.0-alpha",
    lifespan=service,
    docs_url="/docs",
    redoc_url=None
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],#Mocked  for demo!
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(annotation_router, prefix="/api/v1", tags=["annotation"])
app.include_router(auth_router, prefix="/api/v1")
frontend_dir = Path(__file__).parent / "frontend"
@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}

app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
logger.info(f"Getting files from {frontend_dir}.")

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",# For in-dockercontainer works on localhost!
        port=8000,
        reload=False,
        log_level="info"
    )