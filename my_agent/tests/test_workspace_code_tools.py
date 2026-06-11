from pathlib import Path

from agents.tools import ToolRegistry
from agents.workspace import Workspace
from agents.workspace_code import WorkspaceCodeReader
from agents.workspace_code_tools import (
    create_find_related_workspace_files_tool,
    create_find_workspace_files_tool,
    create_outline_workspace_file_tool,
    create_read_workspace_lines_tool,
    create_search_workspace_code_tool,
    create_workspace_code_tools,
)


def test_read_workspace_lines_tool_executes_reader(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    tool = create_read_workspace_lines_tool(WorkspaceCodeReader(Workspace(tmp_path)))

    result = tool.execute({"path": "a.py", "start_line": 2, "end_line": 3})

    assert result["lines"] == [
        {"path": "a.py", "line": 2, "text": "two"},
        {"path": "a.py", "line": 3, "text": "three"},
    ]


def test_search_workspace_code_tool_returns_context(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("before\nneedle()\nafter\n", encoding="utf-8")
    tool = create_search_workspace_code_tool(WorkspaceCodeReader(Workspace(tmp_path)))

    result = tool.execute({"query": "needle", "context_lines": 1})

    assert result["results"][0]["before"] == [
        {"path": "a.py", "line": 1, "text": "before"}
    ]
    assert result["results"][0]["after"] == [
        {"path": "a.py", "line": 3, "text": "after"}
    ]


def test_find_workspace_files_tool_executes_reader(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "runner.py").write_text("", encoding="utf-8")
    tool = create_find_workspace_files_tool(WorkspaceCodeReader(Workspace(tmp_path)))

    result = tool.execute({"query": "runner.py"})

    assert result["results"] == [
        {"path": "src/runner.py", "match_type": "basename"}
    ]


def test_outline_workspace_file_tool_executes_reader(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("class Worker:\n    pass\n", encoding="utf-8")
    tool = create_outline_workspace_file_tool(WorkspaceCodeReader(Workspace(tmp_path)))

    result = tool.execute({"path": "a.py"})

    assert result["symbols"] == [
        {"name": "Worker", "kind": "class", "line": 1, "parent": None}
    ]


def test_find_related_workspace_files_tool_executes_reader(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "bar.py").write_text("", encoding="utf-8")
    (tmp_path / "tests" / "test_bar.py").write_text("", encoding="utf-8")
    tool = create_find_related_workspace_files_tool(WorkspaceCodeReader(Workspace(tmp_path)))

    result = tool.execute({"path": "src/bar.py"})

    assert result["results"] == [
        {"path": "tests/test_bar.py", "reason": "tests_directory_test"}
    ]


def test_create_workspace_code_tools_registers_all_tools(tmp_path: Path) -> None:
    registry = ToolRegistry()
    reader = WorkspaceCodeReader(Workspace(tmp_path))

    for tool in create_workspace_code_tools(reader):
        registry.register(tool)

    assert registry.get("read_workspace_lines").name == "read_workspace_lines"
    assert registry.get("search_workspace_code").name == "search_workspace_code"
    assert registry.get("find_workspace_files").name == "find_workspace_files"
    assert registry.get("outline_workspace_file").name == "outline_workspace_file"
    assert registry.get("find_related_workspace_files").name == "find_related_workspace_files"
