"""security.py 的纯单元测试：不碰数据库、不碰 FastAPI，直接测函数行为。"""

import pytest

from app.core.security import hash_password, validate_jwt_secret, verify_password


@pytest.mark.parametrize(
    "value",
    [None, "", "   ", "replace_me", "too-short"],
)
def test_validate_jwt_secret_rejects_unsafe_values(value):
    with pytest.raises(RuntimeError):
        validate_jwt_secret(value)


def test_validate_jwt_secret_accepts_strong_value():
    secret = "a" * 32

    assert validate_jwt_secret(secret) == secret


def test_hash_password_salts_and_verifies():
    """同一密码两次哈希结果不同（盐），但两次都能验证通过。"""
    password = "s3cret-pass"

    hash_a = hash_password(password)
    hash_b = hash_password(password)

    # 库里存的绝不是明文
    assert hash_a != password
    # bcrypt 哈希有固定格式前缀
    assert hash_a.startswith("$2b$")
    # 盐是随机的：相同输入两次哈希不同
    assert hash_a != hash_b
    # 但两次结果都能通过校验
    assert verify_password(password, hash_a)
    assert verify_password(password, hash_b)


def test_verify_password_rejects_wrong_password():
    """错误密码返回 False，而不是抛异常。"""
    hashed = hash_password("correct-password")

    assert verify_password("wrong-password", hashed) is False
    assert verify_password("", hashed) is False
