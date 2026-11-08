from __future__ import annotations

from .contracts import ToolArgument, ToolSpec
from .tools import FunctionTool
from .workspace import Workspace, WorkspacePathError
from .workspace_code import WorkspaceCodeReader
from .workspace_code_tools import create_workspace_code_tools
from .workspace_inventory import build_workspace_inventory


# 工具会先用 Workspace.ensure_readable_path() 校验目录安全，再返回该目录下可读的相对路径列表
def create_list_workspace_files_tool(workspace: Workspace) -> FunctionTool:
    def list_workspace_files(path: str = ".", max_entries: int = 50) -> dict[str, object]:
        limit = max(0, int(max_entries))
        # 列目录内容 的，所以传进来的路径必须是目录；inventory 入口负责安全校验
        inventory = build_workspace_inventory(
            workspace,
            path=path,
            max_entries=limit,
            max_depth=1,
        )
        entries = [entry.path for entry in inventory.entries if entry.readable]
        truncated = inventory.truncated and len(entries) >= limit

        return {
            "path": inventory.base_path,
            "entries": entries,
            "truncated": truncated,
            "inventory": inventory.to_dict(),
        }

    return FunctionTool(
        spec=ToolSpec(
            name="list_workspace_files",
            description="List readable files and directories inside the workspace.",
            arguments=[
                ToolArgument(
                    name="path",
                    description="Workspace-relative directory path to list.",
                    schema={"type": "string"},
                    required=False,
                ),
                ToolArgument(
                    name="max_entries",
                    description="Maximum number of entries to return.",
                    schema={"type": "integer"},
                    required=False,
                ),
            ],
            returns="object",
        ),
        handler=list_workspace_files,
    )


# 先校验路径，再拒绝目录，随后读取 UTF-8 文本；如果内容过长会裁剪，如果无法解码会返回清晰错误
def create_read_workspace_file_tool(workspace: Workspace) -> FunctionTool:
    def read_workspace_file(path: str, max_chars: int = 8000) -> dict[str, object]:
        file_path = workspace.ensure_readable_path(path)
        if not file_path.is_file():
            raise WorkspacePathError(path, workspace.root, "not a file")

        relative_path = workspace.relative_path(file_path).as_posix()
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {
                "path": relative_path,
                "content": "",
                "truncated": False,
                "error": "File is not UTF-8 text.",
            }

        limit = max(0, int(max_chars))
        truncated = len(content) > limit
        if truncated:
            content = content[:limit]

        return {
            "path": relative_path,
            "content": content,
            "truncated": truncated,
        }

    return FunctionTool(
        spec=ToolSpec(
            name="read_workspace_file",
            description="Read a UTF-8 text file inside the workspace.",
            arguments=[
                ToolArgument(
                    name="path",
                    description="Workspace-relative file path to read.",
                    schema={"type": "string"},
                ),
                ToolArgument(
                    name="max_chars",
                    description="Maximum number of characters to return.",
                    schema={"type": "integer"},
                    required=False,
                ),
            ],
            returns="object",
        ),
        handler=read_workspace_file,
    )


def create_search_workspace_text_tool(workspace: Workspace) -> FunctionTool:
    def search_workspace_text(
        query: str,
        path: str = ".",
        max_results: int = 50,
    ) -> dict[str, object]:
        search_root = workspace.ensure_readable_path(path)
        search_path = workspace.relative_path(search_root).as_posix()
        limit = max(0, int(max_results))
        results: list[dict[str, object]] = []
        truncated = False

        files = [] if not query else [search_root] if search_root.is_file() else sorted(search_root.rglob("*"))
        for file_path in files:
            try:
                readable_path = workspace.ensure_readable_path(file_path)
            except WorkspacePathError:
                continue
            if not readable_path.is_file():
                continue

            try:
                lines = readable_path.read_text(encoding="utf-8").splitlines()
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
                results.append(
                    {
                        "path": workspace.relative_path(readable_path).as_posix(),
                        "line": line_number,
                        "text": line,
                    }
                )

        return {
            "query": query,
            "path": search_path,
            "results": results,
            "truncated": truncated,
        }

    return FunctionTool(
        spec=ToolSpec(
            name="search_workspace_text",
            description="Search UTF-8 text files inside the workspace.",
            arguments=[
                ToolArgument(
                    name="query",
                    description="Text to search for.",
                    schema={"type": "string"},
                ),
                ToolArgument(
                    name="path",
                    description="Workspace-relative file or directory to search.",
                    schema={"type": "string"},
                    required=False,
                ),
                ToolArgument(
                    name="max_results",
                    description="Maximum number of line matches to return.",
                    schema={"type": "integer"},
                    required=False,
                ),
            ],
            returns="object",
        ),
        handler=search_workspace_text,
    )


def create_readonly_workspace_tools(workspace: Workspace) -> list[FunctionTool]:
    code_reader = WorkspaceCodeReader(workspace)
    return [
        create_list_workspace_files_tool(workspace),
        create_read_workspace_file_tool(workspace),
        create_search_workspace_text_tool(workspace),
        *create_workspace_code_tools(code_reader),
    ]
