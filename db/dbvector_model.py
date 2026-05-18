import hashlib
import uuid
from db.dbvector import get_collection
from langchain_openai import OpenAIEmbeddings
import config
from typing import List, Optional
import httpx
import asyncio
import logging
import urllib
import datetime
logger = logging.getLogger(__name__)
async def embeddings(text: str) -> List[float]: # Getting embedder-model directly!
    model_id = "sentence-transformers/all-MiniLM-L6-v2"
    encoded_model = urllib.parse.quote(model_id, safe="")
    API_URL = f"https://router.huggingface.co/hf-inference/models/{encoded_model}/pipeline/feature-extraction"
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            API_URL,
            headers={
                "Authorization": f"Bearer {config.EMBEDDER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"inputs": text}
        )
        response.raise_for_status()
        embedding = response.json()
        if not isinstance(embedding, list) or not embedding:
            logger.error(f"Invalid embedding response from HF: {embedding}!")
            raise ValueError(f"HF API returned invalid embedding: {embedding}!")
        return embedding

def get_fact_hash(user_id: int, content: str) -> str:
    """
    For duplicate-checking!
    """
    text = f"{user_id}:{content}"
    return hashlib.sha256(text.encode()).hexdigest()


async def add_memory(user_id: int, content: str, chunk_index: int, document_id: Optional[str], timestamp: str) -> str:
    """
    Writing in vector-memory,    
    returns:
    - ID for fact or various information-unit, if succsessfully added!
    - None, if failed!.
    """
    embedding = await embeddings(content)
    fact_id = f"{user_id}_{uuid.uuid4()}"
    collection = get_collection()# ChromaDB is sync in temp-demo-version, but thats so fast and do not lock loop or net!
    collection.add(
        documents=[content],
        embeddings=[embedding],
        ids=[fact_id],
        metadatas=[{
            "user_id": user_id,
            "chunk_index": chunk_index,
            "document_id": document_id,
            "timestamp": timestamp
        }]
    )
    return fact_id

async def search_memories(user_id: int, query: str, k: int = 5) -> list:
    """
    Search with filtering by ''user_id''!
    """
    query_embedding = await embeddings(query)
    collection = get_collection() # Generate embedding for request!
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=k,
        where={"user_id": user_id} # In produc. ver. must be filter by acc_id&doc_id for consistent NLI-validation!
    )# Returns "facts-text"!
    return results["documents"][0] if results["documents"] else []


def delete_user_memories(user_id: int):
    """
    If concrete command was used by user, it'll delete all the facts related with (/start ''''or'''' /forget)!
    """
    collection = get_collection() # Getting users-id!
    results = collection.get(
        where={"user_id": user_id},
        include=["metadatas"]
    )
    
    if results["ids"]:
        collection.delete(ids=results["ids"])