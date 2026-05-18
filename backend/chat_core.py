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
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

logger = logging.getLogger(__name__)
ai_client = AsyncOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=config.OPENAI_API_KEY
)
# Global client-object for service!
nli_client = NLIClient(
    api_key=config.EMBEDDER_API_KEY,
    model="MoritzLaurer/deberta-v3-base-mnli-fever-anli"
)# For nli may work model="cross-encoder/nli-distilroberta-base"!
#llm = ChatOpenAI(
#    openai_api_base="https://openrouter.ai/api/v1",
#    openai_api_key=config.OPENAI_API_KEY,
#    model="gpt-4o-mini",
#    temperature=0.2,
#    async_client=ai_client # Client-con. i. ready!
#)
llm = ChatOpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=config.OPENAI_API_KEY,
    model="gpt-4o-mini",
    temperature=0.2 # Langchained client con. us.!
)
parser = JsonOutputParser(pydantic_object=SyntheticExample)
prompt = ChatPromptTemplate.from_messages([
    ("system", "Ответом должен быть ТОЛЬКО валидный JSON без пояснений!\n{format_instructions}"),
    ("user", "Фрагмент документа: \n{chunk}"),
])
generation_chain = prompt | llm | parser

def get_chroma_client():
    from db.dbvector import collection
    return collection  # Getter of true global DBV-collection by singleton, sav. f. concurrency!

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
    """Example generation using LangChain structured output parsing."""
    try: # Pipeline = prompt → LLM → Pydanticso!
        result = await generation_chain.ainvoke({
            "chunk": chunk[:CHUNK_SIZE],
            "format_instructions": parser.get_format_instructions()
        })
        logger.info(f"### LANGCHAIN SUCCESS: {result.get('question', '')[:50]} ###")
        print(f"### LANGCHAIN SUCCESS: {result.get('question', '')[:50]} ###", flush=True)
        return {
            "chunk": chunk,
            "question": str(result["question"]).strip(),
            "answer": str(result["answer"]).strip(),
            "confidence": float(result.get("confidence", 0.5)),
            "source_span": str(result.get("source_span", "")).strip(),
            "status": "pending"
        }
    except Exception as e: # Automated error-tracing with Langchain!
        logger.error(f"### LANGCHAIN ERROR: {type(e).__name__}: {e} ###!")
        print(f"### LANGCHAIN ERROR: {type(e).__name__}: {e} ###!", flush=True)
        return None

async def verify_annotation(
    user_id: int,
    user_answer: str,
    original_chunk: str,
    existing_chunks: Optional[List[str]] = None
  ) -> Dict[str, Any]: # NLI-analisys results in a dict!
    """
    Checking for answer from client's feedback for contradiction with Knowledge base, returns NLI-analisys result!
    """
    if not existing_chunks:
        existing_chunks = await search_memories(user_id=user_id, query=user_answer, k=3)
    # Storage for conflicts-fragments!
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
                conv.summary = json.dumps(current, ensure_ascii=False) # Conv-logging in dict!
                conv.message_count += 1
                await db.commit() # Commit only for full interact-session!
                logger.info(f"Annotation saved: user={user_id}")
                
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
    return "(*)"