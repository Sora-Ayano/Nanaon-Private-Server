"""
FuncId 路由 — 将请求分发到正确的处理器
"""

import logging
from typing import Dict, Callable, Optional

from crypto.funcid import FuncId, ResultCode
from api.models import ParsedRequest, make_response

logger = logging.getLogger("nanaon.router")


class ApiRouter:
    """根据 FuncId 路由请求到对应处理器"""

    def __init__(self):
        self._handlers: Dict[int, Callable] = {}
        self._default_handler: Optional[Callable] = None

    def register(self, func_id: int):
        """装饰器：注册处理器"""
        def decorator(handler: Callable):
            self._handlers[func_id] = handler
            logger.debug(f"Registered handler for FuncId {func_id} ({FuncId(func_id).name})")
            return handler
        return decorator

    def set_default_handler(self, handler: Callable):
        """设置默认处理器 (用于未注册的 FuncId)"""
        self._default_handler = handler

    def dispatch(self, request: ParsedRequest) -> dict:
        """
        分发请求到对应处理器

        Args:
            request: 解析后的请求

        Returns:
            JSON 响应 dict
        """
        func_id = request.func_id

        # 查找处理器
        handler = self._handlers.get(func_id, self._default_handler)

        if handler is None:
            logger.warning(f"No handler for FuncId {func_id}")
            return make_response(
                data=None,
                code=ResultCode.ERROR_UNKNOWN,
            )

        # 记录请求
        func_name = FuncId(func_id).name if func_id in FuncId.__members__.values() else f"UNKNOWN_{func_id}"
        logger.info(f"[{func_name}] Dispatching...")

        try:
            result = handler(request)
            logger.info(f"[{func_name}] OK")
            return result
        except Exception as e:
            logger.error(f"[{func_name}] Error: {e}", exc_info=True)
            return make_response(
                data=None,
                code=ResultCode.ERROR_UNKNOWN,
            )


# 全局路由实例
router = ApiRouter()
