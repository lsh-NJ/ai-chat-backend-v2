
# 所有 LLM 错误的基类
class LLMServiceError(Exception):
    """LLM错误"""

class LLMConfigurationError(LLMServiceError):
    """LLM配置错误"""

class LLMInputTooLongError(LLMServiceError):
    """必需输入已经超过模型输入预算。"""

class LLMTimeoutError(LLMServiceError):
    """模型请求超时"""

class LLMUpstreamError(LLMServiceError):
    """模型供应商返回错误，或者网络请求失败。"""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def retryable(self) -> bool:
        """网络错误（无状态码）和 5xx 可重试；4xx 不可重试。"""
        return self.status_code is None or self.status_code >= 500

class LLMStreamError(LLMServiceError):
    """模型流式传输过程中发生错误。"""

class LLMResponseFormatError(LLMServiceError):
    """模型响应格式不符合预期。"""

# 对话错误：
class ConversationNotFoundError(Exception):
    pass


# 密码与身份认证错误：
class UsernameAlreadyExistsError(Exception):
    """账户已存在"""

class UsernameOrPasswordError(Exception):
    """用户名或密码错误"""

class InvalidTokenError(Exception):
    pass
