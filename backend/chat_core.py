from typing import Optional, List, Dict, Any
from openai import AsyncOpenAI
import config as config
import json
import logging
from db.db import async_session as sql_session
from db.db_model import UserProfile, ConversationSession
from db.dbvector_model import add_memory
from datetime import datetime
from db.dbvector_model import search_memories
from backend.nliclient import NLIClient
import re
from backend.schematic import SyntheticExample
from pydantic import ValidationError

logger = logging.getLogger(__name__)
ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=config.OPENAI_API_KEY
)
RedisStorage=123
nli_client = NLIClient(
    base_url="https://openrouter.ai/api/v1",
    api_key=config.OPENAI_API_KEY,
    model="meta-llama/llama-3.2-1b-instruct"
)
# For nli may work model="cross-encoder/nli-distilroberta-base"!
def get_chroma_client():
    from db.dbvector import collection
    return collection  # Mock-getter of collection!

chroma = get_chroma_client()
CHUNK_SIZE = config.CHUNK_SIZE
NLI_THRESHOLD = config.NLI_THRESHOLD
SYSTEM_PROMPT = """
Ты ассистент-помощник по разметке данных в сервисе для RAG-систем.
На основе предоставленного в формате PDF-документов регламента сгенерируй:
1. Пример данных на основе имеющейся в документах информации, генерируешь это для пользователя, который выполняет разметку.
2. Точный ответ, правильный по твоему суждению, на основе информации из документов.
3. Укажи уровень уверенности (0.0-1.0).
.
Формат ответа строго в виде JSON:
{{
    "question": "текст вопроса",
    "answer": "текст ответа",
    "confidence": 0.95,
    "source_span": "цитата из фрагмента, на которой основан ответ"
}}
!
Фрагмент документа: 
{chunk}
.
"""
class AnnotationSession:
    """Simple ''one-shot'' session!"""
    def __init__(self, user_id: str, document_id: Optional[str] = None):
        self.user_id = user_id
        self.document_id = document_id
        self.history: List[Dict[str, Any]] = []
        self.pending_chunks: List[str] = []
    
    def add_chunk(self, chunk: str):
        """Adding chunk to the queue for example-generation."""
        if chunk not in self.pending_chunks:
            self.pending_chunks.append(chunk)
    
    def get_next_chunk(self) -> Optional[str]:
        return self.pending_chunks.pop(0) if self.pending_chunks else None

async def generate_synthetic_example(chunk: str) -> Optional[Dict[str, Any]]:
    """Example generation for pipeline with structured output using!"""
    try:
        prompt = SYSTEM_PROMPT.format(chunk=chunk[:CHUNK_SIZE])
        completion = await ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ответом должен быть ТОЛЬКО валидный JSON без пояснений!"},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = completion.choices[0].message.content.strip()
        
        if not content:
            print("### ERROR: LLM returned empty content ###", flush=True)
            logger.error("LLM returned empty content")
            return None
        print(f"### RAW: {repr(content[:300])} ###", flush=True)
        logger.error(f"### RAW: {repr(content[:300])} ###")
        clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', content, flags=re.MULTILINE)
        match = re.search(r'\{[\s\S]*\}', clean)
        if not match:
            print("### ERROR: No JSON object found ###", flush=True)
            logger.error("No JSON object found")
            return None
        clean = match.group(0).strip()
        
        print(f"### CLEAN: {repr(clean[:300])} ###", flush=True)
        logger.error(f"### CLEAN: {repr(clean[:300])} ###")

        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            fixed = clean.replace('\n', '\\n').replace('\r', '\\r')
            try:
                data = json.loads(fixed)
            except Exception as e2:
                print(f"### JSON ERROR: {e2} ###", flush=True)
                logger.error(f"JSON decode failed: {e2}")
                return None
        
        if isinstance(data, dict):
            clean_data = {}
            for k, v in data.items():
                clean_key = str(k).strip().strip('"').strip("'").strip()
                clean_key = ''.join(c for c in clean_key if c not in '\n\r\t')

                if clean_key:
                    clean_data[clean_key] = v
            data = clean_data

            print(f"### KEYS: {list(data.keys())} ###", flush=True)
            logger.error(f"### KEYS: {list(data.keys())} ###")

        if not isinstance(data, dict) or "question" not in data or "answer" not in data:
            print(f"### MISSING FIELDS: {list(data.keys()) if isinstance(data, dict) else 'not a dict'} ###", flush=True)
            logger.error(f"Missing required fields. Got: {list(data.keys()) if isinstance(data, dict) else 'not a dict'}")
            return None
        try:
            example = SyntheticExample.model_validate(data)
        except KeyError as ke:
            print(f"### PYDANTIC KEYERROR: {repr(ke)} ###", flush=True)
            print(f"### DATA KEYS: {list(data.keys())} ###", flush=True)
            logger.error(f"Pydantic KeyError: {ke}, keys: {list(data.keys())}")
            return None
        print(f"### SUCCESS: {example.question[:50]} ###", flush=True)
        logger.error(f"### SUCCESS: {example.question[:50]} ###")

        return {
            "chunk": chunk,
            "question": str(example.question).strip(),
            "answer": str(example.answer).strip(),
            "confidence": float(example.confidence),
            "source_span": str(example.source_span or "").strip(),
            "status": "pending"
        }

    except Exception as e:
        print(f"### GLOBAL EXCEPTION: {type(e).__name__}: {e} ###", flush=True)
        logger.error(f"Generation failed: {type(e).__name__}: {e}")
        return None

