"""Concrete adapters for external LLM providers."""

from app.llm.providers.deepseek import DeepSeekConfig, DeepSeekProvider

__all__ = ["DeepSeekConfig", "DeepSeekProvider"]
