"""工具注册与受控执行的安全错误分类。"""


class ToolError(Exception):
    """所有可由工具执行边界安全识别的错误。"""


class ToolConfigurationError(ToolError):
    """开发者提供的工具注册或执行策略配置非法。"""


class ToolNotFoundError(ToolError):
    """模型请求的工具不在应用白名单中。"""


class ToolArgumentsValidationError(ToolError):
    """模型提供的 arguments 不符合已注册工具的 schema。"""


class ToolResourceUnavailableError(ToolError):
    """资源不存在或当前执行身份无权访问，不区分两种情况。"""


class ToolTimeoutError(ToolError):
    """工具 handler 未在应用规定时间内完成。"""


class ToolExecutionError(ToolError):
    """工具执行或结果序列化失败。"""


class ToolProtocolError(ToolError):
    """工具消息历史或 call ID 关联不满足应用协议。"""


class ToolLoopLimitError(ToolError):
    """工具预算耗尽后的强制收尾没有返回最终文本。"""
