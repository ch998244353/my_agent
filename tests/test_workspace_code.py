from dataclasses import FrozenInstanceError

import pytest

from agents.workspace_code import (
    CodeLine,
    CodeSearchMatch,
    FileOutlineSymbol,
    RelatedFileCandidate,
    WorkspaceCodeReader,
)
from agents.workspace import Workspace


def test_code_line_serializes_stable_fields() -> None:
    line = CodeLine(path="src/pkg/a.py", line=3, text="value = 1")

    assert line.to_dict() == {
        "path": "src/pkg/a.py",
        "line": 3,
        "text": "value = 1",
    }

    with pytest.raises(FrozenInstanceError):
        line.text = "changed"


def test_code_search_match_serializes_context_lines() -> None:
    match = CodeSearchMatch(
        path="src/pkg/a.py",
        line=4,
        text="needle()",
        before=(CodeLine(path="src/pkg/a.py", line=3, text="def call():"),),
        after=(CodeLine(path="src/pkg/a.py", line=5, text="return None"),),
    )

    assert match.to_dict() == {
        "path": "src/pkg/a.py",
        "line": 4,
        "text": "needle()",
        "before": [
            {
                "path": "src/pkg/a.py",
                "line": 3,
                "text": "def call():",
            },
        ],
        "after": [
            {
                "path": "src/pkg/a.py",
                "line": 5,
                "text": "return None",
            },
        ],
    }


def test_file_outline_symbol_serializes_parent() -> None:
    symbol = FileOutlineSymbol(
        name="run",
        kind="method",
        line=12,
        parent="Worker",
    )

    assert symbol.to_dict() == {
        "name": "run",
        "kind": "method",
        "line": 12,
        "parent": "Worker",
    }


def test_related_file_candidate_serializes_reason() -> None:
    candidate = RelatedFileCandidate(
        path="tests/test_a.py",
        reason="test_name_matches_source",
    )

    assert candidate.to_dict() == {
        "path": "tests/test_a.py",
        "reason": "test_name_matches_source",
    }


def test_workspace_code_reader_reads_requested_line_range(tmp_path) -> None:
    package_dir = tmp_path / "pkg"
    package_dir.mkdir()
    file_path = package_dir / "a.py"
    file_path.write_text(
        "\n".join(f"line {index}" for index in range(1, 11)),
        encoding="utf-8",
    )
    reader = WorkspaceCodeReader(Workspace(tmp_path))

    result = reader.read_lines("pkg/a.py", start_line=3, end_line=7)

    assert result == {
        "path": "pkg/a.py",
        "start_line": 3,
        "end_line": 7,
        "lines": [
            {"path": "pkg/a.py", "line": 3, "text": "line 3"},
            {"path": "pkg/a.py", "line": 4, "text": "line 4"},
            {"path": "pkg/a.py", "line": 5, "text": "line 5"},
            {"path": "pkg/a.py", "line": 6, "text": "line 6"},
            {"path": "pkg/a.py", "line": 7, "text": "line 7"},
        ],
        "truncated": False,
    }


def test_workspace_code_reader_limits_lines_with_max_lines(tmp_path) -> None:
    file_path = tmp_path / "a.py"
    file_path.write_text(
        "\n".join(f"line {index}" for index in range(1, 8)),
        encoding="utf-8",
    )
    reader = WorkspaceCodeReader(Workspace(tmp_path))

    result = reader.read_lines("a.py", start_line=2, end_line=6, max_lines=3)

    assert result["start_line"] == 2
    assert result["end_line"] == 4
    assert result["lines"] == [
        {"path": "a.py", "line": 2, "text": "line 2"},
        {"path": "a.py", "line": 3, "text": "line 3"},
        {"path": "a.py", "line": 4, "text": "line 4"},
    ]
    assert result["truncated"] is True


def test_workspace_code_reader_returns_utf8_error(tmp_path) -> None:
    file_path = tmp_path / "binary.dat"
    file_path.write_bytes(b"\xff\xfe\x00")
    reader = WorkspaceCodeReader(Workspace(tmp_path))

    result = reader.read_lines("binary.dat", start_line=1)

    assert result == {
        "path": "binary.dat",
        "start_line": 1,
        "end_line": 0,
        "lines": [],
        "truncated": False,
        "error": "File is not UTF-8 text.",
    }


