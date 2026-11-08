from __future__ import annotations

import unittest
from unittest.mock import patch

from agents import OpenAIChatCompletionsModel, OpenAIResponsesModel
from agents.chat_backend.model_profiles import (
    PROVIDER_PRESETS,
    ModelProfileError,
    ModelProfileNotFoundError,
    ModelProfileStore,
    ModelProfileStoreError,
    model_for_profile,
    validate_profile,
)


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        if (service, account) not in self.values:
            raise KeyError(account)
        del self.values[(service, account)]


class FailingIndexKeyring(FakeKeyring):
    def set_password(self, service: str, account: str, value: str) -> None:
        if account == "index":
            raise RuntimeError("unavailable")
        super().set_password(service, account, value)


class ModelProfileTests(unittest.TestCase):
    def test_presets_and_profile_lifecycle_keep_secrets_private(self) -> None:
        self.assertEqual(len(PROVIDER_PRESETS), 13)
        backend = FakeKeyring()
        store = ModelProfileStore(backend, service="test")
        profile = validate_profile(
            name="Daily",
            provider="deepseek",
            model="deepseek-chat",
            api_key="secret-value",
        )

        created = store.create(profile)
        self.assertNotIn("api_key", created.public_dict())
        self.assertTrue(created.public_dict()["has_api_key"])
        self.assertEqual(store.active(), None)

        active = store.activate(profile.id)
        self.assertTrue(active.is_active)
        restarted = ModelProfileStore(backend, service="test")
        self.assertEqual(restarted.active().model, "deepseek-chat")

        updated = restarted.update(profile.id, model="deepseek-reasoner")
        self.assertTrue(updated.is_active)
        self.assertEqual(updated.api_key, "secret-value")
        restarted.delete(profile.id)
        self.assertIsNone(restarted.active())
        with self.assertRaises(ModelProfileNotFoundError):
            restarted.get(profile.id)

    def test_validation_rejects_duplicate_names_and_unsafe_urls(self) -> None:
        backend = FakeKeyring()
        store = ModelProfileStore(backend, service="test")
        first = validate_profile(
            name="Local",
            provider="ollama",
            model="qwen3",
        )
        store.create(first)
        with self.assertRaises(ModelProfileError):
            store.create(
                validate_profile(
                    name="LOCAL",
                    provider="ollama",
                    model="llama3",
                )
            )
        with self.assertRaises(ModelProfileError):
            validate_profile(
                name="Bad",
                provider="custom",
                model="model",
                base_url="http://example.com/v1",
                api_key="secret",
            )

    def test_switching_provider_requires_a_new_provider_key(self) -> None:
        store = ModelProfileStore(FakeKeyring(), service="switch-test")
        profile = store.create(
            validate_profile(
                name="Remote",
                provider="openai",
                model="gpt-test",
                api_key="openai-secret",
            )
        )
        with self.assertRaises(ModelProfileError):
            store.update(profile.id, provider="deepseek")
        updated = store.update(
            profile.id,
            provider="deepseek",
            base_url="https://api.deepseek.com",
            api_key="deepseek-secret",
        )
        self.assertEqual(updated.provider, "deepseek")
        self.assertEqual(updated.api_key, "deepseek-secret")

    def test_create_rolls_back_when_keyring_index_write_fails(self) -> None:
        backend = FailingIndexKeyring()
        store = ModelProfileStore(backend, service="rollback-test")
        profile = validate_profile(
            name="Local",
            provider="ollama",
            model="qwen3",
        )
        with self.assertRaises(ModelProfileStoreError):
            store.create(profile)
        self.assertEqual(backend.values, {})

    def test_every_preset_builds_the_expected_openai_client(self) -> None:
        for preset in PROVIDER_PRESETS:
            base_url = (
                "https://compatible.example/v1"
                if preset.id == "custom"
                else None
            )
            profile = validate_profile(
                name=preset.name,
                provider=preset.id,
                model="model-id",
                base_url=base_url,
                api_key="secret" if preset.api_key_required else None,
            )
            with self.subTest(provider=preset.id), patch("openai.OpenAI") as client:
                adapter = model_for_profile(profile)
                client.assert_called_once_with(
                    api_key="secret" if preset.api_key_required else "ollama",
                    base_url=profile.base_url,
                    max_retries=0,
                )
                expected_type = (
                    OpenAIResponsesModel
                    if preset.protocol == "responses"
                    else OpenAIChatCompletionsModel
                )
                self.assertIsInstance(adapter, expected_type)


if __name__ == "__main__":
    unittest.main()
