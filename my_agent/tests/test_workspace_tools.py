import importlib.util
from pathlib import Path

import pytest

from agents import (
    Workspace as PublicWorkspace,
    create_readonly_workspace_tools as create_public_readonly_workspace_tools,
)
from agents.tools import ToolExecutionError, ToolRegistry
from agents.workspace import Workspace
from agents.workspace_tools import (
    create_list_workspace_files_tool,
    create_readonly_workspace_tools,
    create_read_workspace_file_tool,
    create_search_workspace_text_tool,
)


def load_read_only_workspace_example():
    example_path = Path(__file__).parents[1] / "examples" / "read_only_workspace_agent.py"
    spec = importlib.util.spec_from_file_location("read_only_workspace_agent", example_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_list_workspace_files_returns_relative_entries(tmp_path: Path) -> None:
    (tmp_path / "src" / "agents").mkdir(parents=True)
    (tmp_path / "src" / "agents" / "agent.py").write_text("", encoding="utf-8")

    tool = create_list_workspace_files_tool(Workspace(root=tmp_path))
    result = tool.execute({"path": "src", "max_entries": 10})

    assert result["path"] == "src"
    assert result["entries"] == ["src/agents"]
    assert result["truncated"] is False


def test_list_workspace_files_skips_ignored_entries(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()

    tool = create_list_workspace_files_tool(Workspace(root=tmp_path))
    result = tool.execute({"path": ".", "max_entries": 10})

    assert result["entries"] == ["src"]


def test_list_workspace_files_limits_entries(tmp_path: Path) -> None:
    for name in ("a.py", "b.py", "c.py"):
        (tmp_path / name).write_text("", encoding="utf-8")

    tool = create_list_workspace_files_tool(Workspace(root=tmp_path))
    result = tool.execute({"path": ".", "max_entries": 2})

    assert result["entries"] == ["a.py", "b.py"]
    assert result["truncated"] is True


def test_read_workspace_file_returns_text_content(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "agent.py").write_text("print('hello')", encoding="utf-8")

    tool = create_read_workspace_file_tool(Workspace(root=tmp_path))
    result = tool.execute({"path": "src/agent.py", "max_chars": 100})

    assert result["path"] == "src/agent.py"
    assert result["content"] == "print('hello')"
    assert result["truncated"] is False


def test_read_workspace_file_truncates_long_content(tmp_path: Path) -> None:
    (tmp_path / "long.py").write_text("abcdef", encoding="utf-8")

    tool = create_read_workspace_file_tool(Workspace(root=tmp_path))
    result = tool.execute({"path": "long.py", "max_chars": 3})

    assert result["content"] == "abc"
    assert result["truncated"] is True


def test_read_workspace_file_rejects_directory(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()

    tool = create_read_workspace_file_tool(Workspace(root=tmp_path))

    with pytest.raises(ToolExecutionError, match="not a file"):
        tool.execute({"path": "src", "max_chars": 100})


def test_read_workspace_file_returns_decode_error(tmp_path: Path) -> None:
    (tmp_path / "data.bin").write_bytes(b"\xff\xfe\x00")

    tool = create_read_workspace_file_tool(Workspace(root=tmp_path))
    result = tool.execute({"path": "data.bin", "max_chars": 100})

    assert result["path"] == "data.bin"
    assert result["content"] == ""
    assert result["truncated"] is False
    assert "UTF-8" in result["error"]


def test_search_workspace_text_returns_line_matches(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "agent.py").write_text("alpha\nrun_agent_loop()\n", encoding="utf-8")

    tool = create_search_workspace_text_tool(Workspace(root=tmp_path))
    result = tool.execute({"query": "run_agent_loop", "path": "src", "max_results": 10})

    assert result["query"] == "run_agent_loop"
    assert result["path"] == "src"
    assert result["results"] == [
        {"path": "src/agent.py", "line": 2, "text": "run_agent_loop()"}
    ]
    assert result["truncated"] is False


def test_search_workspace_text_skips_ignored_files(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("needle", encoding="utf-8")
    (tmp_path / "src.py").write_text("needle", encoding="utf-8")

    tool = create_search_workspace_text_tool(Workspace(root=tmp_path))
    result = tool.execute({"query": "needle", "path": ".", "max_results": 10})

    assert result["results"] == [{"path": "src.py", "line": 1, "text": "needle"}]


def test_search_workspace_text_limits_results(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("needle\nneedle\n", encoding="utf-8")

    tool = create_search_workspace_text_tool(Workspace(root=tmp_path))
    result = tool.execute({"query": "needle", "path": ".", "max_results": 1})

    assert result["results"] == [{"path": "a.py", "line": 1, "text": "needle"}]
    assert result["truncated"] is True


def test_create_readonly_workspace_tools_returns_all_readonly_tools(tmp_path: Path) -> None:
    tools = create_readonly_workspace_tools(Workspace(root=tmp_path))

    assert [tool.name for tool in tools] == [
        "list_workspace_files",
        "read_workspace_file",
        "search_workspace_text",
    ]


def test_readonly_workspace_tools_can_register_to_tool_registry(tmp_path: Path) -> None:
    registry = ToolRegistry()

    for tool in create_readonly_workspace_tools(Workspace(root=tmp_path)):
        registry.register(tool)

    assert registry.get("list_workspace_files").name == "list_workspace_files"
    assert registry.get("read_workspace_file").name == "read_workspace_file"
    assert registry.get("search_workspace_text").name == "search_workspace_text"


def test_readonly_workspace_tools_are_exported_from_package(tmp_path: Path) -> None:
    tools = create_public_readonly_workspace_tools(PublicWorkspace(root=tmp_path))

    assert [tool.name for tool in tools] == [
        "list_workspace_files",
        "read_workspace_file",
        "search_workspace_text",
    ]


def test_read_only_workspace_agent_example_builds_readonly_agent(tmp_path: Path) -> None:
    example = load_read_only_workspace_example()

    setup = example.build_read_only_workspace_agent(root=tmp_path, model=object())
    tool_names = [tool.name for tool in setup.agent.tool_registry._tools.values()]

    assert setup.workspace.root == tmp_path.resolve()
    assert setup.run_config.context == {"workspace": setup.workspace}
    assert "list_workspace_files" in tool_names
    assert "read_workspace_file" in tool_names
    assert "search_workspace_text" in tool_names
    assert "python_executor" not in tool_names
