from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from threading import RLock
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4


ProfileProtocol = Literal["responses", "chat_completions"]
_SERVICE = "my-agent-model-profiles"
_INDEX_ACCOUNT = "index"


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    name: str
    protocol: ProfileProtocol
    base_url: str
    api_key_required: bool = True
    compatibility_note: str | None = None


PROVIDER_PRESETS = (
    ProviderPreset("openai", "OpenAI", "responses", "https://api.openai.com/v1"),
    ProviderPreset(
        "claude",
        "Claude（兼容模式）",
        "chat_completions",
        "https://api.anthropic.com/v1/",
        compatibility_note="兼容接口适合测试和比较，不覆盖 Claude 全部原生能力。",
    ),
    ProviderPreset(
        "gemini",
        "Gemini",
        "chat_completions",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
    ),
    ProviderPreset(
        "deepseek", "DeepSeek", "chat_completions", "https://api.deepseek.com"
    ),
    ProviderPreset(
        "moonshot", "Kimi / Moonshot", "chat_completions", "https://api.moonshot.cn/v1"
    ),
    ProviderPreset(
        "qwen",
        "阿里百炼 / Qwen",
        "chat_completions",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    ProviderPreset(
        "zhipu", "智谱 GLM", "chat_completions", "https://open.bigmodel.cn/api/paas/v4"
    ),
    ProviderPreset("xai", "xAI", "chat_completions", "https://api.x.ai/v1"),
    ProviderPreset(
        "mistral", "Mistral", "chat_completions", "https://api.mistral.ai/v1"
    ),
    ProviderPreset(
        "groq", "Groq", "chat_completions", "https://api.groq.com/openai/v1"
    ),
    ProviderPreset(
        "openrouter", "OpenRouter", "chat_completions", "https://openrouter.ai/api/v1"
    ),
    ProviderPreset(
        "ollama",
        "Ollama",
        "chat_completions",
        "http://127.0.0.1:11434/v1",
        api_key_required=False,
    ),
    ProviderPreset("custom", "自定义 OpenAI 兼容", "chat_completions", ""),
)
_PRESETS = {preset.id: preset for preset in PROVIDER_PRESETS}


class ModelProfileError(ValueError):
    pass


class ModelProfileNotFoundError(LookupError):
    pass


class ModelProfileStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelProfile:
    id: UUID
    name: str
    provider: str
    protocol: ProfileProtocol
    base_url: str
    model: str
    api_key: str | None
    is_active: bool = False

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    def public_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "name": self.name,
            "provider": self.provider,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "model": self.model,
            "is_active": self.is_active,
            "has_api_key": self.has_api_key,
        }


