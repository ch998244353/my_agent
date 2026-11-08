from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from .context_mentions import MentionCandidate
from .repo_context import RepoContextSection
from .repo_symbols import SymbolTag
from .workspace_inventory import WorkspaceInventory

_MAX_SYMBOL_SNIPPETS = 3
_TRUNCATED_MARKER = "... repo map truncated"


# 保存一次搜索命中的文件、行号、查询词和行文本
@dataclass(frozen=True)
class RepoMapSearchHit:
    path: str
    line: int
    query: str
    text: str


# 表示一个候选文件
@dataclass(frozen=True)
class RepoMapCandidate:
    path: str
    score: int
    reasons: tuple[str, ...]
    symbols: tuple[SymbolTag, ...] = ()


@dataclass(frozen=True)
class RepoMapRenderResult:
    section: RepoContextSection | None
    included_paths: tuple[str, ...] = ()
    truncated: bool = False


# 函数内部临时桶：同一个 path 多次命中时先累加分数、收集 reason 和 symbol，最后再转成不可变 RepoMapCandidate
@dataclass
class _CandidateBucket:
    path: str
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    symbols: list[SymbolTag] = field(default_factory=list)


# 把 symbol tags、用户 mention、workspace inventory 合成候选列表
def rank_repo_map_candidates(
    tags: Iterable[SymbolTag],
    selected_paths: Iterable[str],
    mentions: Iterable[MentionCandidate],
    inventory: WorkspaceInventory,
    search_hits: Iterable[RepoMapSearchHit] = (),
) -> tuple[RepoMapCandidate, ...]:
    buckets: dict[str, _CandidateBucket] = {}
    selected_set = {_normalize_path(path) for path in selected_paths}
    inventory_paths = tuple(sorted({
        _normalize_path(entry.path)
        for entry in inventory.entries
        if entry.kind == "file" and entry.readable and not entry.ignored
    }))
    basename_index: dict[str, list[str]] = {}
    for path in inventory_paths:
        basename_index.setdefault(path.rsplit("/", 1)[-1], []).append(path)

    tags_by_name: dict[str, list[SymbolTag]] = {}
    for tag in tags:
        if tag.role == "definition":
            tags_by_name.setdefault(tag.name, []).append(tag)

    for mention in mentions: # 判断处理 symbol mention：用户说 Service，并且 tags 里有 Service 定义，就给定义所在文件加 100 分，并记录 symbol definition: Service
        text = mention.normalized_text
        if mention.kind == "symbol":
            for tag in tags_by_name.get(text, ()):
                _add_bucket(
                    buckets,
                    tag.path,
                    100,
                    f"symbol definition: {text}",
                    symbol=tag,
                )
            continue

        # 已经解析到真实文件的 mention：完整路径命中加 80 分，只提文件名命中加 60 分
        if mention.kind not in {"path", "filename", "test"}:
            continue
        matched_path = _normalize_path(mention.matched_path or "")
        if matched_path:
            exact = text == matched_path or "/" in text
            reason = f"path mention: {matched_path}" if exact else f"basename mention: {text}"
            _add_bucket(buckets, matched_path, 80 if exact else 60, reason)
            continue

        normalized_text = _normalize_path(text)
        if normalized_text in inventory_paths:
            _add_bucket(buckets, normalized_text, 20, f"inventory path: {normalized_text}")
            continue
        matches = basename_index.get(normalized_text, [])
        if len(matches) > 1:
            for path in matches:
                _add_bucket(buckets, path, 60, f"ambiguous basename: {normalized_text}")
            continue
        if len(matches) == 1:
            _add_bucket(buckets, matches[0], 20, f"inventory path: {normalized_text}")

    for hit in search_hits:
        _add_bucket(buckets, hit.path, 40, f"search hit: {hit.query}")

    candidates = [
        RepoMapCandidate(
            path=bucket.path,
            score=bucket.score,
            reasons=tuple(bucket.reasons),
            symbols=tuple(bucket.symbols),
        )
        for bucket in buckets.values()
        if bucket.path not in selected_set
    ]
    return tuple(sorted(candidates, key=lambda candidate: (-candidate.score, candidate.path)))

