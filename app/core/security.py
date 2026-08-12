import os
from dotenv import load_dotenv
import bcrypt
from datetime import datetime, timezone, timedelta
import jwt

from app.core.exceptions import InvalidTokenError

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
def create_access_token(user_id: int, time_: int = 30) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=time_),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


# 验证身份凭证 token
def decode_access_token(token: str) -> int:
    try:
        pyload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=["HS256"],
            options={"require": ["sub", "exp"]}
        )
        return int(pyload["sub"])

    except jwt.ExpiredSignatureError as e:
        raise InvalidTokenError("认证超时") from e

    except (jwt.InvalidTokenError, ValueError, TypeError) as e:
        raise InvalidTokenError("非法认证") from e