def _text(name: str, value: object, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelProfileError(f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > maximum:
        raise ModelProfileError(f"{name} is too long")
    return value


def _validate_base_url(value: object) -> str:
    value = _text("base_url", value, 2_000).rstrip("/")
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError:
        raise ModelProfileError("base_url is invalid") from None
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        parts.scheme not in ({"http", "https"} if local else {"https"})
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or bool(parts.query)
        or bool(parts.fragment)
    ):
        raise ModelProfileError(
            "base_url must use HTTPS, except HTTP on localhost, "
            "and contain no credentials"
        )
    return value


def validate_profile(
    *,
    name: object,
    provider: object,
    model: object,
    base_url: object | None = None,
    api_key: object | None = None,
    profile_id: UUID | None = None,
) -> ModelProfile:
    provider = _text("provider", provider, 50)
    preset = _PRESETS.get(provider)
    if preset is None:
        raise ModelProfileError("unknown model provider")
    resolved_url = preset.base_url if base_url is None else base_url
    if provider == "custom" and not resolved_url:
        raise ModelProfileError("custom provider requires base_url")
    resolved_key = None
    if api_key is not None:
        resolved_key = _text("api_key", api_key, 20_000)
    if preset.api_key_required and not resolved_key:
        raise ModelProfileError("api_key is required for this provider")
    return ModelProfile(
        id=profile_id or uuid4(),
        name=_text("name", name, 100),
        provider=provider,
        protocol=preset.protocol,
        base_url=_validate_base_url(resolved_url),
        model=_text("model", model, 200),
        api_key=resolved_key,
    )


class ModelProfileStore:
    """Stores profile metadata and secrets together inside the OS keyring."""

    def __init__(self, backend: Any | None = None, *, service: str = _SERVICE) -> None:
        self._backend = backend
        self._service = service
        self._lock = RLock()

    def _get(self, account: str) -> str | None:
        try:
            backend = self._backend
            if backend is None:
                import keyring

                backend = self._backend = keyring
            return backend.get_password(self._service, account)
        except Exception as exc:
            raise ModelProfileStoreError("system keyring is unavailable") from exc

    def _set(self, account: str, value: str) -> None:
        try:
            backend = self._backend
            if backend is None:
                import keyring

                backend = self._backend = keyring
            backend.set_password(self._service, account, value)
        except Exception as exc:
            raise ModelProfileStoreError(
                "system keyring could not save the profile"
            ) from exc

    def _delete(self, account: str) -> None:
        try:
            backend = self._backend
            if backend is None:
                import keyring

                backend = self._backend = keyring
            backend.delete_password(self._service, account)
        except Exception as exc:
            raise ModelProfileStoreError(
                "system keyring could not delete the profile"
            ) from exc

    def _index(self) -> dict[str, object]:
        raw = self._get(_INDEX_ACCOUNT)
        if raw is None:
            return {"version": 1, "active_id": None, "ids": []}
        try:
            value = json.loads(raw)
            ids = value["ids"]
            active_id = value.get("active_id")
            if (
                value.get("version") != 1
                or not isinstance(ids, list)
                or len(ids) > 100
                or not all(isinstance(item, str) for item in ids)
                or len(set(ids)) != len(ids)
                or (active_id is not None and not isinstance(active_id, str))
                or (active_id is not None and active_id not in ids)
            ):
                raise ValueError
            return {"version": 1, "active_id": active_id, "ids": ids}
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ModelProfileStoreError("model profile index is invalid") from exc

    def _read_profile(self, profile_id: str, active_id: str | None) -> ModelProfile:
        raw = self._get(profile_id)
        if raw is None:
            raise ModelProfileNotFoundError(profile_id)
        try:
            value = json.loads(raw)
            stored_id = UUID(value["id"])
            if str(stored_id) != profile_id:
                raise ValueError
            profile = validate_profile(
                profile_id=stored_id,
                name=value["name"],
                provider=value["provider"],
                base_url=value["base_url"],
                model=value["model"],
                api_key=value.get("api_key"),
            )
            return ModelProfile(
                **{**asdict(profile), "is_active": profile_id == active_id}
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ModelProfileStoreError("stored model profile is invalid") from exc

    @staticmethod
    def _payload(profile: ModelProfile) -> str:
        value = asdict(profile)
        value["id"] = str(profile.id)
        value.pop("is_active", None)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def list(self) -> tuple[ModelProfile, ...]:
        with self._lock:
            index = self._index()
            return tuple(
                self._read_profile(profile_id, index["active_id"])
                for profile_id in index["ids"]
            )

    def get(self, profile_id: UUID) -> ModelProfile:
        with self._lock:
            index = self._index()
            if str(profile_id) not in index["ids"]:
                raise ModelProfileNotFoundError(str(profile_id))
            return self._read_profile(str(profile_id), index["active_id"])

    def active(self) -> ModelProfile | None:
        with self._lock:
            index = self._index()
            active_id = index["active_id"]
            if active_id is None:
                return None
            return self._read_profile(active_id, active_id)

    def create(self, profile: ModelProfile) -> ModelProfile:
        with self._lock:
            index = self._index()
            existing = [
                self._read_profile(item, index["active_id"])
                for item in index["ids"]
            ]
            if any(
                item.name.casefold() == profile.name.casefold()
                for item in existing
            ):
                raise ModelProfileError("profile name already exists")
            account = str(profile.id)
            self._set(account, self._payload(profile))
            updated = {**index, "ids": [*index["ids"], account]}
            try:
                self._set(_INDEX_ACCOUNT, json.dumps(updated, separators=(",", ":")))
            except Exception:
                try:
                    self._delete(account)
                except ModelProfileStoreError:
                    pass
                raise
            return profile

    def update(self, profile_id: UUID, **changes: object) -> ModelProfile:
        with self._lock:
            current = self.get(profile_id)
            provider = changes.get("provider", current.provider)
            api_key = changes.get(
                "api_key",
                None if provider != current.provider else current.api_key,
            )
            profile = validate_profile(
                profile_id=profile_id,
                name=changes.get("name", current.name),
                provider=provider,
                model=changes.get("model", current.model),
                base_url=changes.get("base_url", current.base_url),
                api_key=api_key,
            )
            if any(
                item.id != profile_id
                and item.name.casefold() == profile.name.casefold()
                for item in self.list()
            ):
                raise ModelProfileError("profile name already exists")
            self._set(str(profile_id), self._payload(profile))
            return ModelProfile(**{**asdict(profile), "is_active": current.is_active})

    def activate(self, profile_id: UUID) -> ModelProfile:
        with self._lock:
            profile = self.get(profile_id)
            index = self._index()
            index["active_id"] = str(profile_id)
            self._set(_INDEX_ACCOUNT, json.dumps(index, separators=(",", ":")))
            return ModelProfile(**{**asdict(profile), "is_active": True})

    def delete(self, profile_id: UUID) -> None:
        with self._lock:
            self.get(profile_id)
            old_index = self._index()
            account = str(profile_id)
            new_index = {
                **old_index,
                "active_id": (
                    None
                    if old_index["active_id"] == account
                    else old_index["active_id"]
                ),
                "ids": [item for item in old_index["ids"] if item != account],
            }
            self._set(_INDEX_ACCOUNT, json.dumps(new_index, separators=(",", ":")))
            try:
                self._delete(account)
            except Exception:
                self._set(_INDEX_ACCOUNT, json.dumps(old_index, separators=(",", ":")))
                raise


def provider_public_dict(preset: ProviderPreset) -> dict[str, object]:
    return asdict(preset)


def model_for_profile(profile: ModelProfile) -> object:
    from openai import OpenAI

    from ..models import OpenAIChatCompletionsModel, OpenAIResponsesModel

    api_key = profile.api_key or "ollama"
    client = OpenAI(
        api_key=api_key,
        base_url=profile.base_url,
        max_retries=0,
    )
    if profile.protocol == "responses":
        return OpenAIResponsesModel(model=profile.model, client=client)
    return OpenAIChatCompletionsModel(
        model=profile.model,
        api_key=api_key,
        base_url=profile.base_url,
        client=client,
    )
