from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from psycopg import Error

from .database import Database


_MIGRATION_NAME = re.compile(
    r"(?P<version>\d{3})_[a-z][a-z0-9]*(?:_[a-z][a-z0-9]*)*\.sql"
)


# 保存证明迁移顺序和完整性所需的三项内容
_CREATE_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    checksum char(64) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()  -- 由 PostgreSQL 写入实际执行时间
)
"""


# 迁移错误
class MigrationError(RuntimeError):
    pass


# 保存一份已经检查过的迁移
@dataclass(frozen=True)
class _Migration:
    version: str  # 版本号
    path: Path  # 文件位置
    sql: str  # 迁移文件解码之后的 SQL 文本
    checksum: str  # 迁移文件的 SHA-256 校验值，也就是迁移文件内容的“数字指纹”,检查一个已经执行过的迁移文件，后来有没有被修改


# 迁移器的构造方式
class MigrationRunner:
    def __init__(
        self,
        database: Database, # 数据库对象
        migrations_path: str | Path,  # 迁移文件所在目录
    ) -> None:
        self._database = database
        self._migrations_path = Path(migrations_path)

    def current_version(self) -> str | None: # 查询数据库当前执行到哪个版本
        history = self._read_history()
        return max(history, default=None)

    def upgrade(self) -> tuple[str, ...]:
        migrations = self._discover_migrations()  # 读取本地迁移文件
        history = self._read_history()  # 读取数据库迁移记录
        files = {migration.version: migration for migration in migrations} # 把迁移列表转成字典

        # 检查已执行迁移是否仍然可靠
        for version, checksum in history.items():
            migration = files.get(version)
            if migration is None:
                raise MigrationError(
                    f"migration file is missing for applied version {version}"
                )
            if migration.checksum != checksum:
                raise MigrationError(
                    f"migration checksum mismatch for version {version}: "
                    f"{migration.path.name}"
                )

        applied: list[str] = []
        # 准备记录并执行迁移
        for migration in migrations:
            if migration.version in history:
                continue
            try:
                with self._database.transaction() as connection: # 为每一份迁移开启事务
                    connection.execute(migration.sql)
                    connection.execute(
                        "INSERT INTO schema_migrations (version, checksum) "
                        "VALUES (%s, %s)",
                        (migration.version, migration.checksum),
                    )
            except Error as exc:
                raise MigrationError(
                    f"cannot apply migration {migration.version}: "
                    f"{migration.path.name}"
                ) from exc
            applied.append(migration.version)

        return tuple(applied)

    # 读取数据库迁移历史
    def _read_history(self) -> dict[str, str]:
        try:
            with self._database.connection() as connection:
                connection.execute(_CREATE_MIGRATIONS_TABLE)  # 创建迁移历史表
                rows = connection.execute(  # 查询所有迁移记录
                    "SELECT version, checksum "
                    "FROM schema_migrations ORDER BY version"
                ).fetchall()
        except Error as exc:
            raise MigrationError("cannot read migration history") from exc
        return dict(rows)

    def _discover_migrations(self) -> tuple[_Migration, ...]:  # 发现迁移文件
        # 读取迁移目录
        try:
            # 遍历迁移目录中的所有直接子项
            paths = sorted(
                self._migrations_path.iterdir(),
                key=lambda path: path.name,
            )
        except OSError:
            raise MigrationError(
                f"cannot read migrations directory: {self._migrations_path}"
            ) from None

        migrations: list[_Migration] = []
        versions: set[str] = set()
        for path in paths:
            if not path.is_file() or path.suffix.casefold() != ".sql": # 过滤非 SQL 文件
                continue

            # 正则匹配成功的文件
            match = _MIGRATION_NAME.fullmatch(path.name)
            if match is None:
                raise MigrationError(f"invalid migration filename: {path.name}")

            version = match["version"]
            # 检查版本号是否重复
            if version in versions:
                raise MigrationError(
                    f"duplicate migration version {version}: {path.name}"
                )
            versions.add(version) # 确认没有重复后，将当前版本加入集合

            try:
                contents = path.read_bytes()
            except OSError:
                raise MigrationError(
                    f"cannot read migration file: {path.name}"
                ) from None

            checksum = sha256(contents).hexdigest()
            try:
                sql = contents.decode("utf-8")
            except UnicodeDecodeError:
                raise MigrationError(
                    f"migration file is not UTF-8: {path.name}"
                ) from None
            if not sql.strip():
                raise MigrationError(f"migration file is empty: {path.name}")

            migrations.append(
                _Migration(
                    version=version,
                    path=path,
                    sql=sql,
                    checksum=checksum,
                )
            )

        return tuple(sorted(migrations, key=lambda migration: migration.version))
