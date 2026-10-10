"""数据库对象归属过滤；HTTP 调用者必须传入已经验证的 JWT sub。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional


CURRENT_OWNER_USER_ID: ContextVar[Optional[str]] = ContextVar("current_owner_user_id", default=None)


def current_owner_id() -> Optional[str]:
    """供内部 AI/聚合读取使用；匿名请求及历史无归属任务没有隐式全库权限。"""
    return CURRENT_OWNER_USER_ID.get()


@contextmanager
def bind_owner(user_id: Optional[str]) -> Iterator[None]:
    """绑定已经验证的账号；隔离并发请求并在退出（含异常）时恢复上层身份。"""
    value = str(user_id).strip() if user_id is not None else None
    if value == "":
        raise ValueError("owner id must be non-empty")
    token = CURRENT_OWNER_USER_ID.set(value)
    try:
        yield
    finally:
        CURRENT_OWNER_USER_ID.reset(token)


def owner_filter(owner_user_id: Optional[str], is_admin: bool = False, *, column: str = "owner_user_id") -> tuple[str, list[str]]:
    # None 仅供已有进程内任务及单元测试调用；不能从 HTTP 的可选参数透传。
    if owner_user_id is None or is_admin:
        return "", []
    owner = str(owner_user_id).strip()
    if not owner:
        raise ValueError("owner_user_id must be a verified, non-empty account id")
    return f"{column} = ?", [owner]
