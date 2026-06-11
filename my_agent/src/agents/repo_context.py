from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context_mentions import MentionCandidate
    from .run_context import RunContextWrapper
    from .selected_files import SelectedFilesState
    from .workspace_code import WorkspaceCodeReader
    from .workspace_inventory import WorkspaceInventory


# 表示 repo context 中的一块内容 后续 selected files、mentions、workspace code 结果都会变成 section
@dataclass(frozen=True)
class RepoContextSection:
    title: str
    content: str
    source: str
    priority: int

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("Repo context section title cannot be empty.")
        if not self.source.strip():
            raise ValueError("Repo context section source cannot be empty.")

    def has_content(self) -> bool:
        return bool(self.content.strip())

    def to_text(self) -> str:
        return f"## {self.title.strip()}\n{self.content.strip()}"


# 保存一轮仓库上下文整体结果，包括 sections、已选文件路径、提到的符号和截断标记
@dataclass(frozen=True)
class RepoContext:
    sections: tuple[RepoContextSection, ...] = field(default_factory=tuple)
    selected_paths: tuple[str, ...] = field(default_factory=tuple)
    mentioned_symbols: tuple[str, ...] = field(default_factory=tuple)
    truncated: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "sections", _unique_sections(self.sections))
        object.__setattr__(self, "selected_paths", tuple(self.selected_paths))
        object.__setattr__(self, "mentioned_symbols", tuple(self.mentioned_symbols))

    #  按 priority 排序 section
    def ordered_sections(self) -> tuple[RepoContextSection, ...]:
        indexed_sections = list(enumerate(self.sections))
        ordered = sorted(indexed_sections, key=lambda item: (item[1].priority, item[0]))
        return tuple(section for _, section in ordered if section.has_content())

    def to_text(self) -> str:
        header_lines = ["Repo context:"]
        if self.selected_paths:
            header_lines.append(f"Selected paths: {_join_values(self.selected_paths)}")
        if self.mentioned_symbols:
            header_lines.append(
                f"Mentioned symbols: {_join_values(self.mentioned_symbols)}"
            )

        section_texts = [section.to_text() for section in self.ordered_sections()]
        if not section_texts:
            return "\n".join(header_lines)
        return "\n".join(header_lines) + "\n\n" + "\n\n".join(section_texts)


# repo context 的组装入口
@dataclass(frozen=True)
class RepoContextBuilder:
    inventory: WorkspaceInventory | None = None
    selected_files: SelectedFilesState | None = None
    mentions: Iterable[MentionCandidate] = ()
    workspace_code_reader: WorkspaceCodeReader | None = None
    max_chars: int | None = None
    max_inventory_entries: int = 20

    def __post_init__(self) -> None:
        object.__setattr__(self, "mentions", tuple(self.mentions))

    def build(self) -> RepoContext:
        sections: list[RepoContextSection] = []
        selected_paths: tuple[str, ...] = ()

        inventory_section = self._inventory_summary_section()
        if inventory_section is not None:
            sections.append(inventory_section)

        selected_files_section = self._selected_files_section()
        if selected_files_section is not None:
            sections.append(selected_files_section)
            selected_paths = tuple(
                selected_file.path for selected_file in self.selected_files.files()
            )

        mentioned_paths_section = self._mentioned_paths_section()
        if mentioned_paths_section is not None:
            sections.append(mentioned_paths_section)

        mentioned_symbols = self._mentioned_symbol_values()
        mentioned_symbols_section = self._mentioned_symbols_section()
        if mentioned_symbols_section is not None:
            sections.append(mentioned_symbols_section)

        workspace_code_section = self._workspace_code_matches_section(mentioned_symbols)
        if workspace_code_section is not None:
            sections.append(workspace_code_section)

        context = RepoContext(
            sections=tuple(sections),
            selected_paths=selected_paths,
            mentioned_symbols=mentioned_symbols,
        )
        return _limit_context(context, self.max_chars)


    # 把仓库清单渲染成 Workspace inventory section ：模型不能直接理解 Python 对象 WorkspaceInventory，所以要把 inventory 里的文件树信息整理成文本 section。
    def _inventory_summary_section(self) -> RepoContextSection | None:
        if self.inventory is None:
            return None

        limit = max(0, int(self.max_inventory_entries))
        entries = self.inventory.entries[:limit]
        lines = [
            f"Base path: {self.inventory.base_path}",
            f"Truncated: {'yes' if self.inventory.truncated else 'no'}",
            f"Entries shown: {len(entries)} of {len(self.inventory.entries)}",
        ]
        for entry in entries:
            line = (
                f"- {entry.path} [{entry.kind}] "
                f"readable={entry.readable} ignored={entry.ignored} "
                f"reason={entry.reason}"
            )
            if entry.size_bytes is not None:
                line = (
                    f"- {entry.path} [{entry.kind}] size={entry.size_bytes} "
                    f"readable={entry.readable} ignored={entry.ignored} "
                    f"reason={entry.reason}"
                )
            lines.append(line)

        return RepoContextSection(
            title="Workspace inventory",
            content="\n".join(lines),
            source="workspace_inventory",
            priority=5,
        )

    # 把各个 其他类 渲染成 RepoContextSection
    def _selected_files_section(self) -> RepoContextSection | None:
        if self.selected_files is None:
            return None
        selected_files = self.selected_files.files()
        if not selected_files:
            return None
        return RepoContextSection(
            title="Selected files",
            content="\n".join(
                selected_file.summary_line() for selected_file in selected_files
            ),
            source="selected_files",
            priority=10,
        )

    
    def _mentioned_paths_section(self) -> RepoContextSection | None:
        lines: list[str] = []
        seen_paths: set[str] = set()
        for mention in self.mentions:
            if mention.kind not in {"filename", "path", "test"}:
                continue
            path = (mention.matched_path or mention.normalized_text).strip()
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            lines.append(f"- {path} [{mention.kind}] source={mention.source}")
        if not lines:
            return None
        return RepoContextSection(
            title="Mentioned paths",
            content="\n".join(lines),
            source="context_mentions",
            priority=20,
        )

    # 给程序内部用，拿 symbol 去搜索 workspace 代码
    def _mentioned_symbol_values(self) -> tuple[str, ...]:
        return _unique_values(
            mention.normalized_text
            for mention in self.mentions
            if mention.kind == "symbol"
        )

    # 把用户提到的 symbol 整理成一段 RepoContextSection，放进最终 repo context 里
    def _mentioned_symbols_section(self) -> RepoContextSection | None:
        lines: list[str] = []
        seen_symbols: set[str] = set()
        for mention in self.mentions:
            if mention.kind != "symbol":
                continue
            symbol = mention.normalized_text
            if not symbol or symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            lines.append(
                f"- {symbol} confidence={mention.confidence:.2f} source={mention.source}"
            )
        if not lines:
            return None
        return RepoContextSection(
            title="Mentioned symbols",
            content="\n".join(lines),
            source="context_mentions",
            priority=30,
        )

    # 把提到的symbal 在 workspace 中查找匹配并返回结果
    def _workspace_code_matches_section(
        self,
        mentioned_symbols: tuple[str, ...],
    ) -> RepoContextSection | None:
        if self.workspace_code_reader is None or not mentioned_symbols:
            return None
        lines: list[str] = []
        for symbol in mentioned_symbols:
            search_result = self.workspace_code_reader.search_text(
                symbol,
                max_results=3,
                context_lines=0,
            )
            for match in _workspace_code_matches(search_result):
                lines.append(
                    f"- {symbol}: {match['path']}:{match['line']} {match['text']}"
                )
        if not lines:
            return None
        return RepoContextSection(
            title="Workspace code matches",
            content="\n".join(lines),
            source="workspace_code",
            priority=40,
        )


