from __future__ import annotations

from .contracts import ToolArgument, ToolSpec
from .tools import FunctionTool
from .workspace_code import WorkspaceCodeReader


def create_read_workspace_lines_tool(reader: WorkspaceCodeReader) -> FunctionTool:
    def read_workspace_lines(
        path: str,
        start_line: int,
        end_line: int | None = None,
        max_lines: int = 120,
    ) -> dict[str, object]:
        return reader.read_lines(
            path,
            start_line=start_line,
            end_line=end_line,
            max_lines=max_lines,
        )

    return FunctionTool(
        spec=ToolSpec(
            name="read_workspace_lines",
            description="Read a line range from a UTF-8 text file inside the workspace.",
            arguments=[
                ToolArgument(
                    name="path",
                    description="Workspace-relative file path to read.",
                    schema={"type": "string"},
                ),
                ToolArgument(
                    name="start_line",
                    description="1-based first line to read.",
                    schema={"type": "integer"},
                ),
                ToolArgument(
                    name="end_line",
                    description="1-based last line to read.",
                    schema={"type": "integer"},
                    required=False,
                ),
                ToolArgument(
                    name="max_lines",
                    description="Maximum number of lines to return.",
                    schema={"type": "integer"},
                    required=False,
                ),
            ],
            returns="object",
        ),
        handler=read_workspace_lines,
    )


def create_search_workspace_code_tool(reader: WorkspaceCodeReader) -> FunctionTool:
    def search_workspace_code(
        query: str,
        path: str = ".",
        max_results: int = 50,
        context_lines: int = 2,
        max_scan_files: int = 500,
    ) -> dict[str, object]:
        return reader.search_text(
            query=query,
            path=path,
            max_results=max_results,
            context_lines=context_lines,
            max_scan_files=max_scan_files,
        )

    return FunctionTool(
        spec=ToolSpec(
            name="search_workspace_code",
            description="Search UTF-8 workspace code and return matching lines with context.",
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
                    description="Maximum number of matches to return.",
                    schema={"type": "integer"},
                    required=False,
                ),
                ToolArgument(
                    name="context_lines",
                    description="Number of lines to include before and after each match.",
                    schema={"type": "integer"},
                    required=False,
                ),
                ToolArgument(
                    name="max_scan_files",
                    description="Maximum number of inventory entries to scan.",
                    schema={"type": "integer"},
                    required=False,
                ),
            ],
            returns="object",
        ),
        handler=search_workspace_code,
    )


def create_find_workspace_files_tool(reader: WorkspaceCodeReader) -> FunctionTool:
    def find_workspace_files(
        query: str,
        max_results: int = 20,
        max_scan_files: int = 500,
    ) -> dict[str, object]:
        return reader.find_files(
            query=query,
            max_results=max_results,
            max_scan_files=max_scan_files,
        )

    return FunctionTool(
        spec=ToolSpec(
            name="find_workspace_files",
            description="Find readable workspace files by path, basename, glob, or path segment.",
            arguments=[
                ToolArgument(
                    name="query",
                    description="File path, basename, glob, or path segment to find.",
                    schema={"type": "string"},
                ),
                ToolArgument(
                    name="max_results",
                    description="Maximum number of file candidates to return.",
                    schema={"type": "integer"},
                    required=False,
                ),
                ToolArgument(
                    name="max_scan_files",
                    description="Maximum number of inventory entries to scan.",
                    schema={"type": "integer"},
                    required=False,
                ),
            ],
            returns="object",
        ),
        handler=find_workspace_files,
    )


def create_outline_workspace_file_tool(reader: WorkspaceCodeReader) -> FunctionTool:
    def outline_workspace_file(
        path: str,
        max_symbols: int = 80,
    ) -> dict[str, object]:
        return reader.outline_file(
            path=path,
            max_symbols=max_symbols,
        )

    return FunctionTool(
        spec=ToolSpec(
            name="outline_workspace_file",
            description="Return a lightweight Python outline for a workspace file.",
            arguments=[
                ToolArgument(
                    name="path",
                    description="Workspace-relative Python file path to outline.",
                    schema={"type": "string"},
                ),
                ToolArgument(
                    name="max_symbols",
                    description="Maximum number of outline symbols to return.",
                    schema={"type": "integer"},
                    required=False,
                ),
            ],
            returns="object",
        ),
        handler=outline_workspace_file,
    )


def create_find_related_workspace_files_tool(reader: WorkspaceCodeReader) -> FunctionTool:
    def find_related_workspace_files(
        path: str,
        max_results: int = 20,
        max_scan_files: int = 500,
    ) -> dict[str, object]:
        return reader.find_related_files(
            path=path,
            max_results=max_results,
            max_scan_files=max_scan_files,
        )

    return FunctionTool(
        spec=ToolSpec(
            name="find_related_workspace_files",
            description="Find related source or test files using simple workspace naming rules.",
            arguments=[
                ToolArgument(
                    name="path",
                    description="Workspace-relative source or test file path.",
                    schema={"type": "string"},
                ),
                ToolArgument(
                    name="max_results",
                    description="Maximum number of related file candidates to return.",
                    schema={"type": "integer"},
                    required=False,
                ),
                ToolArgument(
                    name="max_scan_files",
                    description="Maximum number of inventory entries to scan.",
                    schema={"type": "integer"},
                    required=False,
                ),
            ],
            returns="object",
        ),
        handler=find_related_workspace_files,
    )


def create_workspace_code_tools(reader: WorkspaceCodeReader) -> list[FunctionTool]:
    return [
        create_read_workspace_lines_tool(reader),
        create_search_workspace_code_tool(reader),
        create_find_workspace_files_tool(reader),
        create_outline_workspace_file_tool(reader),
        create_find_related_workspace_files_tool(reader),
    ]


__all__ = [
    "create_find_related_workspace_files_tool",
    "create_find_workspace_files_tool",
    "create_outline_workspace_file_tool",
    "create_read_workspace_lines_tool",
    "create_search_workspace_code_tool",
    "create_workspace_code_tools",
]
