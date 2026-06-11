from __future__ import annotations

import ast
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any

from .workspace import Workspace, WorkspacePathError
from .workspace_inventory import build_workspace_inventory


@dataclass(frozen=True)
class CodeLine:
    path: str
    line: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "text": self.text,
        }


# 表示一次文本命中 
@dataclass(frozen=True)
class CodeSearchMatch:
    path: str
    line: int
    text: str
    before: tuple[CodeLine, ...] = ()
    after: tuple[CodeLine, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "before", tuple(self.before))
        object.__setattr__(self, "after", tuple(self.after))

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "text": self.text,
            "before": [line.to_dict() for line in self.before],
            "after": [line.to_dict() for line in self.after],
        }



# 标识文件中的某个符号(类,函数,method等) 
@dataclass(frozen=True)
class FileOutlineSymbol:
    name: str
    kind: str
    line: int
    parent: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "line": self.line,
            "parent": self.parent,
        }


@dataclass(frozen=True)
class RelatedFileCandidate:
    path: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "reason": self.reason,
        }


# 拥有各种文件读取方法
class WorkspaceCodeReader:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    # 查询目标文件若干行
    def read_lines(
        self,
        path: str,
        *,
        start_line: int,
        end_line: int | None = None,
        max_lines: int = 120,
    ) -> dict[str, object]:
        file_path = self.workspace.ensure_readable_path(path)
        if not file_path.is_file():
            raise WorkspacePathError(path, self.workspace.root, "not a file")

        relative_path = self.workspace.relative_path(file_path).as_posix()
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            return {
                "path": relative_path,
                "start_line": max(1, int(start_line)),
                "end_line": 0,
                "lines": [],
                "truncated": False,
                "error": "File is not UTF-8 text.",
            }

        line_count = len(lines)
        start = min(max(1, int(start_line)), line_count + 1)
        requested_end = line_count if end_line is None else min(max(start, int(end_line)), line_count)
        limit = max(0, int(max_lines))
        limited_end = min(requested_end, start + limit - 1) if limit else start - 1
        truncated = limited_end < requested_end

        code_lines = [
            CodeLine(path=relative_path, line=line_number, text=lines[line_number - 1])
            for line_number in range(start, limited_end + 1)
        ]

        return {
            "path": relative_path,
            "start_line": start,
            "end_line": limited_end,
            "lines": [line.to_dict() for line in code_lines],
            "truncated": truncated,
        }


    #在workspace中每个文件每个行查询 text 出现位置
    def search_text(
        self,
        query: str,
        path: str = ".",
        max_results: int = 50,
        context_lines: int = 2,
        max_scan_files: int = 500,
    ) -> dict[str, object]:
        search_root = self.workspace.ensure_readable_path(path)
        search_path = self.workspace.relative_path(search_root).as_posix()
        limit = max(0, int(max_results))
        context_limit = max(0, int(context_lines))
        results: list[dict[str, Any]] = []
        truncated = False

        if not query or limit == 0:
            return {
                "query": query,
                "path": search_path,
                "results": results,
                "truncated": truncated,
            }

        files, scan_truncated = self._search_files(search_root, max_scan_files)
        truncated = scan_truncated
        for file_path in files:
            relative_path = self.workspace.relative_path(file_path).as_posix()
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue

            for line_number, line in enumerate(lines, start=1):
                if query not in line:
                    continue
                if len(results) >= limit:
                    truncated = True
                    return {
                        "query": query,
                        "path": search_path,
                        "results": results,
                        "truncated": truncated,
                    }

                before = [
                    CodeLine(relative_path, before_line_number, lines[before_line_number - 1])
                    for before_line_number in range(
                        max(1, line_number - context_limit),
                        line_number,
                    )
                ]
                after = [
                    CodeLine(relative_path, after_line_number, lines[after_line_number - 1])
                    for after_line_number in range(
                        line_number + 1,
                        min(len(lines), line_number + context_limit) + 1,
                    )
                ]
                results.append(
                    CodeSearchMatch(
                        path=relative_path,
                        line=line_number,
                        text=line,
                        before=tuple(before),
                        after=tuple(after),
                    ).to_dict()
                )

        return {
            "query": query,
            "path": search_path,
            "results": results,
            "truncated": truncated,
        }

    # 返回workspace 和inventory 约束后的file候选列表
    def _search_files(self, search_root: Path, max_scan_files: int) -> tuple[list[Path], bool]:
        if search_root.is_file():
            return [search_root], False

        scan_limit = max(0, int(max_scan_files))
        inventory = build_workspace_inventory(
            self.workspace,
            path=search_root,
            max_entries=scan_limit,
        )
        files: list[Path] = []
        for entry in inventory.entries:
            if len(files) >= scan_limit:
                return files, True
            if entry.kind != "file" or not entry.readable:
                continue
            try:
                files.append(self.workspace.ensure_readable_path(entry.path))
            except WorkspacePathError:
                continue
        return files, inventory.truncated


    # 能根据文件名查询workspace中文件 
    def find_files(
        self,
        query: str,
        max_results: int = 20,
        max_scan_files: int = 500,
    ) -> dict[str, object]:
        normalized_query = query.strip().replace("\\", "/")
        limit = max(0, int(max_results))
        scan_limit = max(0, int(max_scan_files))
        if not normalized_query or limit == 0:
            return {
                "query": query,
                "results": [],
                "truncated": False,
            }

        inventory = build_workspace_inventory(
            self.workspace,
            max_entries=scan_limit,
        )
        matches: list[dict[str, str]] = []
        for entry in inventory.entries:
            if entry.kind != "file" or not entry.readable:
                continue
            match_type = _file_match_type(entry.path, normalized_query)
            if match_type is None:
                continue
            matches.append(
                {
                    "path": entry.path,
                    "match_type": match_type,
                }
            )

        matches.sort(key=lambda match: (_MATCH_PRIORITY[match["match_type"]], match["path"]))
        truncated = inventory.truncated or len(matches) > limit
        return {
            "query": query,
            "results": matches[:limit],
            "truncated": truncated,
        }


    # 读取一个 Python 文件，解析它的代码结构，然后返回这个文件里的“符号大纲”，比如函数、类、方法等。
    def outline_file(
        self,
        path: str,
        max_symbols: int = 80,
    ) -> dict[str, object]:
        file_path = self.workspace.ensure_readable_path(path)
        if not file_path.is_file():
            raise WorkspacePathError(path, self.workspace.root, "not a file")

        relative_path = self.workspace.relative_path(file_path).as_posix()
        if file_path.suffix != ".py":
            return {
                "path": relative_path,
                "symbols": [],
                "truncated": False,
                "error": "Only Python files are supported.",
            }

        try:
            source = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {
                "path": relative_path,
                "symbols": [],
                "truncated": False,
                "error": "File is not UTF-8 text.",
            }

        try:
            tree = ast.parse(source, filename=relative_path)
        except SyntaxError as exc:
            return {
                "path": relative_path,
                "symbols": [],
                "truncated": False,
                "error": f"SyntaxError: {exc.msg} at line {exc.lineno}",
            }

        limit = max(0, int(max_symbols))
        symbols = _python_outline_symbols(tree)
        truncated = len(symbols) > limit
        return {
            "path": relative_path,
            "symbols": [symbol.to_dict() for symbol in symbols[:limit]],
            "truncated": truncated,
        }


    # 返回相关文件
    def find_related_files(
        self,
        path: str,
        max_results: int = 20,
        max_scan_files: int = 500,
    ) -> dict[str, object]:
        file_path = self.workspace.ensure_readable_path(path)
        if not file_path.is_file():
            raise WorkspacePathError(path, self.workspace.root, "not a file")

        relative_path = self.workspace.relative_path(file_path).as_posix()
        limit = max(0, int(max_results))
        scan_limit = max(0, int(max_scan_files))
        if limit == 0:
            return {
                "path": relative_path,
                "results": [],
                "truncated": False,
            }

        inventory = build_workspace_inventory(
            self.workspace,
            max_entries=scan_limit,
        )
        candidates: list[RelatedFileCandidate] = []
        seen_paths: set[str] = set()
        for entry in inventory.entries:
            if entry.kind != "file" or not entry.readable or entry.path == relative_path:
                continue
            reason = _related_file_reason(relative_path, entry.path)
            if reason is None or entry.path in seen_paths:
                continue
            seen_paths.add(entry.path)
            candidates.append(RelatedFileCandidate(path=entry.path, reason=reason))

        candidates.sort(key=lambda candidate: (_RELATED_REASON_PRIORITY[candidate.reason], candidate.path))
        truncated = inventory.truncated or len(candidates) > limit
        return {
            "path": relative_path,
            "results": [candidate.to_dict() for candidate in candidates[:limit]],
            "truncated": truncated,
        }