async def verify_annotation(
    user_id: int,
    user_answer: str,
    original_chunk: str,
    existing_chunks: Optional[List[str]] = None
  ) -> Dict[str, Any]:
    """
    Checking for answer from client's feedback for contradiction with Knowledge base, returns NLI-analisys result!
    """
    if not existing_chunks:
        existing_chunks = search_memories(user_id=user_id, query=user_answer, k=3)

    conflicts = []
    for chunk in existing_chunks:
        nli_result = await nli_client.check_contradiction(
            premise=chunk, 
            hypothesis=user_answer
        )
        if nli_result["label"] == "contradiction" and nli_result["score"] > NLI_THRESHOLD:
            conflicts.append({
                "conflicting_chunk": chunk,
                "confidence": nli_result["score"],
                "source_doc": "..."
            })

    return {
        "has_conflict": len(conflicts) > 0,
        "conflicts": conflicts,
        "recommendation": "block" if conflicts else "allow"
    }

async def submit_annotation(
    session: AnnotationSession,
    example: Dict[str, Any],
    user_feedback: str,
    edited_answer: Optional[str] = None,
    user_id: Optional[int] = None
  ) -> Dict[str, Any]:
    target_answer = edited_answer if edited_answer else example["answer"]
    # Countradaction-check if user give unagreed feedback!
    if user_feedback == "edit" and edited_answer:
        verification = await verify_annotation(
            user_id=user_id,
            user_answer=edited_answer,
            original_chunk=example["chunk"]
        )
        if verification["has_conflict"]:
            return {
                "status": "conflict_detected",
                "conflicts": verification["conflicts"],
                "example": example,
                "user_answer": target_answer
            }
    # In-memory update for demonstration!
    example["status"] = user_feedback
    if edited_answer:
        example["answer"] = edited_answer
        example["edited_by_user"] = True
    
    session.history.append({
        "timestamp": datetime.now().isoformat(),
        "example": example,
        "feedback": user_feedback
    })

    if user_id is not None and user_feedback in ("accept", "edit"):
        async with sql_session() as db:
            try:
                profile = await UserProfile.get_or_create_with_lock(db, user_id) # Session with secure-lock!
                conv = await ConversationSession.get_active_with_lock(db, user_id)
                if not conv:
                    conv = ConversationSession(user_id=user_id)
                    db.add(conv)
                    await db.flush()  # Getting id before commited!
                audit_entry = {
                    "chunk_id": example.get("chunk_id", "unknown"),
                    "action": user_feedback,
                    "original_answer": example["answer"],
                    "final_answer": target_answer,
                    "timestamp": datetime.now().isoformat()
                }# Writing to the "summary" cell!
                current = json.loads(conv.summary or "[]")
                current.append(audit_entry)
                conv.summary = json.dumps(current, ensure_ascii=False)
                conv.message_count += 1
                fact_content = f"Q: {example['question']}\nA: {target_answer}"
                fact_id = await add_memory(
                    user_id=user_id,
                    content=fact_content,
                    chunk_index=0,
                    document_id="verified_feedback",
                    timestamp=datetime.now().isoformat()
                )
                await db.commit() # Commit only for full interact-session!
                logger.info(f"Annotation saved: user={user_id}, fact_id={fact_id}")
                
            except Exception as e:
                await db.rollback()
                logger.error(f"DB transaction failed: {e}") # In-memory for debug!
                return {"status": "accepted_with_warning", "example": example, "db_error": str(e)}
    return {"status": "accepted", "example": example}

async def export_golden_set(session: AnnotationSession, format: str = "jsonl") -> str:
    verified = [
        h["example"] for h in session.history 
        if h["example"]["status"] in ("accept", "edit")
    ]
    if format == "jsonl":
        lines = []
        for ex in verified:
            messages = [
                {"role": "user", "content": ex["question"]},
                {"role": "assistant", "content": ex["answer"]}
            ]
            record = {"messages": messages}
            lines.append(json.dumps(record, ensure_ascii=False)
            )
        return "\n".join(lines)
    return "(*)"# При миграции Qdrant заменить на AsyncQdrantClient и вынести генерацию эмбеддингов в поток/сервис.!!!