from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .workspace_inventory import WorkspaceFileEntry, WorkspaceInventory


MentionKind = Literal["path", "filename", "test", "symbol"]
_CODE_FILE_EXTENSIONS = (
    "py|pyi|md|txt|json|toml|yaml|yml|ini|cfg|env|lock|"
    "js|jsx|ts|tsx|css|scss|html|xml|sql|sh|ps1|bat|"
    "go|rs|java|kt|c|h|cpp|hpp|cs|rb|php|lua"
)

# 从文本里识别“像文件路径一样的字符串”
_PATH_MENTION_RE = re.compile(
    r"(?P<token>[`\"']?[A-Za-z0-9_.\-/\\]+[/\\][A-Za-z0-9_.\-/\\]+\.[A-Za-z0-9]+[`\"']?)"
)

_FILENAME_MENTION_RE = re.compile(
    rf"(?<![/\\\w.-])(?P<token>[`\"']?[A-Za-z0-9_.-]+\.({_CODE_FILE_EXTENSIONS})[`\"']?)(?![/\\\w.-])",
    re.IGNORECASE,
)
_SYMBOL_MENTION_RE = re.compile(r"(?<![\w.])(?P<token>[A-Za-z_][A-Za-z0-9_]*)(?![\w.])")
_SYMBOL_STOP_WORDS = frozenset(
    {
        "and",
        "code",
        "file",
        "please",
        "run",
        "test",
        "the",
        "update",
    }
)


# 候选文件 : 保存保存原始文本、候选类型、置信度、可选真实路径和来源
@dataclass(frozen=True)
class MentionCandidate:
    text: str
    kind: MentionKind
    confidence: float
    matched_path: str | None = None
    source: str = "text"

    @property
    def normalized_text(self) -> str:
        return _normalize_mention_text(self.text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "kind": self.kind,
            "confidence": self.confidence,
            "matched_path": self.matched_path,
            "source": self.source,
        }



# 用正则匹配并生成候选文件路径
def detect_file_mentions(text: str) -> list[MentionCandidate]:
    candidates: list[MentionCandidate] = []
    occupied_spans: list[tuple[int, int]] = []
    for match in _PATH_MENTION_RE.finditer(text):
        occupied_spans.append(match.span("token"))
        normalized = _normalize_mention_text(match.group("token"))
        candidates.append(
            MentionCandidate(
                text=normalized,
                kind=_kind_for_path(normalized),
                confidence=1.0,
            )
        )
    for match in _FILENAME_MENTION_RE.finditer(text):
        if _span_overlaps(match.span("token"), occupied_spans):
            continue
        occupied_spans.append(match.span("token"))
        normalized = _normalize_mention_text(match.group("token"))
        kind = _kind_for_filename(normalized)
        candidates.append(
            MentionCandidate(
                text=normalized,
                kind=kind,
                confidence=0.85 if kind == "test" else 0.8,
            )
        )
    for match in _SYMBOL_MENTION_RE.finditer(text):
        if _span_overlaps(match.span("token"), occupied_spans):
            continue
        token = match.group("token")
        if not _is_symbol_token(token):
            continue
        candidates.append(
            MentionCandidate(
                text=token,
                kind="symbol",
                confidence=0.7,
            )
        )
    return _dedupe_candidates(candidates)


# 把用户文本里提到的“文件/符号候选”，拿到真实 workspace 文件清单里验证，并返回结果
def resolve_mentions_against_inventory(
    text: str,
    inventory: WorkspaceInventory,
) -> list[MentionCandidate]:
    path_index, basename_index = _build_inventory_indexes(inventory)
    resolved: list[MentionCandidate] = []
    for candidate in detect_file_mentions(text):
        matches = _inventory_matches(candidate, path_index, basename_index)
        if not matches:
            resolved.append(candidate)
            continue
        confidence = candidate.confidence if len(matches) == 1 else min(candidate.confidence, 0.5)
        resolved.extend(
            replace(
                candidate,
                confidence=confidence,
                matched_path=matched_path,
                source="inventory",
            )
            for matched_path in matches
        )
    return _dedupe_candidates(resolved)


# 接受text去掉引号、反引号、常见句末标点，并把 Windows 路径分隔符转成 /
def _normalize_mention_text(text: str) -> str:
    stripped = text.strip()
    stripped = stripped.strip("\"'`*_")
    stripped = stripped.rstrip(",.!;:?")
    stripped = stripped.strip("\"'`*_")
    return stripped.replace("\\", "/")



# 返回两个索引的该workspace中的所有扫描出的文件。path_index 处理完整相对路径命中，basename_index 处理只说文件名
def _build_inventory_indexes(
    inventory: WorkspaceInventory,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    path_index: dict[str, str] = {}
    basename_index: dict[str, list[str]] = {}
    for entry in inventory.entries:
        if not _is_matchable_inventory_entry(entry):
            continue
        path = _normalize_mention_text(entry.path)
        path_index[path] = entry.path
        basename = path.rsplit("/", 1)[-1]
        basename_index.setdefault(basename, []).append(entry.path)
    return path_index, basename_index


# 判断单个 候选文件名 能匹配到哪些真实 workspace 文件路径
def _inventory_matches(
    candidate: MentionCandidate,
    path_index: dict[str, str],
    basename_index: dict[str, list[str]],
) -> list[str]:
    text = candidate.normalized_text
    if text in path_index:
        return [path_index[text]]
    if "/" in text or candidate.kind not in {"filename", "test"}:
        return []
    return basename_index.get(text, [])


def _is_matchable_inventory_entry(entry: WorkspaceFileEntry) -> bool:
    return entry.kind == "file" and entry.readable and not entry.ignored


# 接收规范化路径，返回 MentionKind
def _kind_for_path(path: str) -> MentionKind:
    basename = path.rsplit("/", 1)[-1]
    if "/tests/" in f"/{path}" or basename.startswith("test_"):
        return "test"
    return "path"


# 回 test 或 filename。它只做保守判断：test_ 开头才算测试文件，不做模糊猜测
def _kind_for_filename(filename: str) -> MentionKind:
    if filename.startswith("test_"):
        return "test"
    return "filename"


# 判断裸文件名是否来自已识别路径内部
def _span_overlaps(span: tuple[int, int], ranges: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < range_end and end > range_start for range_start, range_end in ranges)


def _is_symbol_token(token: str) -> bool:
    if len(token) < 3 or token.lower() in _SYMBOL_STOP_WORDS:
        return False
    if "_" in token:
        return not token.startswith("_") and not token.endswith("_")
    return token[0].isupper() and any(character.islower() for character in token)


def _dedupe_candidates(candidates: list[MentionCandidate]) -> list[MentionCandidate]:
    seen: set[tuple[MentionKind, str, str | None]] = set()
    deduped: list[MentionCandidate] = []
    for candidate in candidates:
        key = (candidate.kind, candidate.normalized_text, candidate.matched_path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


__all__ = [
    "MentionCandidate",
    "MentionKind",
    "detect_file_mentions",
    "resolve_mentions_against_inventory",
]