_MATCH_PRIORITY = {
    "path": 0,
    "basename": 1,
    "glob": 2,
    "path_contains": 3,
}


_RELATED_REASON_PRIORITY = {
    "same_directory_test": 0,
    "same_directory_source": 0,
    "tests_directory_test": 1,
    "source_name_matches_test": 1,
    "test_name_matches_source": 2,
}


def _file_match_type(path: str, query: str) -> str | None:
    basename = path.rsplit("/", 1)[-1]
    if path == query:
        return "path"
    if basename == query:
        return "basename"
    if any(marker in query for marker in "*?[]") and fnmatch(path, query):
        return "glob"
    if query.startswith(".") and path.endswith(query):
        return "glob"
    if query in path:
        return "path_contains"
    return None


def _related_file_reason(path: str, candidate_path: str) -> str | None:
    current = PurePosixPath(path)
    candidate = PurePosixPath(candidate_path)
    if current.suffix != candidate.suffix:
        return None
    if _source_stem(current.stem) != _source_stem(candidate.stem):
        return None

    current_is_test = _is_test_file(path)
    candidate_is_test = _is_test_file(candidate_path)
    if not current_is_test and candidate_is_test:
        if current.parent == candidate.parent:
            return "same_directory_test"
        if _has_tests_directory(candidate):
            return "tests_directory_test"
        return "test_name_matches_source"
    if current_is_test and not candidate_is_test:
        if current.parent == candidate.parent:
            return "same_directory_source"
        return "source_name_matches_test"
    return None


