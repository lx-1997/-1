"""
统一存储接入层（可换生产库的"seam"）。

设计目标：把"用哪种数据库"从业务代码里抽出来，集中由一个 `DEEPFOCUS_DATABASE_URL`
环境变量决定。默认仍是单机 SQLite（零配置、适合开发/演示），但只要把
`DEEPFOCUS_DATABASE_URL` 指向 `postgresql+psycopg://...` 即可整体切到托管 Postgres，
不需要改任何业务代码——这就是"摘掉演示态：SQLite 可换"的承诺所在。

当前先承载**认证子系统**（auth.py 的 User 表），后续的存储模块可逐步迁移到这里。
所有模型挂在统一的 `Base` 上，`init_storage()` 一次性建表。
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger("deepfocus.storage")


class Base(DeclarativeBase):
    """所有走统一存储层的 ORM 模型的公共基类。"""


_DEFAULT_SQLITE_PATH = Path(__file__).resolve().parents[1] / ".deepfocus_core.sqlite3"

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None


def database_url() -> str:
    """解析当前生效的数据库 URL。

    优先级：显式 `DEEPFOCUS_DATABASE_URL` > 自定义 SQLite 路径 > 默认本地 SQLite 文件。
    """
    explicit = os.getenv("DEEPFOCUS_DATABASE_URL")
    if explicit:
        return explicit.strip()
    sqlite_path = os.getenv("DEEPFOCUS_CORE_DB_PATH", str(_DEFAULT_SQLITE_PATH))
    return f"sqlite:///{sqlite_path}"


def is_sqlite() -> bool:
    return database_url().startswith("sqlite")


def is_managed_database() -> bool:
    """是否指向托管/生产级数据库（非默认本地 SQLite 文件）。"""
    return bool(os.getenv("DEEPFOCUS_DATABASE_URL")) and not is_sqlite()


def _build_engine() -> Engine:
    url = database_url()
    connect_args: dict = {}
    if url.startswith("sqlite"):
        # SQLite 在多线程（FastAPI 线程池）下必须放开 same-thread 检查。
        connect_args["check_same_thread"] = False
        # 默认/自定义文件路径确保父目录存在；:memory: 跳过。
        if url.startswith("sqlite:///"):
            raw_path = url[len("sqlite:///") :]
            if raw_path and raw_path != ":memory:":
                Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
    if url.startswith("sqlite"):
        # WAL + busy_timeout：几十并发读写不互锁、避免 "database is locked"
        from sqlalchemy import event as _sa_event

        @_sa_event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _rec):  # noqa: ANN001
            try:
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA busy_timeout=8000")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()
            except Exception:  # noqa: BLE001
                pass
    return engine


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        _engine = _build_engine()
        _session_factory = sessionmaker(
            bind=_engine, expire_on_commit=False, future=True
        )
    return _engine


def get_session_factory() -> sessionmaker:
    get_engine()
    assert _session_factory is not None
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """事务边界：正常提交、异常回滚、最终关闭。"""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_storage() -> None:
    """在当前 engine 上建好所有已注册模型的表（幂等）。

    建表前先确保各 ORM 模型模块已被导入（把模型注册到 Base.metadata），
    避免「按入口导入顺序不同导致漏建表」的隐患。函数级导入规避循环依赖。
    """
    from . import auth as _auth  # noqa: F401
    from . import user_prefs as _user_prefs  # noqa: F401
    from . import support_store as _support_store  # noqa: F401
    from . import referral as _referral  # noqa: F401
    from . import membership_codes as _membership_codes  # noqa: F401
    from . import engagement as _engagement  # noqa: F401

    Base.metadata.create_all(get_engine())
    # 幂等迁移：补后加的列（如兑换码 kind=体验卡标记）
    try:
        _membership_codes._ensure_code_columns()
    except Exception:  # noqa: BLE001
        logger.exception("ensure_code_columns migration failed")


def reset_engine_for_tests() -> None:
    """丢弃缓存的 engine/session 工厂，供测试切换到临时数据库后重新构建。"""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
