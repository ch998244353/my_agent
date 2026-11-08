from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit  # 用于拆解 URL

from .embedding import EMBEDDING_DIMENSIONS


# 配置值不能使用
class SettingsError(ValueError):
    pass


# 保存某个服务的一次检查结果
@dataclass(frozen=True)
class HealthStatus:
    service: str  # 服务名称
    status: Literal["ready", "unavailable", "disabled"]
    detail: str | None = None  # 可省略的说明

    def __post_init__(self) -> None:
        if not isinstance(self.service, str) or not self.service.strip():
            raise ValueError("service must be a non-empty string.")
        if self.status not in ("ready", "unavailable", "disabled"):
            raise ValueError("status must be ready, unavailable, or disabled.")


@dataclass(frozen=True)
class AppSettings:
    postgres_dsn: str  # PostgreSQL 数据库连接字符串
    workspace_root: Path = Path(".")
    workspace_test_command: str = "python -m pytest"
    embedding_model: str = "text-embedding-3-small"   # 嵌入模型负责把文本转换成向量，供 RAG 向量检索使用
    embedding_dimensions: int = EMBEDDING_DIMENSIONS    # 嵌入向量的维度
    http_host: str = "127.0.0.1"    # TTP 服务器监听的主机地址
    http_port: int = 8000   # HTTP 服务器监听端口
    cors_origins: tuple[str, ...] = ()  # 允许跨域访问后端的前端地址
    retrieval_top_k: int = 8  # RAG 检索阶段首先取回多少个候选文本块
    max_context_chunks: int = 4    # 从检索出的候选文本块中，最多选择多少个真正放进模型上下文
    max_context_chars: int = 12_000   # 允许注入模型上下文的检索文本最大字符数
    memory_retrieval_enabled: bool = False
    allow_real_model_smoke: bool = False    # 是否允许进行真实模型的冒烟测试

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> AppSettings:
        source = os.environ if environ is None else environ
        postgres_dsn = _required(source, "MYAGENT_POSTGRES_DSN")
        workspace_root = _required_directory(source, "MYAGENT_WORKSPACE_ROOT")
        workspace_test_command = source.get(
            "MYAGENT_WORKSPACE_TEST_COMMAND", "python -m pytest"
        ).strip()
        if not workspace_test_command:
            raise SettingsError(
                "MYAGENT_WORKSPACE_TEST_COMMAND must be a non-empty string."
            )

        embedding_model = source.get(
            "MYAGENT_EMBEDDING_MODEL", "text-embedding-3-small"
        ).strip()
        if not embedding_model:
            raise SettingsError("MYAGENT_EMBEDDING_MODEL must be a non-empty string.")

        embedding_dimensions = _positive_int(
            source, "MYAGENT_EMBEDDING_DIMENSIONS", EMBEDDING_DIMENSIONS
        )
        if embedding_dimensions != EMBEDDING_DIMENSIONS:
            raise SettingsError(
                "MYAGENT_EMBEDDING_DIMENSIONS must be "
                f"{EMBEDDING_DIMENSIONS}."
            )

        http_host = source.get("MYAGENT_HTTP_HOST", "127.0.0.1").strip()
        if http_host != "127.0.0.1":
            raise SettingsError("MYAGENT_HTTP_HOST must be 127.0.0.1.")

        http_port = _positive_int(source, "MYAGENT_HTTP_PORT", 8000)
        if http_port > 65535:
            raise SettingsError("MYAGENT_HTTP_PORT must be between 1 and 65535.")

        retrieval_top_k = _positive_int(source, "MYAGENT_RETRIEVAL_TOP_K", 8)
        max_context_chunks = _positive_int(
            source, "MYAGENT_MAX_CONTEXT_CHUNKS", 4
        )
        if max_context_chunks > retrieval_top_k:
            raise SettingsError(
                "MYAGENT_MAX_CONTEXT_CHUNKS must not exceed "
                "MYAGENT_RETRIEVAL_TOP_K."
            )

        return cls(
            postgres_dsn=postgres_dsn,
            workspace_root=workspace_root,
            workspace_test_command=workspace_test_command,
            embedding_model=embedding_model,
            embedding_dimensions=embedding_dimensions,
            http_host=http_host,
            http_port=http_port,
            cors_origins=_origins(source.get("MYAGENT_CORS_ORIGINS", "")),
            retrieval_top_k=retrieval_top_k,
            max_context_chunks=max_context_chunks,
            max_context_chars=_positive_int(
                source, "MYAGENT_MAX_CONTEXT_CHARS", 12_000
            ),
            memory_retrieval_enabled=_boolean(
                source, "MYAGENT_MEMORY_RETRIEVAL_ENABLED", False
            ),
            allow_real_model_smoke=_boolean(
                source, "MYAGENT_ALLOW_REAL_MODEL_SMOKE", False
            ),
        )


# 读取一个必填字符串环境变量
def _required(source: Mapping[str, str], name: str) -> str:
    value = source.get(name)
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"{name} is required.")
    return value.strip()


def _required_directory(source: Mapping[str, str], name: str) -> Path:
    path = Path(_required(source, name)).expanduser().resolve(strict=False)
    if not path.is_dir():
        raise SettingsError(f"{name} must point to an existing directory.")
    return path


def _positive_int(source: Mapping[str, str], name: str, default: int) -> int:
    raw_value = source.get(name)
    if raw_value is None:
        return default
    if not isinstance(raw_value, str):
        raise SettingsError(f"{name} must be a positive integer.")
    try:
        value = int(raw_value.strip())
    except ValueError:
        raise SettingsError(f"{name} must be a positive integer.") from None
    if value < 1:
        raise SettingsError(f"{name} must be a positive integer.")
    return value


def _boolean(source: Mapping[str, str], name: str, default: bool) -> bool:
    raw_value = source.get(name)
    if raw_value is None:
        return default
    if not isinstance(raw_value, str):
        raise SettingsError(f"{name} must be a boolean value.")
    value = raw_value.strip().casefold()
    if value in ("1", "true", "yes"):
        return True
    if value in ("0", "false", "no"):
        return False
    raise SettingsError(
        f"{name} must be one of: 1, 0, true, false, yes, no."
    )



# 把逗号分隔的 CORS 地址字符串解析成元组，并限制只能使用本机地址
def _origins(value: str) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise SettingsError("MYAGENT_CORS_ORIGINS must be a comma-separated string.")
    origins = tuple(origin.strip() for origin in value.split(",") if origin.strip())
    message = (
        "MYAGENT_CORS_ORIGINS accepts only local http/https origins without "
        "credentials, paths, queries, or fragments."
    )
    for origin in origins:
        try:
            parts = urlsplit(origin)
            _ = parts.port
        except ValueError:
            raise SettingsError(message) from None
        if (
            origin == "*"
            or parts.scheme not in ("http", "https")
            or parts.hostname not in ("localhost", "127.0.0.1", "::1")
            or parts.username is not None
            or parts.password is not None
            or bool(parts.path)
            or bool(parts.query)
            or bool(parts.fragment)
            or parts.netloc.endswith(":")
            or any(character.isspace() for character in origin)
        ):
            raise SettingsError(message)
    return origins
