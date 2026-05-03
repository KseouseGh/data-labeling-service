import os
from dotenv import load_dotenv
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASS = os.getenv("REDIS_PASS")
POSTGRES_PASS = os.getenv("POSTGRES_PASS")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
DBURL = f"postgresql+asyncpg://postgres:{POSTGRES_PASS}@{POSTGRES_HOST}:5432/raglabel"
CHUNK_SIZE=int(os.getenv("CHUNK_SIZE", 512))
NLI_THRESHOLD=float(os.getenv("NLI_THRESHOLD", 0.82))
EMBEDDER_API_KEY=os.getenv("EMBEDDER_API_KEY", "")
JWT_SECRET_KEY = "QWERTY12345JWTSECRETKEY123456789"