def test_workspace_code_reader_search_text_returns_context(tmp_path) -> None:
    file_path = tmp_path / "pkg" / "a.py"
    file_path.parent.mkdir()
    file_path.write_text(
        "\n".join(
            [
                "before one",
                "before two",
                "needle()",
                "after one",
                "after two",
            ]
        ),
        encoding="utf-8",
    )
    reader = WorkspaceCodeReader(Workspace(tmp_path))

    result = reader.search_text("needle", path="pkg", context_lines=1)

    assert result == {
        "query": "needle",
        "path": "pkg",
        "results": [
            {
                "path": "pkg/a.py",
                "line": 3,
                "text": "needle()",
                "before": [
                    {"path": "pkg/a.py", "line": 2, "text": "before two"},
                ],
                "after": [
                    {"path": "pkg/a.py", "line": 4, "text": "after one"},
                ],
            },
        ],
        "truncated": False,
    }


def test_workspace_code_reader_search_text_respects_max_results(tmp_path) -> None:
    (tmp_path / "a.py").write_text("needle one\nneedle two\n", encoding="utf-8")
    reader = WorkspaceCodeReader(Workspace(tmp_path))

    result = reader.search_text("needle", max_results=1)

    assert len(result["results"]) == 1
    assert result["truncated"] is True


def test_workspace_code_reader_search_text_skips_ignored_files(tmp_path) -> None:
    (tmp_path / "visible.py").write_text("needle visible\n", encoding="utf-8")
    ignored_dir = tmp_path / ".codegraph"
    ignored_dir.mkdir()
    (ignored_dir / "hidden.py").write_text("needle hidden\n", encoding="utf-8")
    reader = WorkspaceCodeReader(Workspace(tmp_path))

    result = reader.search_text("needle")

    assert [match["path"] for match in result["results"]] == ["visible.py"]


def test_workspace_code_reader_find_files_matches_basename(tmp_path) -> None:
    source_dir = tmp_path / "src" / "agents"
    test_dir = tmp_path / "tests"
    source_dir.mkdir(parents=True)
    test_dir.mkdir()
    (source_dir / "context_chunks.py").write_text("", encoding="utf-8")
    (test_dir / "test_context_chunks.py").write_text("", encoding="utf-8")
    reader = WorkspaceCodeReader(Workspace(tmp_path))

    result = reader.find_files("context_chunks.py")

    assert result == {
        "query": "context_chunks.py",
        "results": [
            {
                "path": "src/agents/context_chunks.py",
                "match_type": "basename",
            },
            {
                "path": "tests/test_context_chunks.py",
                "match_type": "path_contains",
            },
        ],
        "truncated": False,
    }


def test_workspace_code_reader_find_files_matches_glob_and_path_segment(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "runner.py").write_text("", encoding="utf-8")
    (tmp_path / "docs" / "runner.md").write_text("", encoding="utf-8")
    reader = WorkspaceCodeReader(Workspace(tmp_path))

    glob_result = reader.find_files("*.py")
    segment_result = reader.find_files("docs")

    assert glob_result["results"] == [
        {
            "path": "src/runner.py",
            "match_type": "glob",
        },
    ]
    assert segment_result["results"] == [
        {
            "path": "docs/runner.md",
            "match_type": "path_contains",
        },
    ]


def test_workspace_code_reader_find_files_respects_max_results(tmp_path) -> None:
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    reader = WorkspaceCodeReader(Workspace(tmp_path))

    result = reader.find_files("*.py", max_results=1)

    assert result["results"] == [
        {
            "path": "a.py",
            "match_type": "glob",
        },
    ]
    assert result["truncated"] is True


