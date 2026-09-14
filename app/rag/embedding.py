"""本地 Embedding 模型适配器（Week 16 Day 5）。

业务层只依赖 `Embedder` 契约：
- `embed_query(text)`：把查询编码成向量；
- `embed_documents(texts)`：批量编码文档 chunk；
- `dimension`：输出向量维度，必须与数据库 `vector(N)` 一致。

默认使用 `BAAI/bge-small-zh-v1.5`，输出 512 维向量。模型采用懒加载：
只有第一次真正编码时才 import `sentence_transformers` 和下载/加载模型，
避免应用启动和纯逻辑测试被重型依赖拖慢。
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

from app.core.exceptions import EmbeddingConfigurationError

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
DEFAULT_EMBEDDING_DIMENSION = 512


@runtime_checkable
class Embedder(Protocol):
    """provider-neutral 的文本向量化契约。"""

    @property
    def dimension(self) -> int:
        """返回向量维度。"""
        ...

    def embed_query(self, text: str) -> list[float]:
        """把查询文本编码成向量。"""
        ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """批量把文档文本编码成向量。"""
        ...


class SentenceTransformerEmbedder:
    """基于 sentence-transformers 的本地 Embedding 适配器。"""

    def __init__(
        self,
        model_name: str = DEFAULT_EMBEDDING_MODEL,
        *,
        device: str | None = None,
        normalize_embeddings: bool = True,
    ) -> None:
        if not isinstance(model_name, str) or not model_name.strip():
            raise ValueError("model_name must be a non-empty string")
        if device is not None and (
            not isinstance(device, str) or not device.strip()
        ):
            raise ValueError("device must be a non-empty string or None")
        if not isinstance(normalize_embeddings, bool):
            raise TypeError("normalize_embeddings must be a boolean")

        self._model_name = model_name.strip()
        self._device = device
        self._normalize_embeddings = normalize_embeddings
        self._model: Any | None = None
        self._dimension: int | None = None

    @property
    def dimension(self) -> int:
        self._ensure_loaded()
        assert self._dimension is not None
        return self._dimension

    def embed_query(self, text: str) -> list[float]:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        model = self._ensure_loaded()
        vector = model.encode(
            text,
            normalize_embeddings=self._normalize_embeddings,
        )
        return self._to_vector(vector)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if isinstance(texts, (str, bytes)) or not isinstance(texts, Sequence):
            raise TypeError("texts must be a sequence of strings")
        if any(not isinstance(text, str) for text in texts):
            raise TypeError("texts must contain strings")
        if not texts:
            return []
        model = self._ensure_loaded()
        vectors = model.encode(
            list(texts),
            normalize_embeddings=self._normalize_embeddings,
        )
        return [self._to_vector(vector) for vector in vectors]

    def _ensure_loaded(self) -> Any:
        if self._model is not None:
            return self._model

        try:
            from sentence_transformers import (  # type: ignore[import-not-found]
                SentenceTransformer,
            )
        except ImportError as exc:
            raise EmbeddingConfigurationError(
                "缺少 sentence-transformers，请安装后重试"
            ) from exc

        try:
            model = SentenceTransformer(self._model_name, device=self._device)
            dimension_getter = getattr(
                model,
                "get_embedding_dimension",
                None,
            )
            if dimension_getter is None:
                # 兼容旧版 sentence-transformers；新版本已改名为
                # get_embedding_dimension，因此这里不直接写属性访问，
                # 避免 IDE / 类型检查器报告 deprecated 警告。
                legacy_dimension_method = "get_sentence_embedding_dimension"
                dimension_getter = getattr(model, legacy_dimension_method)
            dimension = dimension_getter()
        except Exception as exc:
            raise EmbeddingConfigurationError(
                f"加载 embedding 模型失败: {self._model_name}"
            ) from exc

        if isinstance(dimension, bool) or not isinstance(dimension, int):
            raise EmbeddingConfigurationError(
                "embedding 模型没有返回合法维度"
            )
        if dimension <= 0:
            raise EmbeddingConfigurationError(
                "embedding 模型维度必须为正"
            )

        self._model = model
        self._dimension = dimension
        return model

    @staticmethod
    def _to_vector(vector: object) -> list[float]:
        tolist = getattr(vector, "tolist", None)
        if callable(tolist):
            vector = tolist()
        if isinstance(vector, (str, bytes)) or not isinstance(vector, Sequence):
            raise EmbeddingConfigurationError(
                "embedding 模型返回了非法向量类型"
            )
        result: list[float] = []
        for value in vector:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise EmbeddingConfigurationError(
                    "embedding 模型返回了非数字向量值"
                )
            result.append(float(value))
        return result


def create_embedder_from_env() -> Embedder:
    """从环境变量创建默认 embedder；模型本身仍然懒加载。"""
    model_name = os.environ.get(
        "EMBEDDING_MODEL",
        DEFAULT_EMBEDDING_MODEL,
    )
    device = os.environ.get("EMBEDDING_DEVICE") or None
    return SentenceTransformerEmbedder(model_name, device=device)
