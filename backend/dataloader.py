import os
import uuid
import logging
import asyncio
from pathlib import Path
from typing import List, Optional
from fastapi import UploadFile, HTTPException
import fitz #PDF-reader!
from docx import Document #DOCX!
import chardet #TXT!
from db.dbvector_model import add_memory, get_fact_hash
import config
from datetime import datetime

logger = logging.getLogger(__name__)
CHUNK_SIZE = config.CHUNK_SIZE
CHUNK_OVERLAP = 64

def extract_text_from_pdf(file_path: str) -> str:
    text = []
    doc = fitz.open(file_path)
    for page in doc:
        page_text = page.get_text("text")
        
        if page_text.strip():
            text.append(page_text)
    doc.close()

    return "\n\n".join(text)

def extract_text_from_docx(file_path: str) -> str:
    doc = Document(file_path)
    return "\n\n".join([para.text for para in doc.paragraphs if para.text.strip()])


def extract_text_from_txt(file_path: str) -> str:
    with open(file_path, "rb") as f:
        raw = f.read()
        encoding = chardet.detect(raw)["encoding"] or "utf-8"
    return raw.decode(encoding, errors="ignore")

def smart_chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    if not text.strip():
        return [] #Chunking by symbols with overlaying!
    chunks = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = start + chunk_size
        #If not end of the text then finding end of line or end of sentence!
        if end < text_len: #20% Range from full chunk!
            search_window = text[end - overlap : end + chunk_size // 4]
            for sep in ["\n", ". ", "! ", "? "]:
                idx = search_window.find(sep)

                if idx != -1 and idx > chunk_size // 2:
                    end = start + chunk_size // 2 + idx + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap #Overlaying!
        #Avoiding of the infinity loop!
        if overlap == 0 and start >= end:
            break
    return chunks


async def process_document(
    file: UploadFile,
    user_id: int,
    document_id: Optional[str] = None
  ) -> dict:  
    """
    Main pipeline: loading → parsing → chunking → embedding-create → data-saving-packet.!
    """
    allowed_extensions = {".pdf", ".docx", ".doc", ".txt"}
    ext = Path(file.filename).suffix.lower()

    if ext not in allowed_extensions:
        raise HTTPException(400, f"Неподдерживаемый формат файлов: {ext}!")
    #Temp-data!
    temp_dir = Path("/tmp/documents")
    temp_dir.mkdir(exist_ok=True)
    temp_path = temp_dir / f"{uuid.uuid4()}{ext}"
    with open(temp_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)
    try:
        if ext == ".pdf":
            text = extract_text_from_pdf(str(temp_path))
        elif ext in {".docx", ".doc"}:
            text = extract_text_from_docx(str(temp_path))
        else:  # .txt
            text = extract_text_from_txt(str(temp_path))
        if not text.strip():
            raise HTTPException(400, "Документ пуст или не содержит извлекаемого текста.")
        chunks = smart_chunk_text(text) 
        
        if not chunks:
            raise HTTPException(400, "Не удалось разбить текст на чанки.")
        #Async-correct data proccessing!
        added_count = 0
        doc_timestamp = datetime.now().isoformat()
        for i, chunk in enumerate(chunks):
            #fact_id = get_fact_hash(user_id, chunk) for cache w. g.!!!
            try:
                result = await add_memory(
                    user_id=user_id,
                    content=chunk,
                    chunk_index=i,
                    document_id=document_id or file.filename,
                    timestamp=doc_timestamp
                )
                if result:
                    added_count += 1
            except Exception as e:
                logger.warning(f"Failed to add chunk {i}: {e}")
                continue #Skipping of difficult chunks for perfomance-balance!

        return {
            "status": "success",
            "document_id": document_id or file.filename,
            "chunks_processed": len(chunks),
            "chunks_added": added_count,
            "message": f"Обработано {len(chunks)} чанков, добавлено {added_count}"
        }
    finally:
        if temp_path.exists():
            temp_path.unlink()