# 把 candidates 转成 RepoContextSection
def render_repo_map_section(
    candidates: Iterable[RepoMapCandidate],
    reader: object,
    selected_paths: Iterable[str] = (),
    max_chars: int | None = 4000,
) -> RepoMapRenderResult:
    budget = None if max_chars is None else max(0, int(max_chars))
    if budget == 0:
        return RepoMapRenderResult(section=None)

    selected_set = {_normalize_path(path) for path in selected_paths}
    included_paths: list[str] = []
    blocks: list[str] = []
    truncated = False
    for candidate in candidates:
        path = _normalize_path(candidate.path)
        if path in selected_set:
            continue
        block = _candidate_block(candidate, reader)
        next_content = "\n".join(blocks + [block])
        if budget is not None and len(next_content) > budget:
            truncated = True
            break
        included_paths.append(path)
        blocks.append(block)

    if truncated and budget is not None:
        blocks, kept_count = _append_truncated_marker(blocks, budget)
        included_paths = included_paths[:kept_count]

    if not blocks:
        return RepoMapRenderResult(section=None)

    included = tuple(included_paths)
    section = RepoContextSection(
        title="Repo map",
        content="\n".join(blocks),
        source="repo_map",
        priority=15,
        metadata={"included_paths": included, "truncated": truncated},
    )
    return RepoMapRenderResult(section=section, included_paths=included, truncated=truncated)


def _add_bucket(
    buckets: dict[str, _CandidateBucket],
    path: str,
    score: int,
    reason: str,
    *,
    symbol: SymbolTag | None = None,
) -> None:
    normalized_path = _normalize_path(path)
    bucket = buckets.setdefault(normalized_path, _CandidateBucket(normalized_path))
    if reason not in bucket.reasons:
        bucket.score += score
        bucket.reasons.append(reason)
    if symbol is not None and symbol not in bucket.symbols:
        bucket.symbols.append(symbol)


def _normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/")


def _candidate_header(candidate: RepoMapCandidate) -> str:
    lines = [f"{candidate.path} score={candidate.score}"]
    if candidate.reasons:
        lines.append(f"reasons: {', '.join(candidate.reasons)}")
    return "\n".join(lines)


def _append_truncated_marker(blocks: list[str], budget: int) -> tuple[list[str], int]:
    if len(_TRUNCATED_MARKER) > budget:
        return blocks, len(blocks)
    while blocks and len("\n".join(blocks + [_TRUNCATED_MARKER])) > budget:
        blocks.pop()
    return blocks + [_TRUNCATED_MARKER], len(blocks)


# 生成单个候选文件的完整文本块
def _candidate_block(candidate: RepoMapCandidate, reader: object) -> str:
    lines = _candidate_header(candidate).splitlines()
    for symbol in candidate.symbols[:_MAX_SYMBOL_SNIPPETS]:
        lines.extend(_symbol_snippet_lines(reader, candidate.path, symbol))
    return "\n".join(lines)


def _symbol_snippet_lines(reader: object, path: str, symbol: SymbolTag) -> list[str]:
    try:
        result = reader.read_lines(
            path,
            start_line=symbol.line,
            end_line=symbol.line,
            max_lines=1,
        )
    except Exception as exc:
        return [f"  read failed: {exc}"]

    error = result.get("error")
    if error:
        return [f"  read failed: {error}"]

    lines = result.get("lines")
    if not isinstance(lines, list):
        return []
    snippets: list[str] = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        line_number = line.get("line", symbol.line)
        text = line.get("text", "")
        snippets.append(f"  L{line_number} {text}")
    return snippets


__all__ = [
    "RepoMapCandidate",
    "RepoMapRenderResult",
    "RepoMapSearchHit",
    "rank_repo_map_candidates",
    "render_repo_map_section",
]
