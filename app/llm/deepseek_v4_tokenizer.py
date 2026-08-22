"""Pinned DeepSeek V4 chat serialization and token counting."""

import hashlib
from collections.abc import Sequence
from pathlib import Path

from tokenizers import Tokenizer

from app.core.exceptions import LLMConfigurationError
from app.llm.contracts import LLMMessage, LLMRole

DEEPSEEK_V4_MODEL = "deepseek-v4-flash"
DEEPSEEK_V4_REVISION = "a7aaed80dd2df27620eb534454253ea25eb11c7a"
DEEPSEEK_V4_TOKENIZER_SHA256 = (
    "8f9f37ca37fdc4f5fd36d5cf4d3b0e8392edb4e894fd10cc0d70b4957c8633cf"
)
DEFAULT_TOKENIZER_PATH = (
    Path(__file__).parent / "resources" / "deepseek_v4_tokenizer.json"
)

_BOS = "<｜begin▁of▁sentence｜>"
_EOS = "<｜end▁of▁sentence｜>"
_USER = "<｜User｜>"
_ASSISTANT = "<｜Assistant｜>"
_NON_THINKING = "</think>"


class DeepSeekV4TokenCounter:
    """Count the provider contract's role subset with the official V4 assets."""

    def __init__(self, tokenizer: Tokenizer) -> None:
        self._tokenizer = tokenizer

    @classmethod
    def from_resource(
        cls,
        *,
        model: str,
        path: Path = DEFAULT_TOKENIZER_PATH,
    ) -> "DeepSeekV4TokenCounter":
        if model != DEEPSEEK_V4_MODEL:
            raise LLMConfigurationError(
                "tokenizer 只与 deepseek-v4-flash 固定版本匹配"
            )
        try:
            with path.open("rb") as resource:
                actual_hash = hashlib.file_digest(resource, "sha256").hexdigest()
        except OSError as exc:
            raise LLMConfigurationError("DeepSeek V4 tokenizer 资源不可用") from exc

        if actual_hash != DEEPSEEK_V4_TOKENIZER_SHA256:
            raise LLMConfigurationError("DeepSeek V4 tokenizer 校验失败")

        try:
            return cls(Tokenizer.from_file(str(path)))
        except Exception as exc:
            raise LLMConfigurationError("DeepSeek V4 tokenizer 加载失败") from exc

    def count_messages(self, messages: Sequence[LLMMessage]) -> int:
        prompt = serialize_deepseek_v4_chat(messages)
        return len(self._tokenizer.encode(prompt, add_special_tokens=False).ids)


def serialize_deepseek_v4_chat(messages: Sequence[LLMMessage]) -> str:
    """Render the contract's system/user/assistant subset in V4 chat mode.

    The format is pinned to the official encoder revision above. The provider
    contract deliberately excludes V4 tool/developer roles, so their much larger
    encoding rules do not belong here.
    """
    prompt = _BOS
    for index, message in enumerate(messages):
        if message.role is LLMRole.SYSTEM:
            prompt += message.content
        elif message.role is LLMRole.USER:
            prompt += _USER + message.content
        elif message.role is LLMRole.ASSISTANT:
            prompt += message.content + _EOS
        else:  # pragma: no cover - enum currently makes this unreachable
            raise ValueError(f"unsupported LLM role: {message.role}")

        next_role = messages[index + 1].role if index + 1 < len(messages) else None
        if message.role is LLMRole.USER and next_role in {
            LLMRole.ASSISTANT,
            None,
        }:
            prompt += _ASSISTANT + _NON_THINKING

    return prompt
