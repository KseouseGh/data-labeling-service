from passlib.context import CryptContext
from datetime import datetime, timedelta
import jwt
import config

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
SECRET_KEY = config.JWT_SECRET_KEY
ALGORITHM = "HS256"

def get_password_hash(password: str) -> str: # Hash-72-s!
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(user_id: int, username: str) -> str:
    payload = {"sub": str(user_id),
        "username": username,
        "exp": datetime.utcnow() + timedelta(days=30)}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])