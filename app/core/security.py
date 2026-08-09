import os
from dotenv import load_dotenv
import asyncio
import bcrypt
from datetime import datetime, timezone, timedelta
import jwt

load_dotenv()
JWT_SECRET = os.environ["JWT_SECRET"]

# 将明文密码变为不可逆哈希：
def hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")
    hashed_bytes = bcrypt.hashpw(
        password_bytes,
        bcrypt.gensalt(),
    )
    return hashed_bytes.decode("utf-8")


# 校验密码：
def verify_password(password: str, hashed: str) -> bool:
    password_bytes = password.encode("utf-8")
    hashed_bytes = hashed.encode("utf-8")
    return bcrypt.checkpw(password_bytes, hashed_bytes)


# 创建身份凭证 token
def create_access_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")
