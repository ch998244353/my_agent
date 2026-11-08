from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass

from .workspace import Workspace, WorkspacePathError

_JS_CLASS_RE = re.compile(r"^\s*class\s+([A-Za-z_$][A-Za-z0-9_$]*)\b")
_JS_FUNCTION_RE = re.compile(
    r"^\s*(?:export\s+)?function\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*\("
)

# repo-map 的基础符号证据
@dataclass(frozen=True)
class SymbolTag:
    path: str
    name: str
    kind: str
    line: int
    role: str = "definition"


# 失败证据结构。它接收文件路径和失败原因，输出不可变诊断对象
@dataclass(frozen=True)
class SymbolExtractionDiagnostic:
    path: str
    reason: str

# 提取函数、类、方法等 SymbolTag，最后返回所有symboltag
def extract_workspace_symbol_tags(
    workspace: Workspace,
    paths: Iterable[str],
    max_files: int | None = None,
) -> tuple[tuple[SymbolTag, ...], tuple[SymbolExtractionDiagnostic, ...]]:
    tags: list[SymbolTag] = []
    diagnostics: list[SymbolExtractionDiagnostic] = []
    for index, path in enumerate(paths):
        if max_files is not None and index >= max_files:
            break
        try:
            file_path = workspace.ensure_readable_path(path)
            relative_path = workspace.relative_path(file_path).as_posix()
            suffix = file_path.suffix.lower()
            if suffix not in {".py", ".js", ".ts"}:
                diagnostics.append(
                    SymbolExtractionDiagnostic(
                        path=relative_path,
                        reason=f"Unsupported file type: {suffix or '<none>'}",
                    )
                )
                continue

            source = file_path.read_text(encoding="utf-8")
            if suffix == ".py":
                tree = ast.parse(source, filename=relative_path)
                tags.extend(_python_definition_tags(relative_path, tree))
            else:
                tags.extend(_javascript_definition_tags(relative_path, source))
        except WorkspacePathError as exc:
            diagnostics.append(SymbolExtractionDiagnostic(path=str(path), reason=exc.reason))
            continue
        except UnicodeDecodeError:
            diagnostics.append(
                SymbolExtractionDiagnostic(path=relative_path, reason="File is not UTF-8 text.")
            )
            continue
        except SyntaxError as exc:
            diagnostics.append(
                SymbolExtractionDiagnostic(
                    path=relative_path,
                    reason=f"SyntaxError: {exc.msg} at line {exc.lineno}",
                )
            )
            continue
        except OSError as exc:
            diagnostics.append(SymbolExtractionDiagnostic(path=relative_path, reason=str(exc)))
            continue

    unique_tags = list(dict.fromkeys(tags))
    unique_tags.sort(key=lambda tag: (tag.path, tag.line, tag.kind, tag.name))
    return tuple(unique_tags), tuple(diagnostics)


# 从一个 Python 文件的 AST 里提取类、函数、方法，然后转成 SymbolTag
def _python_definition_tags(path: str, tree: ast.Module) -> list[SymbolTag]:
    tags: list[SymbolTag] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            tags.append(SymbolTag(path=path, name=node.name, kind="class", line=node.lineno))
            tags.extend(_python_method_tags(path, node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tags.append(SymbolTag(path=path, name=node.name, kind="function", line=node.lineno))
    return tags


# 扫描 Python 文件的最外层结构，提取顶层 class 和顶层 function
def _python_method_tags(path: str, class_node: ast.ClassDef) -> list[SymbolTag]:
    return [
        SymbolTag(path=path, name=node.name, kind="method", line=node.lineno)
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


# ponytail: 行级 JS/TS 定义提取；需要 JSX/复杂 export 时再换 parser。
def _javascript_definition_tags(path: str, source: str) -> list[SymbolTag]:
    tags: list[SymbolTag] = []
    for line_number, line in enumerate(source.splitlines(), start=1):
        class_match = _JS_CLASS_RE.match(line)
        if class_match:
            tags.append(
                SymbolTag(path=path, name=class_match.group(1), kind="class", line=line_number)
            )
            continue
        function_match = _JS_FUNCTION_RE.match(line)
        if function_match:
            tags.append(
                SymbolTag(
                    path=path,
                    name=function_match.group(1),
                    kind="function",
                    line=line_number,
                )
            )
    return tags


__all__ = [
    "SymbolExtractionDiagnostic",
    "SymbolTag",
    "extract_workspace_symbol_tags",
]