def _source_stem(stem: str) -> str:
    if stem.startswith("test_"):
        return stem.removeprefix("test_")
    if stem.endswith("_test"):
        return stem.removesuffix("_test")
    return stem


def _is_test_file(path: str) -> bool:
    parsed = PurePosixPath(path)
    return (
        parsed.stem.startswith("test_")
        or parsed.stem.endswith("_test")
        or _has_tests_directory(parsed)
    )


def _has_tests_directory(path: PurePosixPath) -> bool:
    return "tests" in path.parts


# 从整个 Python 文件的 AST 里，提取这个文件的类、顶层函数、类方法
def _python_outline_symbols(tree: ast.AST) -> list[FileOutlineSymbol]:
    symbols: list[FileOutlineSymbol] = []
    if not isinstance(tree, ast.Module):
        return symbols

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.append(FileOutlineSymbol(name=node.name, kind="class", line=node.lineno))
            symbols.extend(_python_class_methods(node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(FileOutlineSymbol(name=node.name, kind="function", line=node.lineno))

    symbols.sort(key=lambda symbol: symbol.line)
    return symbols

# 专门提取类方法的函数
def _python_class_methods(class_node: ast.ClassDef) -> list[FileOutlineSymbol]:
    return [
        FileOutlineSymbol(
            name=node.name,
            kind="method",
            line=node.lineno,
            parent=class_node.name,
        )
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


__all__ = [
    "CodeLine",
    "CodeSearchMatch",
    "FileOutlineSymbol",
    "RelatedFileCandidate",
    "WorkspaceCodeReader",
]
