import logging
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Header
from pydantic import BaseModel, Field
from backend.chat_core import (
    generate_synthetic_example,
    submit_annotation,
    export_golden_set,
    AnnotationSession
)
from db.dbvector_model import search_memories  # In-RAG searching!
from fastapi import UploadFile, File, Form
from backend.dataloader import process_document
from backend.auth import get_password_hash, verify_password, create_access_token, decode_token
from db.db import async_session as sql_session
from db.db_model import UserProfile
from sqlalchemy import select

auth_router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)
router = APIRouter()# In-memory as demo!{user_id: {"target": int, "current": int, "session": AnnotationSession, "queue": List[str]}}
_active_sessions: Dict[int, Dict[str, Any]] = {}
class AuthPayload(BaseModel):
    username: str
    password: str
# Structured output!
class StartSessionRequest(BaseModel):
    target_count: int = Field(..., ge=1, le=100, description="Сколько примеров данных нужно разметить.")
    document_filter: Optional[str] = Field(None, description="Опциональный фильтр по документу.")

class FeedbackRequest(BaseModel):
    feedback_type: str = Field(..., pattern="^(like|dislike|text)$")
    text_feedback: Optional[str] = Field(None, max_length=500) # Example getting from context!

class ExportRequest(BaseModel):
    format: str = Field(default="jsonl", pattern="^(jsonl|csv)$")

async def get_current_user(authorization: str = Header(...)) -> int: # Id-security[] for routing of API!
    try:
        scheme, token = authorization.split()
        if scheme.lower() != "bearer":
            raise ValueError
        payload = decode_token(token)
        return int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Token is invalidated of olded!")

@router.post("/session/start")
async def start_session(req: StartSessionRequest, user_id: int = Depends(get_current_user)):
    if user_id in _active_sessions: # If session already exists, then return status for idemp-y!
        session_data = _active_sessions[user_id]
        return {
            "status": "resumed",
            "progress": f"{session_data['current']}/{session_data['target']}",
            "message": "Сессия уже активна, возврат статуса!"
        } # Mocked-var with getting last added data in chunks!
    initial_chunks = await search_memories(user_id=user_id, query="Common context", k=20)
    
    if not initial_chunks:
        raise HTTPException(status_code=400, detail="Нет данных для генерации примеров. Сначала нужно загрузить данные!")
    # In-memory session annotation!
    annotation_session = AnnotationSession(
        user_id=str(user_id),
        document_id=req.document_filter
    )
    for chunk in initial_chunks:
        annotation_session.add_chunk(chunk)

    _active_sessions[user_id] = { # Saving state!
        "target": req.target_count,
        "current": 0,
        "session": annotation_session,
        "queue": initial_chunks,
        "started_at": datetime.now()
    }
    logger.info(f"Session started for user {user_id}: target={req.target_count}!")
    
    return {
        "status": "started",
        "target": req.target_count,
        "message": "Сессия создана. Команда /next_example для получения первого примера!"
    }

@router.get("/session/{user_id}/status")
async def get_session_status(user_id: int = Depends(get_current_user)):
    if user_id not in _active_sessions:
        raise HTTPException(status_code=404, detail="No active session!")
    
    data = _active_sessions[user_id]
    return {
        "user_id": user_id,
        "progress": f"{data['current']}/{data['target']}",
        "completed": data['current'],
        "remaining": data['target'] - data['current'],
        "is_finished": data['current'] >= data['target']
    }

@router.post("/next_example")
async def get_next_example(user_id: int = Depends(get_current_user)):
    if user_id not in _active_sessions:
        raise HTTPException(status_code=404, detail="Firstly, /session/start is required!")
    
    session_data = _active_sessions[user_id]
    # Checking is session completed or not!
    if session_data["current"] >= session_data["target"]:
        raise HTTPException(
            status_code=400, 
            detail=f"Разметка завершена, ({session_data['target']}/{session_data['target']}). Команда /export для выгрузки в датасет!"
        )
    
    annotation_session = session_data["session"]
    chunk = annotation_session.get_next_chunk() # Next chunk from queue!
    if not chunk:
        new_chunks = await search_memories(user_id=user_id, query="Additional context", k=10)
        for c in new_chunks:
            annotation_session.add_chunk(c)
        chunk = annotation_session.get_next_chunk()
        
        if not chunk:
            raise HTTPException(status_code=404, detail="No data for more examples!")
    
    example = await generate_synthetic_example(chunk)

    if not example:
        raise HTTPException(status_code=500, detail="Error while example generation. Try again later!")

    session_data["session"].history.append({"example": example, "timestamp": datetime.now()})
    return {
        "example_id": str(uuid.uuid4()),  # Unique-id for item!
        "question": example["question"],
        "answer": example["answer"],
        "confidence": example["confidence"],
        "source_span": example["source_span"],
        "progress": f"{session_data['current'] + 1}/{session_data['target']}"
    }

