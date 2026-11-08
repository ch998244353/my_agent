from __future__ import annotations

import asyncio
import json
import os
import unittest
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx

from agents import (
    Agent,
    AgentCapabilities,
    AgentMemory,
    ModelErrorKind,
    ModelResponse,
    ModelResponseError,
    ModelSettings,
    ResponseCompleted,
    ResponseCreated,
    TextDelta,
    TextDone,
)
from agents.chat_backend import AppSettings, Conversation, StoredMessage, create_app
from agents.chat_backend.http import main as http_main
from agents.chat_backend.model_profiles import ModelProfileStore, validate_profile


class FakeKeyring:
    def __init__(self) -> None:
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        del self.values[(service, account)]


class StreamingAnswerModel:
    model = "fake-stream"

    async def stream_response(self, request):
        yield ResponseCreated(
            attempt_id=request.attempt_id,
            sequence=1,
            response_id="response-1",
        )
        yield TextDelta(
            attempt_id=request.attempt_id,
            sequence=2,
            output_index=0,
            content_index=0,
            delta="你",
        )
        yield TextDelta(
            attempt_id=request.attempt_id,
            sequence=3,
            output_index=0,
            content_index=0,
            delta="好",
        )
        yield TextDone(
            attempt_id=request.attempt_id,
            sequence=4,
            output_index=0,
            content_index=0,
            text="你好",
        )
        yield ResponseCompleted(
            attempt_id=request.attempt_id,
            sequence=5,
            response_id="response-1",
            output=[],
        )


class RetryAnswerModel:
    model = "retry-stream"

    def __init__(self) -> None:
        self.calls = 0

    async def stream_response(self, request):
        self.calls += 1
        yield ResponseCreated(
            attempt_id=request.attempt_id,
            sequence=1,
            response_id=f"response-{self.calls}",
        )
        if self.calls == 1:
            yield TextDelta(
                attempt_id=request.attempt_id,
                sequence=2,
                output_index=0,
                content_index=0,
                delta="旧",
            )
            raise ModelResponseError("retry me", kind=ModelErrorKind.SERVER)
        yield TextDelta(
            attempt_id=request.attempt_id,
            sequence=2,
            output_index=0,
            content_index=0,
            delta="新",
        )
        yield TextDone(
            attempt_id=request.attempt_id,
            sequence=3,
            output_index=0,
            content_index=0,
            text="新",
        )
        yield ResponseCompleted(
            attempt_id=request.attempt_id,
            sequence=4,
            response_id="response-2",
            output=[],
        )


class BlockingModel:
    model = "blocking-stream"

    def __init__(self) -> None:
        self.started = Event()

    async def stream_response(self, request):
        yield ResponseCreated(
            attempt_id=request.attempt_id,
            sequence=1,
            response_id="blocking-response",
        )
        self.started.set()
        await asyncio.sleep(0.2)
        yield ResponseCompleted(
            attempt_id=request.attempt_id,
            sequence=2,
            response_id="blocking-response",
            output=[],
        )


def make_app(*, profile_store=None, model=None, model_settings=None):
    settings = AppSettings(
        postgres_dsn="postgresql://unused:unused@127.0.0.1:1/unused"
    )
    selected_model = model or StreamingAnswerModel()
    return create_app(
        settings,
        lambda: Agent(
            memory=AgentMemory(),
            model=selected_model,
            model_settings=model_settings or ModelSettings(),
            capabilities=AgentCapabilities(final_answer_tool=False),
        ),
        model_profile_store=profile_store,
    )


class ChatWebTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_chat_uses_active_profile_without_openai_env(self) -> None:
        store = ModelProfileStore(FakeKeyring(), service="main-chat")
        profile = store.create(
            validate_profile(
                name="DeepSeek",
                provider="deepseek",
                model="deepseek-v4-flash",
                api_key="secret-value",
            )
        )
        store.activate(profile.id)
        captured = {}

        def capture_app(app, **_kwargs):
            captured["app"] = app

        with (
            patch.dict(
                os.environ,
                {
                    "MYAGENT_POSTGRES_DSN": (
                        "postgresql://unused:unused@127.0.0.1:1/unused"
                    ),
                    "MYAGENT_WORKSPACE_ROOT": str(
                        Path(__file__).resolve().parents[1]
                    ),
                    "OPENAI_API_KEY": "",
                },
            ),
            patch(
                "agents.chat_backend.http.ModelProfileStore",
                return_value=store,
            ),
            patch(
                "agents.chat_backend.http.model_for_profile",
                return_value=StreamingAnswerModel(),
            ),
            patch("agents.chat_backend.http.uvicorn.run", side_effect=capture_app),
        ):
            http_main()

        app = captured["app"]
        conversation_id = uuid4()
        now = datetime.now(UTC)
        app.state.conversations.get = lambda _id: Conversation(
            conversation_id, now, 1, "Profile chat"
        )
        app.state.messages.list = lambda _id, _limit=None: ()

        def add_batch(_id, items):
            return tuple(
                StoredMessage(
                    uuid4(), conversation_id, index, item.role, item.content, now
                )
                for index, item in enumerate(items, 1)
            )

        app.state.messages.add_batch = add_batch
        app.state.messages.set_run_trace = MagicMock()
        with patch(
            "agents.chat_backend.http.model_for_profile",
            return_value=StreamingAnswerModel(),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                response = await client.post(
                    f"/conversations/{conversation_id}/messages/stream",
                    json={"content": "hello"},
                )

        self.assertIn("event: done", response.text)
        self.assertIn('"content":"你好"', response.text)

    async def test_static_assets_have_security_headers(self) -> None:
        app = make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            index = await client.get("/")
            script = await client.get("/assets/app.js")
            style = await client.get("/assets/app.css")
        self.assertEqual(index.status_code, 200)
        self.assertIn("MyAgent 工作台", index.text)
        self.assertIn('id="load-models"', index.text)
        self.assertIn('href="/assets/app.css?v=20260814-9"', index.text)
        self.assertIn('src="/assets/app.js?v=20260814-9"', index.text)
        self.assertIn('id="conversation-profile"', index.text)
        self.assertIn('id="profile-editor"', index.text)
        self.assertIn("一个配置可以用于多个会话", index.text)
        self.assertIn('id="planning-panel"', index.text)
        self.assertIn('id="planning-final"', index.text)
        self.assertIn('id="planning-inline-actions"', index.text)
        self.assertIn('id="mode-switch"', index.text)
        self.assertLess(
            index.text.index('id="messages"'),
            index.text.index('id="planning-panel"'),
        )
        self.assertIn("default-src 'self'", index.headers["content-security-policy"])
        self.assertEqual(index.headers["x-content-type-options"], "nosniff")
        self.assertNotIn("localStorage", script.text)
        self.assertRegex(
            style.text,
            r"(?s)\.chat-stage\s*\{[^}]*min-height:\s*0;[^}]*overflow-y:\s*scroll",
        )
        self.assertNotIn("scroll-behavior: smooth", style.text)
        self.assertRegex(
            style.text,
            r"(?s)\.chat-stage::\-webkit-scrollbar\s*\{[^}]*width:\s*14px",
        )
        self.assertIn(".chat-stage::-webkit-scrollbar-track", style.text)
        self.assertIn(".model-launcher", style.text)
        self.assertIn(".run-trace", style.text)
        self.assertIn(".planning-panel", style.text)
        self.assertIn(".planning-inline-actions", style.text)
        self.assertIn(".planning-step.current", style.text)
        self.assertIn(".planning-progress", style.text)
        self.assertRegex(
            style.text,
            r"(?s)\.message\.user\s*\{[^}]*align-self:\s*flex-end;"
            r"[^}]*flex-direction:\s*row-reverse",
        )
        self.assertRegex(
            style.text,
            r"(?s)\.message\.user\s*>\s*div:last-child\s*\{"
            r"[^}]*flex:\s*1\s+1\s+0;[^}]*align-items:\s*flex-end",
        )
        self.assertNotIn("sessionStorage", script.text)
        self.assertNotIn("innerHTML", script.text)
        self.assertIn("runTraces: new Map()", script.text)
        self.assertIn("state.runTraces.set(finalReply.message_id", script.text)
        self.assertIn('$("#conversation-profile").addEventListener("change"', script.text)
        self.assertIn("delete payload.name;", script.text)
        self.assertIn("/model-profiles/models", script.text)
        self.assertIn("/planning/draft/stream", script.text)
        self.assertIn('runPlanningAction("tool-approval"', script.text)
        self.assertIn("setInterval(async () =>", script.text)
        self.assertIn("state.planning = payload", script.text)
        self.assertIn("planningProgress: []", script.text)
        self.assertIn("planningNotice: null", script.text)
        self.assertIn("planningDisclosure: null", script.text)
        self.assertRegex(
            script.text,
            r"(?s)async function runPlanningAction\(action, extra\).*?"
            r"return streamPlanning\(",
        )
        self.assertIn("计划批准已提交，正在开始执行", script.text)
        self.assertIn("本次工具调用已批准", script.text)
        self.assertIn("planning-notice", script.text)
        self.assertIn(".planning-notice", style.text)
        self.assertIn("不包含模型私有推理", script.text)
        self.assertIn("function beginPlanningDraft", script.text)
        self.assertIn("readPlanningStream(response, track)", script.text)
        self.assertIn("state.runTraces.set(done.message_id", script.text)
        self.assertIn("state.planningProgress.slice(-200)", script.text)
        self.assertIn("function planningComposerBlockReason()", script.text)
        self.assertIn("function renderPlanningInlineActions(plan)", script.text)
        self.assertIn("function planningInlineLabel(plan)", script.text)
        self.assertIn("function isPlanningDefinitionMessage(message)", script.text)
        self.assertIn("if (isPlanningDefinitionMessage(message)) return", script.text)
        self.assertIn("function renderPlanningFinalReply(plan)", script.text)
        self.assertIn('shell.className = "planning-shell"', script.text)
        self.assertIn("panel.append(shell)", script.text)
        self.assertIn("progress.open = false", script.text)
        self.assertIn("makeRunTrace(assistantNode.content, false)", script.text)
        self.assertIn("最终结果：", script.text)
        self.assertIn(".planning-shell", style.text)
        self.assertIn("function scrollPlanningToLatest()", script.text)
        self.assertNotIn("scrollIntoView", script.text)
        self.assertIn("计划等待你确认", script.text)
        self.assertIn("正在执行：", script.text)
        self.assertIn("最终验证未通过", script.text)
        self.assertIn("查看任务详情", script.text)
        self.assertIn("输入框上方操作区", script.text)
        self.assertIn("const accepted = state.composerMode", script.text)
        self.assertIn("if (accepted)", script.text)
        self.assertNotIn(
            '(locked && node.dataset.mode === "chat")',
            script.text,
        )

    async def test_model_profile_http_never_returns_api_key(self) -> None:
        store = ModelProfileStore(FakeKeyring(), service="web-test")
        app = make_app(profile_store=store)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            providers = await client.get("/model-providers")
            created = await client.post(
                "/model-profiles",
                json={
                    "name": "Local",
                    "provider": "ollama",
                    "model": "qwen3",
                    "base_url": "http://127.0.0.1:11434/v1",
                },
            )
            profile_id = created.json()["id"]
            activated = await client.post(
                f"/model-profiles/{profile_id}/activate"
            )
            listed = await client.get("/model-profiles")
        self.assertEqual(len(providers.json()), 13)
        self.assertEqual(created.status_code, 201)
        self.assertTrue(activated.json()["is_active"])
        self.assertNotIn("api_key", listed.json()[0])

    @patch(
        "agents.chat_backend.http.run_non_stream_model_response",
        new_callable=AsyncMock,
    )
    async def test_model_profile_draft_accepts_profile_form_payload(
        self,
        run_response: AsyncMock,
    ) -> None:
        run_response.return_value = ModelResponse("response", [], "OK", [])
        app = make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/model-profiles/test",
                json={
                    "name": "Local",
                    "provider": "ollama",
                    "model": "qwen3",
                    "base_url": "http://127.0.0.1:11434/v1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "message": "连接成功。"})
        run_response.assert_awaited_once()

    @patch("agents.chat_backend.http.model_for_profile")
    async def test_model_profile_models_uses_draft_credentials(
        self,
        build_model: MagicMock,
    ) -> None:
        client = build_model.return_value.client.with_options.return_value
        client.models.list.return_value = [
            SimpleNamespace(id="chat-b"),
            SimpleNamespace(id="chat-a"),
            SimpleNamespace(id="chat-b"),
            SimpleNamespace(id="text-embedding-test"),
            SimpleNamespace(id="bge-m3:latest"),
            SimpleNamespace(id=""),
        ]
        app = make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/model-profiles/models",
                json={
                    "provider": "ollama",
                    "base_url": "http://127.0.0.1:11434/v1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "ok": True,
                "models": ["chat-b", "chat-a"],
                "message": "已获取 2 个模型。",
            },
        )

    @patch(
        "agents.chat_backend.http.run_non_stream_model_response",
        new_callable=AsyncMock,
    )
    async def test_model_profile_test_returns_safe_authentication_reason(
        self,
        run_response: AsyncMock,
    ) -> None:
        run_response.side_effect = ModelResponseError(
            "provider-secret-detail",
            kind=ModelErrorKind.AUTHENTICATION,
        )
        app = make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/model-profiles/test",
                json={
                    "provider": "ollama",
                    "model": "qwen3",
                    "base_url": "http://127.0.0.1:11434/v1",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"ok": False, "message": "API Key 无效或没有调用权限。"},
        )
        self.assertNotIn("provider-secret-detail", response.text)

    async def test_sse_uses_real_runner_and_sends_done_after_commit(self) -> None:
        app = make_app()
        conversation_id = uuid4()
        now = datetime.now(UTC)
        app.state.conversations.get = lambda _conversation_id: Conversation(
            conversation_id,
            now,
            1,
            "Stream",
        )
        app.state.messages.list = lambda _conversation_id, _limit=None: ()
        committed = []

        def add_batch(saved_conversation_id, items):
            self.assertEqual(saved_conversation_id, conversation_id)
            result = tuple(
                StoredMessage(
                    uuid4(),
                    conversation_id,
                    index,
                    item.role,
                    item.content,
                    now,
                )
                for index, item in enumerate(items, 1)
            )
            committed.extend(result)
            return result

        app.state.messages.add_batch = add_batch
        persist_trace = MagicMock()
        app.state.messages.set_run_trace = persist_trace
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/conversations/{conversation_id}/messages/stream",
                json={"content": "hello"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["content-type"].split(";")[0],
            "text/event-stream",
        )
        self.assertLess(
            response.text.index('"text":"你"'),
            response.text.index('"text":"好"'),
        )
        self.assertIn('"reset":true', response.text)
        self.assertEqual(committed[-1].content, "你好")
        self.assertLess(
            response.text.index("Agent 循环已完成，正在保存结果"),
            response.text.index("event: done"),
        )
        persisted = persist_trace.call_args.args[1]
        self.assertEqual(persisted[0]["stage"], "retrieval")
        self.assertEqual(persisted[-1]["stage"], "done")
        self.assertIn('"run_trace"', response.text)

    async def test_retry_delta_resets_before_the_successful_attempt(self) -> None:
        model = RetryAnswerModel()
        app = make_app(
            model=model,
            model_settings=ModelSettings(
                max_attempts=2,
                backoff_initial=0,
                backoff_max=0,
                jitter_ratio=0,
                retry_budget=1,
                total_timeout=10,
            ),
        )
        conversation_id = uuid4()
        now = datetime.now(UTC)
        app.state.conversations.get = lambda _id: Conversation(
            conversation_id, now, 1, "Retry"
        )
        app.state.messages.list = lambda _id, _limit=None: ()
        committed = []

        def add_batch(_id, items):
            result = tuple(
                StoredMessage(
                    uuid4(), conversation_id, index, item.role, item.content, now
                )
                for index, item in enumerate(items, 1)
            )
            committed.extend(result)
            return result

        app.state.messages.add_batch = add_batch
        app.state.messages.set_run_trace = MagicMock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/conversations/{conversation_id}/messages/stream",
                json={"content": "hello"},
            )
        self.assertEqual(model.calls, 2)
        self.assertEqual(response.text.count('"reset":true'), 2)
        self.assertIn('"stage":"retry"', response.text)
        self.assertEqual(committed[-1].content, "新")
        self.assertIn('event: done', response.text)

    async def test_cancelling_streaming_service_does_not_commit(self) -> None:
        model = BlockingModel()
        app = make_app(model=model)
        conversation_id = uuid4()
        now = datetime.now(UTC)
        app.state.conversations.get = lambda _id: Conversation(
            conversation_id, now, 1, "Cancel"
        )
        app.state.messages.list = lambda _id, _limit=None: ()
        committed = []
        app.state.messages.add_batch = lambda *_args: committed.append(True)
        task = asyncio.create_task(
            app.state.chat_service.send_message(
                conversation_id,
                "hello",
                on_event=lambda _event: None,
            )
        )
        self.assertTrue(await asyncio.to_thread(model.started.wait, 1))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(committed, [])


if __name__ == "__main__":
    unittest.main()
