import chromadb
from chromadb.config import Settings
import os

CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "./chroma-data")
chroma_client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
collection = chroma_client.get_or_create_collection(
    name="user_memories",
    metadata={
        "description": "Various facts and memories for LLM context",
        "embedding_model": "text-embedding-3-small"
    }
)

def get_collection():
    return collection