from __future__ import annotations

from collections.abc import Iterator
# AbstractContextManager 用来表示一个可以放在 with 后面的对象
from contextlib import AbstractContextManager, contextmanager

from psycopg import Connection, Error   # 负责使用具体的 PostgreSQL 连接执行 SQL

from psycopg_pool import ConnectionPool  # 负责创建和管理一组 PostgreSQL 连接

from .settings import HealthStatus


# 创建 PostgreSQL 连接池
class Database:
    def __init__(
        self,
        dsn: str,
        *, # 表示它后面的参数必须通过名称传递
        min_size: int = 1,  # 表示连接池通常至少维护多少个连接
        max_size: int = 4,
        timeout: float = 5.0,
    ) -> None:
        self._timeout = timeout
        self._pool = ConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            open=False,
            kwargs={"autocommit": True},
        )

    def open(self) -> None:  # 打开连接池
        self._pool.open(wait=True, timeout=self._timeout)

    def close(self) -> None:
        self._pool.close()

    def connection(self) -> AbstractContextManager[Connection]:  # 从连接池获取连接,返回一个上下文管理器
        '''
        用法:
        with database.connection() as connection:
            connection.execute("SELECT 1")
        '''
        return self._pool.connection(timeout=self._timeout)

    @contextmanager
    # 从连接池借一个数据库连接，开启事务，把连接交给业务代码；业务代码全部成功就提交，出现异常就回滚，最后把连接归还连接池
    def transaction(self) -> Iterator[Connection]:
        with self.connection() as connection:
            with connection.transaction():
                yield connection # 暂停 transaction() 方法，把数据库连接交给外部 with 代码块使用

    def health(self) -> HealthStatus:
        try:
            with self.connection() as connection:
                connection.execute("SELECT 1")
        except Error:
            return HealthStatus(
                service="postgres",
                status="unavailable",
                detail="postgres unavailable",
            )
        return HealthStatus(
            service="postgres",
            status="ready",
            detail="postgres ready",
        )