@router.post("/feedback")
async def submit_feedback(req: FeedbackRequest, background_tasks: BackgroundTasks, user_id: int = Depends(get_current_user)):
# notmoc-var Can with make writin data in backgr-d!
    if user_id not in _active_sessions:
        raise HTTPException(status_code=404, detail="No active session!")
    session_data = _active_sessions[user_id]
    annotation_session = session_data["session"] # Mocked-var for taking the last example!
    if not annotation_session.history:
        raise HTTPException(status_code=400, detail="No active example for feedback. Firstly /next_example command is r-ed!")
    
    last_entry = annotation_session.history[-1]
    example = last_entry["example"]
    feedback_map = {
        "like": "accept",
        "dislike": "reject",
        "text": "edit"
    }
    status = feedback_map[req.feedback_type]
    edited_answer = req.text_feedback if req.feedback_type == "text" else None
    result = await submit_annotation(
        session=annotation_session,
        example=example,
        user_feedback=status,
        edited_answer=edited_answer,
        user_id=user_id
    )

    if result["status"] in ("accepted", "accepted_with_warning"):
        session_data["current"] += 1
        logger.info(f"Feedback processed for user {user_id}: {req.feedback_type} → {result['status']}")
    response = {
        "status": result["status"],
        "message": "Фидбек принят!",
        "progress": f"{session_data['current']}/{session_data['target']}",
        "next_action": (
            "call /next_example for next item"
            if session_data["current"] < session_data["target"]
            else " calling command /export to download dataset!"
            )
    }
    if result["status"] == "conflict_detected":
        response["warning"] = "Обнаружено противоречие (заглушка: в следующей версии будет детализация.)!"
    
    return response

@router.post("/export")
async def export_dataset(req: ExportRequest, user_id: int = Depends(get_current_user)):
    if user_id not in _active_sessions:
        raise HTTPException(status_code=404, detail="Session is not founded!")
    session_data = _active_sessions[user_id]

    if session_data["current"] < session_data["target"]:
        raise HTTPException(
            status_code=400,
            detail=f"Не размечено нужное кол-во пр-ров: {session_data['current']}/{session_data['target']}!"
        )
    dataset = await export_golden_set(session_data["session"], format=req.format)
    
    if dataset == "(*)":
        raise HTTPException(status_code=404, detail="No data for export!")#If notmocked-var it FileResponse!
    return {
        "format": req.format,
        "count": len(dataset.strip().split("\n")),
        "data": dataset
    }

@router.delete("/session/{user_id}")
async def end_session(user_id: int = Depends(get_current_user)):
    if user_id not in _active_sessions:
        raise HTTPException(status_code=404, detail="No session!")
    del _active_sessions[user_id]
    logger.info(f"Session ended for user {user_id}!")
    return {"status": "ended", "message": "Сессия закрыта!"}

@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user),
    document_id: Optional[str] = Form(None)
    ):
    if not file or not file.filename:
        raise HTTPException(400, "There is no data to load!")
    try:
        result = await process_document(file, user_id, document_id)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Upload failed with error {e}!")
        raise HTTPException(500, f"Error while file loading {str(e)}!")

@auth_router.post("/register")
async def register(req: AuthPayload):
    uname = req.username.strip().lower()
    if len(uname) < 3 or len(req.password) < 6:
        raise HTTPException(400, "Логин ≥3, пароль ≥6 символов!")
    
    async with sql_session() as db:
        exists = await db.execute(select(UserProfile).where(UserProfile.username == uname))
        if exists.scalar_one_or_none():
            raise HTTPException(409, "Пользователь с таким никнеймом уже существует!")
        user = UserProfile(username=uname, password_hash=get_password_hash(req.password))
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return {"user_id": user.user_id, "username": user.username}

@auth_router.post("/login")
async def login(req: AuthPayload):
    uname = req.username.strip().lower()
    async with sql_session() as db:
        res = await db.execute(select(UserProfile).where(UserProfile.username == uname))
        user = res.scalar_one_or_none()

    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Неверный логин или пароль!")

    token = create_access_token(user.user_id, user.username)
    return {"access_token": token, "token_type": "bearer", "user_id": user.user_id}