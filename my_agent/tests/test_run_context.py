from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import (  # noqa: E402
    Agent,
    AgentMemory,
    RunConfig,
    RunContextWrapper,
    Runner,
    ToolCall,
)
from agents.run_context import (  # noqa: E402
    CONTEXT_SELECTED_FILES_KEY,
    CONTEXT_WORKSPACE_MANIFEST_KEY,
)
from agents.selected_files import SelectedFilesState  # noqa: E402
from agents.workspace_manifest import WorkspaceManifest  # noqa: E402


class ScriptedModel:
    def __init__(self, actions) -> None:
        self.actions = list(actions)
        self._index = 0

    def decide(self, messages, tool_specs):
        if self._index >= len(self.actions):
            return None
        action = self.actions[self._index]
        self._index += 1
        return action


class RunContextTestCase(unittest.TestCase):
    def test_run_context_wrapper_keeps_context_usage_and_metadata(self) -> None:
        business_context = {"user_id": "user_123"}

        run_context = RunContextWrapper(
            context=business_context,
            metadata={"request_id": "req_1"},
        )

        run_context.usage["requests"] = 1
        run_context.metadata["phase"] = "test"

        self.assertIs(run_context.context, business_context)
        self.assertEqual(run_context.usage["requests"], 1)
        self.assertEqual(run_context.metadata["request_id"], "req_1")
        self.assertEqual(run_context.metadata["phase"], "test")

    def test_run_context_wrapper_exposes_selected_files_from_context(self) -> None:
        selected_files = SelectedFilesState()
        selected_files.add_file(
            "src/agents/run_context.py",
            mode="read_only",
            reason="mentioned_by_user",
            source="context_mentions",
        )

        run_context = RunContextWrapper(
            context={CONTEXT_SELECTED_FILES_KEY: selected_files}
        )

        self.assertIs(run_context.selected_files, selected_files)

    def test_run_context_wrapper_selected_files_is_none_when_type_mismatch(self) -> None:
        run_context = RunContextWrapper(
            context={CONTEXT_SELECTED_FILES_KEY: "not selected files"}
        )

        self.assertIsNone(run_context.selected_files)

    def test_run_context_wrapper_exposes_workspace_manifest_from_context(
        self,
    ) -> None:
        manifest = WorkspaceManifest(root=PROJECT_ROOT)
        run_context = RunContextWrapper(
            context={CONTEXT_WORKSPACE_MANIFEST_KEY: manifest}
        )

        self.assertIs(run_context.workspace_manifest, manifest)

    def test_run_context_wrapper_workspace_manifest_is_none_when_type_mismatch(
        self,
    ) -> None:
        run_context = RunContextWrapper(
            context={CONTEXT_WORKSPACE_MANIFEST_KEY: "not manifest"}
        )

        self.assertIsNone(run_context.workspace_manifest)

    def test_run_context_wrapper_exposes_repo_context_from_context(self) -> None:
        from agents import run_context as run_context_module
        from agents.repo_context import RepoContext, RepoContextSection

        repo_context = RepoContext(
            sections=(
                RepoContextSection(
                    title="Selected files",
                    content="- src/agents/run_context.py [read_only]",
                    source="selected_files",
                    priority=10,
                ),
            )
        )
        run_context = RunContextWrapper(
            context={run_context_module.CONTEXT_REPO_CONTEXT_KEY: repo_context}
        )

        self.assertIs(run_context.repo_context, repo_context)

    def test_run_context_wrapper_repo_context_is_none_when_type_mismatch(self) -> None:
        from agents import run_context as run_context_module

        run_context = RunContextWrapper(
            context={run_context_module.CONTEXT_REPO_CONTEXT_KEY: "not repo context"}
        )

        self.assertIsNone(run_context.repo_context)

    def test_runner_creates_context_wrapper_from_run_config(self) -> None:
        business_context = {"tenant": "acme"}
        metadata = {"run_id": "run_1"}
        agent = Agent(
            memory=AgentMemory(),
            model=ScriptedModel(
                [
                    ToolCall("final_answer", {"answer": "done"}, "call_1"),
                ]
            ),
        )

        result = Runner.run_sync(
            agent,
            "Return done.",
            config=RunConfig(context=business_context, metadata=metadata),
        )

        self.assertTrue(result.reached_final_answer)
        self.assertIs(result.context_wrapper.context, business_context)
        self.assertEqual(result.context_wrapper.metadata, metadata)
        self.assertIsNot(result.context_wrapper.metadata, metadata)
        self.assertEqual(result.context_wrapper.usage, {})

    def test_each_run_gets_a_distinct_context_wrapper(self) -> None:
        config = RunConfig(metadata={"shared": "config"})
        first_agent = Agent(
            memory=AgentMemory(),
            model=ScriptedModel(
                [ToolCall("final_answer", {"answer": "first"}, "call_1")]
            ),
        )
        second_agent = Agent(
            memory=AgentMemory(),
            model=ScriptedModel(
                [ToolCall("final_answer", {"answer": "second"}, "call_2")]
            ),
        )

        first_result = Runner.run_sync(first_agent, "First.", config=config)
        second_result = Runner.run_sync(second_agent, "Second.", config=config)

        self.assertIsNot(first_result.context_wrapper, second_result.context_wrapper)
        self.assertIsNot(
            first_result.context_wrapper.metadata,
            second_result.context_wrapper.metadata,
        )
        self.assertEqual(first_result.context_wrapper.metadata, {"shared": "config"})
        self.assertEqual(second_result.context_wrapper.metadata, {"shared": "config"})


if __name__ == "__main__":
    unittest.main()