def _join_values(values: Iterable[str]) -> str:
    return ", ".join(value.strip() for value in values if value.strip())


def _unique_values(values: Iterable[str]) -> tuple[str, ...]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = value.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return tuple(unique)


def _unique_sections(
    sections: Iterable[RepoContextSection],
) -> tuple[RepoContextSection, ...]:
    unique: list[RepoContextSection] = []
    seen: set[tuple[str, str, str]] = set()
    for section in sections:
        key = (
            section.title.strip(),
            section.source.strip(),
            section.content.strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(section)
    return tuple(unique)


def _limit_context(context: RepoContext, max_chars: int | None) -> RepoContext:
    if max_chars is None:
        return context

    max_chars = max(0, max_chars)
    if len(context.to_text()) <= max_chars:
        return context

    kept_sections: list[RepoContextSection] = []
    for section in context.ordered_sections():
        candidate = RepoContext(
            sections=tuple(kept_sections + [section]),
            selected_paths=context.selected_paths,
            mentioned_symbols=context.mentioned_symbols,
            truncated=True,
        )
        if len(candidate.to_text()) <= max_chars:
            kept_sections.append(section)

    return RepoContext(
        sections=tuple(kept_sections),
        selected_paths=context.selected_paths,
        mentioned_symbols=context.mentioned_symbols,
        truncated=True,
    )


def _workspace_code_matches(search_result: dict[str, object]) -> tuple[dict[str, object], ...]:
    results = search_result.get("results")
    if not isinstance(results, list):
        return ()
    matches: list[dict[str, object]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        if not all(key in result for key in ("path", "line", "text")):
            continue
        matches.append(result)
    return tuple(matches)


# 把“用户任务文本”自动转换成 RepoContext，并写入 RunContextWrapper.context
def build_task_repo_context(
    task: str,
    context_wrapper: RunContextWrapper,
    *,
    max_inventory_entries: int = 500,
    max_inventory_depth: int | None = None,
    max_context_chars: int | None = 4000,
    max_inventory_section_entries: int = 30,
) -> RepoContext | None:
    if not task.strip():
        return None

    workspace = context_wrapper.workspace
    if workspace is None:
        return None

    from .context_mentions import resolve_mentions_against_inventory
    from .run_context import CONTEXT_REPO_CONTEXT_KEY
    from .workspace import WorkspacePathError
    from .workspace_code import WorkspaceCodeReader
    from .workspace_inventory import build_workspace_inventory

    try:
        inventory = build_workspace_inventory(
            workspace,
            max_entries=max_inventory_entries,
            max_depth=max_inventory_depth,
        )
    except (OSError, WorkspacePathError):
        return None

    mentions = resolve_mentions_against_inventory(task, inventory)
    selected_files = context_wrapper.selected_files
    if selected_files is not None:
        selected_files.add_mentions(mentions)

    repo_context = RepoContextBuilder(
        inventory=inventory,
        selected_files=selected_files,
        mentions=mentions,
        workspace_code_reader=WorkspaceCodeReader(workspace),
        max_chars=max_context_chars,
        max_inventory_entries=max_inventory_section_entries,
    ).build()

    if (
        not repo_context.sections
        and not repo_context.selected_paths
        and not repo_context.mentioned_symbols
    ):
        return None

    if context_wrapper.context is None:
        context_wrapper.context = {}
    if isinstance(context_wrapper.context, dict):
        context_wrapper.context[CONTEXT_REPO_CONTEXT_KEY] = repo_context

    return repo_context


__all__ = [
    "RepoContext",
    "RepoContextBuilder",
    "RepoContextSection",
    "build_task_repo_context",
]
