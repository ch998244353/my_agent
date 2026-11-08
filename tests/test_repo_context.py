from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


class RepoContextTestCase(unittest.TestCase):
    def _load_repo_context_api(self):
        try:
            from agents.repo_context import RepoContext, RepoContextSection
        except ModuleNotFoundError as exc:
            self.fail(f"repo context API is missing: {exc}")
        return RepoContext, RepoContextSection

    def test_repo_context_section_renders_title_and_content(self) -> None:
        _, RepoContextSection = self._load_repo_context_api()

        section = RepoContextSection(
            title="Selected files",
            content="- src/app.py [editable]",
            source="selected_files",
            priority=10,
        )

        self.assertEqual(
            section.to_text(),
            "## Selected files\n- src/app.py [editable]",
        )

    def test_repo_context_renders_sections_in_priority_order(self) -> None:
        RepoContext, RepoContextSection = self._load_repo_context_api()

        context = RepoContext(
            sections=(
                RepoContextSection(
                    title="Search results",
                    content="src/app.py:10 def run()",
                    source="workspace_code",
                    priority=30,
                ),
                RepoContextSection(
                    title="Selected files",
                    content="- src/app.py [editable]",
                    source="selected_files",
                    priority=10,
                ),
            ),
            selected_paths=("src/app.py",),
            mentioned_symbols=("run",),
        )

        self.assertEqual(
            context.to_text(),
            "Repo context:\n"
            "Selected paths: src/app.py\n"
            "Mentioned symbols: run\n\n"
            "## Selected files\n"
            "- src/app.py [editable]\n\n"
            "## Search results\n"
            "src/app.py:10 def run()",
        )
        self.assertFalse(context.truncated)

    def test_repo_context_skips_empty_sections(self) -> None:
        RepoContext, RepoContextSection = self._load_repo_context_api()

        context = RepoContext(
            sections=(
                RepoContextSection(
                    title="Empty",
                    content="   ",
                    source="empty",
                    priority=0,
                ),
                RepoContextSection(
                    title="Mentioned symbols",
                    content="- RepoContext",
                    source="mentions",
                    priority=10,
                ),
            )
        )

        self.assertEqual(
            context.to_text(),
            "Repo context:\n\n"
            "## Mentioned symbols\n"
            "- RepoContext",
        )

    def test_repo_context_deduplicates_duplicate_sections(self) -> None:
        RepoContext, RepoContextSection = self._load_repo_context_api()

        duplicate_section = RepoContextSection(
            title="Mentioned paths",
            content="- src/app.py",
            source="context_mentions",
            priority=20,
        )

        context = RepoContext(sections=(duplicate_section, duplicate_section))

        self.assertEqual(context.ordered_sections(), (duplicate_section,))
        self.assertEqual(
            context.to_text(),
            "Repo context:\n\n"
            "## Mentioned paths\n"
            "- src/app.py",
        )

    def test_builder_renders_selected_files_section(self) -> None:
        from agents.repo_context import RepoContextBuilder
        from agents.selected_files import SelectedFilesState

        selected_files = SelectedFilesState()
        selected_files.add_file(
            "src/app.py",
            mode="editable",
            reason="manual_add",
            source="cli",
        )
        selected_files.add_file(
            "tests/test_app.py",
            mode="read_only",
            reason="mentioned_by_user",
            source="context_mentions",
        )

        context = RepoContextBuilder(selected_files=selected_files).build()

        self.assertEqual(context.selected_paths, ("src/app.py", "tests/test_app.py"))
        self.assertEqual(
            context.to_text(),
            "Repo context:\n"
            "Selected paths: src/app.py, tests/test_app.py\n\n"
            "## Selected files\n"
            "- src/app.py [editable] reason=manual_add source=cli\n"
            "- tests/test_app.py [read_only] reason=mentioned_by_user "
            "source=context_mentions",
        )

    def test_builder_skips_empty_selected_files_state(self) -> None:
        from agents.repo_context import RepoContextBuilder
        from agents.selected_files import SelectedFilesState

        context = RepoContextBuilder(selected_files=SelectedFilesState()).build()

        self.assertEqual(context.sections, ())
        self.assertEqual(context.selected_paths, ())
        self.assertEqual(context.to_text(), "Repo context:")

    def test_builder_renders_mention_sections(self) -> None:
        from agents.context_mentions import MentionCandidate
        from agents.repo_context import RepoContextBuilder

        mentions = (
            MentionCandidate(
                text="src/app.py",
                kind="path",
                confidence=1.0,
                matched_path="src/app.py",
                source="inventory",
            ),
            MentionCandidate(
                text="RepoContext",
                kind="symbol",
                confidence=0.7,
            ),
            MentionCandidate(
                text="src/app.py",
                kind="path",
                confidence=1.0,
                matched_path="src/app.py",
                source="inventory",
            ),
            MentionCandidate(
                text="test_repo_context.py",
                kind="test",
                confidence=0.85,
                matched_path="my_agent/tests/test_repo_context.py",
                source="inventory",
            ),
        )

        context = RepoContextBuilder(mentions=mentions).build()

        self.assertEqual(context.mentioned_symbols, ("RepoContext",))
        self.assertEqual(
            context.to_text(),
            "Repo context:\n"
            "Mentioned symbols: RepoContext\n\n"
            "## Mentioned paths\n"
            "- src/app.py [path] source=inventory\n"
            "- my_agent/tests/test_repo_context.py [test] source=inventory\n\n"
            "## Mentioned symbols\n"
            "- RepoContext confidence=0.70 source=text",
        )

    def test_builder_renders_inventory_summary_section(self) -> None:
        from agents.repo_context import RepoContextBuilder
        from agents.workspace_inventory import WorkspaceFileEntry, WorkspaceInventory

        inventory = WorkspaceInventory(
            root="/workspace/project",
            base_path=".",
            entries=(
                WorkspaceFileEntry(path="src", kind="directory"),
                WorkspaceFileEntry(path="src/app.py", kind="file", size_bytes=42),
                WorkspaceFileEntry(
                    path=".git",
                    kind="directory",
                    readable=False,
                    ignored=True,
                    reason="ignored_by_workspace_policy",
                ),
            ),
            truncated=True,
        )

        context = RepoContextBuilder(inventory=inventory, max_inventory_entries=2).build()

        self.assertEqual(
            context.to_text(),
            "Repo context:\n\n"
            "## Workspace inventory\n"
            "Base path: .\n"
            "Truncated: yes\n"
            "Entries shown: 2 of 3\n"
            "- src [directory] readable=True ignored=False reason=ok\n"
            "- src/app.py [file] size=42 readable=True ignored=False reason=ok",
        )

    def test_builder_renders_workspace_code_matches_for_symbols(self) -> None:
        from agents.context_mentions import MentionCandidate
        from agents.repo_context import RepoContextBuilder

        class FakeWorkspaceCodeReader:
            def search_text(
                self,
                query: str,
                path: str = ".",
                max_results: int = 50,
                context_lines: int = 2,
                max_scan_files: int = 500,
            ) -> dict[str, object]:
                return {
                    "query": query,
                    "path": path,
                    "results": [
                        {
                            "path": "my_agent/src/agents/repo_context.py",
                            "line": 67,
                            "text": "class RepoContextBuilder:",
                        }
                    ],
                    "truncated": False,
                }

        context = RepoContextBuilder(
            mentions=(
                MentionCandidate(
                    text="RepoContextBuilder",
                    kind="symbol",
                    confidence=0.7,
                ),
            ),
            workspace_code_reader=FakeWorkspaceCodeReader(),
        ).build()

        self.assertEqual(
            context.to_text(),
            "Repo context:\n"
            "Mentioned symbols: RepoContextBuilder\n\n"
            "## Mentioned symbols\n"
            "- RepoContextBuilder confidence=0.70 source=text\n\n"
            "## Workspace code matches\n"
            "- RepoContextBuilder: my_agent/src/agents/repo_context.py:67 "
            "class RepoContextBuilder:",
        )

    def test_build_task_repo_context_adds_repo_map_section_for_symbol_mention(self) -> None:
        from agents.repo_context import build_task_repo_context
        from agents.run_context import (
            CONTEXT_REPO_CONTEXT_KEY,
            CONTEXT_SELECTED_FILES_KEY,
            CONTEXT_WORKSPACE_KEY,
            RunContextWrapper,
        )
        from agents.selected_files import SelectedFilesState
        from agents.workspace import Workspace

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        workspace_path = Path(temp_dir.name)
        (workspace_path / "src").mkdir()
        (workspace_path / "src" / "service.py").write_text(
            "class Service:\n"
            "    def run(self):\n"
            "        return 'ok'\n",
            encoding="utf-8",
        )
        context_wrapper = RunContextWrapper(
            context={
                CONTEXT_WORKSPACE_KEY: Workspace(workspace_path),
                CONTEXT_SELECTED_FILES_KEY: SelectedFilesState(),
            }
        )

        repo_context = build_task_repo_context(
            "Use Service to inspect the service layer.",
            context_wrapper,
        )

        self.assertIs(repo_context, context_wrapper.context[CONTEXT_REPO_CONTEXT_KEY])
        repo_map_sections = [
            section for section in repo_context.sections if section.source == "repo_map"
        ]
        self.assertEqual(len(repo_map_sections), 1)
        self.assertEqual(repo_map_sections[0].priority, 15)
        self.assertIn("src/service.py", repo_map_sections[0].content)

    def test_builder_inserts_repo_map_section_between_selected_and_mentions(self) -> None:
        from agents.context_mentions import MentionCandidate
        from agents.repo_context import RepoContextBuilder, RepoContextSection
        from agents.selected_files import SelectedFilesState

        selected_files = SelectedFilesState()
        selected_files.add_file(
            "src/app.py",
            mode="editable",
            reason="manual_add",
            source="cli",
        )
        repo_map_section = RepoContextSection(
            title="Repo map",
            content="src/service.py score=100",
            source="repo_map",
            priority=15,
            metadata={"included_paths": ("src/service.py",), "truncated": False},
        )

        context = RepoContextBuilder(
            selected_files=selected_files,
            repo_map_section=repo_map_section,
            mentions=(
                MentionCandidate(
                    text="src/service.py",
                    kind="path",
                    confidence=1.0,
                    matched_path="src/service.py",
                    source="inventory",
                ),
            ),
        ).build()

        self.assertEqual(
            [section.source for section in context.ordered_sections()],
            ["selected_files", "repo_map", "context_mentions"],
        )
        self.assertIs(context.sections[1], repo_map_section)
        self.assertEqual(
            context.sections[1].metadata["included_paths"],
            ("src/service.py",),
        )

    def test_builder_truncates_low_priority_sections_when_over_budget(self) -> None:
        from agents.context_mentions import MentionCandidate
        from agents.repo_context import RepoContextBuilder
        from agents.selected_files import SelectedFilesState

        class FakeWorkspaceCodeReader:
            def search_text(
                self,
                query: str,
                path: str = ".",
                max_results: int = 50,
                context_lines: int = 2,
                max_scan_files: int = 500,
            ) -> dict[str, object]:
                return {
                    "query": query,
                    "path": path,
                    "results": [
                        {
                            "path": "my_agent/src/agents/repo_context.py",
                            "line": 171,
                            "text": "x" * 200,
                        }
                    ],
                    "truncated": False,
                }

        selected_files = SelectedFilesState()
        selected_files.add_file(
            "a.py",
            mode="editable",
            reason="manual_add",
            source="cli",
        )

        context = RepoContextBuilder(
            selected_files=selected_files,
            mentions=(
                MentionCandidate(
                    text="RepoContextBuilder",
                    kind="symbol",
                    confidence=0.7,
                ),
            ),
            workspace_code_reader=FakeWorkspaceCodeReader(),
            max_chars=220,
        ).build()
        text = context.to_text()

        self.assertTrue(context.truncated)
        self.assertLessEqual(len(text), 220)
        self.assertIn("## Selected files", text)
        self.assertIn("## Mentioned symbols", text)
        self.assertNotIn("## Workspace code matches", text)


if __name__ == "__main__":
    unittest.main()