def test_workspace_code_reader_outline_file_returns_python_symbols(tmp_path) -> None:
    package_dir = tmp_path / "pkg"
    package_dir.mkdir()
    (package_dir / "worker.py").write_text(
        "\n".join(
            [
                "class Worker:",
                "    def run(self):",
                "        pass",
                "",
                "async def build_worker():",
                "    return Worker()",
            ]
        ),
        encoding="utf-8",
    )
    reader = WorkspaceCodeReader(Workspace(tmp_path))

    result = reader.outline_file("pkg/worker.py")

    assert result == {
        "path": "pkg/worker.py",
        "symbols": [
            {"name": "Worker", "kind": "class", "line": 1, "parent": None},
            {"name": "run", "kind": "method", "line": 2, "parent": "Worker"},
            {"name": "build_worker", "kind": "function", "line": 5, "parent": None},
        ],
        "truncated": False,
    }


def test_workspace_code_reader_outline_file_returns_syntax_error(tmp_path) -> None:
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    reader = WorkspaceCodeReader(Workspace(tmp_path))

    result = reader.outline_file("broken.py")

    assert result["path"] == "broken.py"
    assert result["symbols"] == []
    assert result["truncated"] is False
    assert result["error"].startswith("SyntaxError:")


def test_workspace_code_reader_outline_file_rejects_non_python_file(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("class NotPython:\n", encoding="utf-8")
    reader = WorkspaceCodeReader(Workspace(tmp_path))

    result = reader.outline_file("notes.txt")

    assert result == {
        "path": "notes.txt",
        "symbols": [],
        "truncated": False,
        "error": "Only Python files are supported.",
    }


def test_workspace_code_reader_find_related_files_returns_test_candidates(tmp_path) -> None:
    source_dir = tmp_path / "src" / "foo"
    tests_dir = tmp_path / "tests"
    source_dir.mkdir(parents=True)
    tests_dir.mkdir()
    (source_dir / "bar.py").write_text("", encoding="utf-8")
    (source_dir / "bar_test.py").write_text("", encoding="utf-8")
    (source_dir / "test_bar.py").write_text("", encoding="utf-8")
    (tests_dir / "test_bar.py").write_text("", encoding="utf-8")
    (tests_dir / "test_other.py").write_text("", encoding="utf-8")
    reader = WorkspaceCodeReader(Workspace(tmp_path))

    result = reader.find_related_files("src/foo/bar.py")

    assert result == {
        "path": "src/foo/bar.py",
        "results": [
            {"path": "src/foo/bar_test.py", "reason": "same_directory_test"},
            {"path": "src/foo/test_bar.py", "reason": "same_directory_test"},
            {"path": "tests/test_bar.py", "reason": "tests_directory_test"},
        ],
        "truncated": False,
    }


def test_workspace_code_reader_find_related_files_maps_test_to_source(tmp_path) -> None:
    source_dir = tmp_path / "src" / "foo"
    tests_dir = tmp_path / "tests"
    source_dir.mkdir(parents=True)
    tests_dir.mkdir()
    (source_dir / "bar.py").write_text("", encoding="utf-8")
    (source_dir / "other.py").write_text("", encoding="utf-8")
    (tests_dir / "test_bar.py").write_text("", encoding="utf-8")
    reader = WorkspaceCodeReader(Workspace(tmp_path))

    result = reader.find_related_files("tests/test_bar.py")

    assert result == {
        "path": "tests/test_bar.py",
        "results": [
            {"path": "src/foo/bar.py", "reason": "source_name_matches_test"},
        ],
        "truncated": False,
    }


def test_workspace_code_reader_find_related_files_respects_max_results(tmp_path) -> None:
    source_dir = tmp_path / "src" / "foo"
    tests_dir = tmp_path / "tests"
    source_dir.mkdir(parents=True)
    tests_dir.mkdir()
    (source_dir / "bar.py").write_text("", encoding="utf-8")
    (source_dir / "bar_test.py").write_text("", encoding="utf-8")
    (tests_dir / "test_bar.py").write_text("", encoding="utf-8")
    reader = WorkspaceCodeReader(Workspace(tmp_path))

    result = reader.find_related_files("src/foo/bar.py", max_results=1)

    assert result == {
        "path": "src/foo/bar.py",
        "results": [
            {"path": "src/foo/bar_test.py", "reason": "same_directory_test"},
        ],
        "truncated": True,
    }
