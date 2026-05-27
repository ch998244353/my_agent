from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from .contracts import ChatMessage, MessageRole, StepRecord, ToolCall
from .python_executor import PYTHON_EXECUTOR_TOOL_NAME


def _render_tool_call(tool_call: ToolCall) -> str:
    arguments = ", ".join(
        f"{name}={value!r}" for name, value in tool_call.arguments.items()
    )
    return f"{tool_call.call_id}: {tool_call.tool_name}({arguments})"


def _render_tool_calls(tool_calls: list[ToolCall]) -> str:
    rendered_calls = "\n".join(
        f"- {_render_tool_call(tool_call)}" for tool_call in tool_calls
    )
    return f"Calling tools:\n{rendered_calls}"


def _render_error(step: StepRecord) -> str:
    message = ""
    if step.tool_calls:
        message += f"Call id: {step.tool_calls[0].call_id}\n"
    message += (
        f"Error:\n{step.error}\n"
        "Now let's retry: take care not to repeat previous errors!"
    )
    return message


# 根据 max_steps 选择最近的若干个 step
def _select_recent_steps(
    steps: list[StepRecord], max_steps: int | None
) -> list[StepRecord]:
    if max_steps is None:
        return steps
    return steps[-max_steps:] if max_steps > 0 else []


_MESSAGE_ROLES: set[MessageRole] = {
    "system",
    "user",
    "assistant",
    "tool_call",
    "tool_response",
}


def _message_role(value: Any, default: MessageRole = "user") -> MessageRole:
    if isinstance(value, str) and value in _MESSAGE_ROLES:
        return cast(MessageRole, value)
    return default


def session_item_to_message(item: Any) -> ChatMessage:
    if isinstance(item, ChatMessage):
        return item
    if isinstance(item, dict):
        return ChatMessage(
            role=_message_role(item.get("role")),
            content=str(item.get("content", item)),
        )
    role = getattr(item, "role", "user")
    content = getattr(item, "content", item)
    return ChatMessage(role=_message_role(role), content=str(content))


def session_items_from_result(result: Any) -> list[ChatMessage]:
    to_input_list = getattr(result, "to_input_list", None)
    if not callable(to_input_list):
        return []
    items = to_input_list()
    if not isinstance(items, list):
        return []
    return [session_item_to_message(item) for item in items]


def _message_to_dict(message: ChatMessage) -> dict[str, str]:
    return {"role": message.role, "content": message.content}


def _message_from_dict(data: dict[str, Any]) -> ChatMessage:
    return ChatMessage(
        role=_message_role(data.get("role")),
        content=str(data.get("content", "")),
    )


def _tool_call_to_dict(tool_call: ToolCall) -> dict[str, Any]:
    return {
        "tool_name": tool_call.tool_name,
        "arguments": dict(tool_call.arguments),
        "call_id": tool_call.call_id,
    }


def _tool_call_from_dict(data: dict[str, Any]) -> ToolCall:
    arguments = data.get("arguments", {})
    if not isinstance(arguments, dict):
        arguments = {}
    return ToolCall(
        tool_name=str(data.get("tool_name", "")),
        arguments=dict(arguments),
        call_id=str(data.get("call_id", "")),
    )


def _step_to_dict(step: StepRecord) -> dict[str, Any]:
    return {
        "step_number": step.step_number,
        "messages": [_message_to_dict(message) for message in step.messages],
        "tool_calls": [_tool_call_to_dict(tool_call) for tool_call in step.tool_calls],
        "observation": step.observation,
        "is_final_answer": step.is_final_answer,
        "error": step.error,
    }


def _step_from_dict(data: dict[str, Any]) -> StepRecord:
    return StepRecord(
        step_number=int(data.get("step_number", 0)),
        messages=[_message_from_dict(message) for message in data.get("messages", [])],
        tool_calls=[_tool_call_from_dict(tool_call) for tool_call in data.get("tool_calls", [])],
        observation=data.get("observation"),
        is_final_answer=bool(data.get("is_final_answer", False)),
        error=data.get("error"),
    )


# 把一个 StepRecord 转换成若干条 ChatMessage，并追加到 messages
def _append_step_messages(messages: list[ChatMessage], step: StepRecord) -> None:
    messages.extend(step.messages)
    if step.tool_calls:
        messages.append(
            ChatMessage(
                role="tool_call",
                content=_render_tool_calls(step.tool_calls),
            )
        )
    if step.observation is not None:
        messages.append(
            ChatMessage(
                role="tool_response",
                content=f"Observation:\n{step.observation}",
            )
        )
    if step.error is not None:
        messages.append(ChatMessage(role="tool_response", content=_render_error(step)))


@dataclass
class AgentMemory:
    task: str | None = None
    steps: list[StepRecord] = field(default_factory=list)

    def add_task(self, task: str) -> None:
        self.task = task

    def add_step(self, step: StepRecord) -> None:
        self.steps.append(step)

    def reset_steps(self) -> None:
        self.steps.clear()

    def to_messages(self, max_steps: int | None = None) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        if self.task is not None:
            messages.append(ChatMessage(role="user", content=self.task))

        for step in _select_recent_steps(self.steps, max_steps):
            _append_step_messages(messages, step)

        return messages


    # 把模型多次生成并执行过的 Python 代码片段合并起来
    def return_full_code(self) -> str:
        python_snippets: list[str] = []
        for step in self.steps:
            for tool_call in step.tool_calls:
                if tool_call.tool_name != PYTHON_EXECUTOR_TOOL_NAME:
                    continue
                code = tool_call.arguments.get("code")
                if isinstance(code, str):
                    python_snippets.append(code)
        return "\n\n".join(python_snippets)


@dataclass
class SessionTurn:
    task: str | None
    steps: list[StepRecord] = field(default_factory=list)


@dataclass(frozen=True)
class MemorySummary:
    content: str
    source_turn_count: int = 0

    def to_message(self) -> ChatMessage:
        return ChatMessage(
            role="system",
            content=f"Conversation summary:\n{self.content}",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "source_turn_count": self.source_turn_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemorySummary:
        return cls(
            content=str(data.get("content", "")),
            source_turn_count=int(data.get("source_turn_count", 0)),
        )


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


# 压缩策略 : 超过几轮开始压缩、保留最近几轮、summary 最大长度
@dataclass(frozen=True)
class CompactionPolicy:
    compact_after_turns: int | None = None
    keep_recent_turns: int = 2
    max_summary_chars: int | None = None
    #  assistant/user 文本、工具参数、observation、error 的最大保留长度。
    compact_message_chars: int = 200
    compact_argument_chars: int = 120
    compact_observation_chars: int = 240
    compact_error_chars: int = 200

    def to_dict(self) -> dict[str, Any]:
        return {
            "compact_after_turns": self.compact_after_turns,
            "keep_recent_turns": self.keep_recent_turns,
            "max_summary_chars": self.max_summary_chars,
            "compact_message_chars": self.compact_message_chars,
            "compact_argument_chars": self.compact_argument_chars,
            "compact_observation_chars": self.compact_observation_chars,
            "compact_error_chars": self.compact_error_chars,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompactionPolicy:
        return cls(
            compact_after_turns=_optional_int(data.get("compact_after_turns")),
            keep_recent_turns=int(data.get("keep_recent_turns", 2)),
            max_summary_chars=_optional_int(data.get("max_summary_chars")),
            compact_message_chars=int(data.get("compact_message_chars", 200)),
            compact_argument_chars=int(data.get("compact_argument_chars", 120)),
            compact_observation_chars=int(data.get("compact_observation_chars", 240)),
            compact_error_chars=int(data.get("compact_error_chars", 200)),
        )


# 压缩结果
@dataclass(frozen=True)
class CompactionResult:
    summary: MemorySummary
    turns: list[SessionTurn]


class MemorySummarizer(Protocol):
    def summarize(
        self,
        compact_input: str,
        *,
        previous_summary: MemorySummary | None = None,
    ) -> MemorySummary:
        ...


# 把一个旧 turn 变成可读摘要文本
def _render_turn_summary(turn: SessionTurn, turn_number: int) -> str:
    messages: list[ChatMessage] = []
    if turn.task is not None:
        messages.append(ChatMessage(role="user", content=turn.task))
    for step in turn.steps:
        _append_step_messages(messages, step)
    lines = [f"Turn {turn_number}:"]
    lines.extend(f"{message.role}: {message.content}" for message in messages)
    return "\n".join(lines)


# 剪切上下文
def _clip_compact_text(value: Any, max_chars: int) -> str:
    text = str(value).replace("\r\n", "\n").strip()
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}..."

# 从 response 提取 output_text,没有直接字段就从 output.content.text 拼接
def _response_field(response: Any, field_name: str) -> Any:
    if isinstance(response, dict):
        return response.get(field_name)
    return getattr(response, field_name, None)


def _response_text(response: Any) -> str | None:
    output_text = _response_field(response, "output_text")
    if isinstance(output_text, str):
        return output_text

    text_parts: list[str] = []
    for output_item in _response_field(response, "output") or []:
        for content_item in _response_field(output_item, "content") or []:
            text = _response_field(content_item, "text")
            if isinstance(text, str):
                text_parts.append(text)
    return "\n".join(text_parts) if text_parts else None


#把 ToolCall 渲染:tool: search(query='OpenAI Agent SDK', top_k='5')
def _render_compact_tool_call(tool_call: ToolCall, max_arg_chars: int) -> str:
    arguments = ", ".join(
        f"{name}={_clip_compact_text(value, max_arg_chars)!r}"
        for name, value in tool_call.arguments.items()
    )
    return f"tool: {tool_call.tool_name}({arguments})"


# 把一个 SessionTurn 渲染成多行紧凑文本
# 只保留 1.user: 用户问题, 2.assistant : 模型回答, 3.tool: 某个工具调用(...), 4.observation: 工具返回结果, 5.error: 工具错误
def _render_compact_turn(
    turn: SessionTurn,
    turn_number: int,
    policy: CompactionPolicy,
) -> str:
    lines = [f"Turn {turn_number}:"]
    if turn.task is not None:
        user_text = _clip_compact_text(turn.task, policy.compact_message_chars)
        lines.append(f"user: {user_text}")
    for step in turn.steps:
        for message in step.messages:
            if message.role == "assistant":
                assistant_text = _clip_compact_text(
                    message.content,
                    policy.compact_message_chars,
                )
                lines.append(f"assistant: {assistant_text}")
        for tool_call in step.tool_calls:
            lines.append(
                _render_compact_tool_call(
                    tool_call,
                    policy.compact_argument_chars,
                )
            )
        if step.observation is not None:
            observation_text = _clip_compact_text(
                step.observation,
                policy.compact_observation_chars,
            )
            lines.append(f"observation: {observation_text}")
        if step.error is not None:
            error_text = _clip_compact_text(step.error, policy.compact_error_chars)
            lines.append(f"error: {error_text}")
    return "\n".join(lines)

# 把 compact input 整理成五段
class RuleBasedSummarizer:
    headings = [
        "User goals",
        "Constraints",
        "Decisions",
        "Important facts",
        "Open tasks",
    ]

    def summarize(
        self,
        compact_input: str,
        *,
        previous_summary: MemorySummary | None = None,
    ) -> MemorySummary:
        sections = {heading: [] for heading in self.headings}
        if previous_summary is not None and previous_summary.content:
            sections["Important facts"].append(
                _clip_compact_text(previous_summary.content, 160)
            )
        for raw_line in compact_input.splitlines():
            line = raw_line.strip()
            if line.startswith("user: "):
                sections["User goals"].append(line.removeprefix("user: "))
            elif line.startswith("assistant: "):
                sections["Decisions"].append(line.removeprefix("assistant: "))
            elif line.startswith("observation: "):
                sections["Important facts"].append(line.removeprefix("observation: "))
            elif line.startswith("error: "):
                sections["Open tasks"].append(line.removeprefix("error: "))

        content = "\n".join(
            f"{heading}:\n{self._render_items(sections[heading])}"
            for heading in self.headings
        )
        return MemorySummary(content=content)

    def _render_items(self, items: list[str]) -> str:
        if not items:
            return "- None."
        return "\n".join(
            f"- {_clip_compact_text(item, 120)}"
            for item in items[:3]
        )


class ModelSummarizer:
    def __init__(
        self,
        model: str = "gpt-5.4",
        *,
        client: Any | None = None,
        fallback: MemorySummarizer | None = None,
        max_output_tokens: int = 500,
    ) -> None:
        self.model = model
        self.client = client
        self.fallback = fallback or RuleBasedSummarizer()
        self.max_output_tokens = max_output_tokens

    def summarize(
        self,
        compact_input: str,
        *,
        previous_summary: MemorySummary | None = None,
    ) -> MemorySummary:
        prompt = self._build_prompt(compact_input, previous_summary) # 先构造用户 prompt
        try:
            response = self._client().responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                max_output_tokens=self.max_output_tokens,
                store=False,
            )
            content = (_response_text(response) or "").strip()
        except Exception:
            return self.fallback.summarize(
                compact_input,
                previous_summary=previous_summary,
            )
        if not content:
            return self.fallback.summarize(
                compact_input,
                previous_summary=previous_summary,
            )
        return MemorySummary(content=content)

    def _client(self) -> Any:
        if self.client is not None:
            return self.client
        from openai import OpenAI
        return OpenAI()

    def _system_prompt(self) -> str:
        return (
            "Summarize compacted agent conversation history. Return only these "
            "sections: User goals, Constraints, Decisions, Important facts, Open tasks."
        )

    def _build_prompt(
        self,
        compact_input: str,
        previous_summary: MemorySummary | None,
    ) -> str:
        previous = previous_summary.content if previous_summary is not None else "None."
        return f"Previous summary:\n{previous}\n\nCompact input:\n{compact_input}"



# 独立负责判断是否压缩、切出旧 turns、保留最近 turns，并生成新的 MemorySummary
class MemoryCompressor:
    def __init__(
        self,
        policy: CompactionPolicy | None = None,
        *,
        summarizer: MemorySummarizer | None = None,
    ) -> None:
        self.policy = policy or CompactionPolicy()
        self.summarizer = summarizer or RuleBasedSummarizer()

    def should_compact(self, turns: list[SessionTurn]) -> bool:
        threshold = self.policy.compact_after_turns
        return threshold is not None and threshold > 0 and len(turns) > threshold

    # 把 turns 切分成两部分 要压缩 turns 与 无需压缩turns
    def split_turns(
        self, turns: list[SessionTurn]
    ) -> tuple[list[SessionTurn], list[SessionTurn]]:
        if not self.should_compact(turns):  # 如果不需要压缩，直接返回
            return [], list(turns)
        keep_turns = max(self.policy.keep_recent_turns, 1)  # 至少保留几个最近 turn
        compact_count = len(turns) - keep_turns # 要压缩多少个 turn
        if compact_count <= 0:
            return [], list(turns)
        return turns[:compact_count], turns[compact_count:]


    # 压缩记忆
    def compact(
        self,
        turns: list[SessionTurn],
        *,
        summary: MemorySummary | None = None,
    ) -> CompactionResult | None:
        compacted_turns, kept_turns = self.split_turns(turns) # 切分turns
        if not compacted_turns:
            return None

        # 合并 需要压缩turns 进 summary
        content, source_turn_count = self._merge_summary(summary, compacted_turns)
        return CompactionResult(
            summary=MemorySummary(
                content=self._trim_summary(content),
                source_turn_count=source_turn_count,
            ),
            turns=kept_turns,
        )

    # 把旧 turns 转成规则剪切后的 compact input
    def build_compact_input(self, turns: list[SessionTurn]) -> str:
        return "\n\n".join(
            _render_compact_turn(turn, index, self.policy)
            for index, turn in enumerate(turns, start=1)
        )

    def _merge_summary(
        self,
        summary: MemorySummary | None,
        compacted_turns: list[SessionTurn],
    ) -> tuple[str, int]:
        previous_count = 0
        if summary is not None and summary.content:
            previous_count = summary.source_turn_count
        compact_input = self.build_compact_input(compacted_turns)
        summarized = self.summarizer.summarize(
            compact_input,
            previous_summary=summary,
        )
        # 返回合并后的内容和总 turn 数
        return summarized.content, previous_count + len(compacted_turns)


    # 限制并截断 summary 最大字符长度
    def _trim_summary(self, content: str) -> str:
        max_chars = self.policy.max_summary_chars
        if max_chars is None:
            return content
        if max_chars <= 0:
            return ""
        return content[-max_chars:]


@dataclass
class AgentSession(AgentMemory):
    max_steps: int | None = None
    max_turns: int | None = None
    turns: list[SessionTurn] = field(default_factory=list)
    summary: MemorySummary | None = None
    compact_after_turns: int | None = None
    compact_keep_turns: int = 2
    compaction_policy: CompactionPolicy | None = None
    summarizer: MemorySummarizer | None = None

    # 在 __init__ 执行完之后自动调用的后处理函数
    def __post_init__(self) -> None:
        if not self.turns and (self.task is not None or self.steps):
            self.turns.append(SessionTurn(task=self.task, steps=list(self.steps)))
        if self.turns:
            self.task = self.turns[-1].task
        self._trim_turns()
        self._trim_steps()
        self._sync_steps_from_turns()
        self._compact_if_needed()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "max_steps": self.max_steps,
            "max_turns": self.max_turns,
            "summary": self.summary.to_dict() if self.summary is not None else None,
            "compaction_policy": self._effective_compaction_policy().to_dict(),
            "compact_after_turns": self.compact_after_turns,
            "compact_keep_turns": self.compact_keep_turns,
            "turns": [
                {
                    "task": turn.task,
                    "steps": [_step_to_dict(step) for step in turn.steps],
                }
                for turn in self.turns
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentSession:
        policy = (
            CompactionPolicy.from_dict(data["compaction_policy"])
            if isinstance(data.get("compaction_policy"), dict)
            else None
        )
        turns = [
            SessionTurn(
                task=turn.get("task"),
                steps=[_step_from_dict(step) for step in turn.get("steps", [])],
            )
            for turn in data.get("turns", [])
        ]
        return cls(
            task=data.get("task"),
            max_steps=data.get("max_steps"),
            max_turns=data.get("max_turns"),
            turns=turns,
            summary=(
                MemorySummary.from_dict(data["summary"])
                if isinstance(data.get("summary"), dict)
                else None
            ),
            compact_after_turns=(
                data.get("compact_after_turns")
                if policy is None
                else policy.compact_after_turns
            ),
            compact_keep_turns=(
                int(data.get("compact_keep_turns", 2))
                if policy is None
                else policy.keep_recent_turns
            ),
            compaction_policy=policy,
        )

    def add_task(self, task: str) -> None:
        self.task = task
        self.turns.append(SessionTurn(task=task))
        self._trim_turns()
        self._trim_steps()
        self._sync_steps_from_turns()
        self._compact_if_needed()

    def append(self, step: StepRecord) -> None:
        self.add_step(step)

    def add_step(self, step: StepRecord) -> None:
        current_turn = self._ensure_current_turn() #  找到当前 turn
        current_turn.steps.append(step)
        self._trim_turns()
        self._trim_steps()
        self._sync_steps_from_turns()
        self._compact_if_needed()

    def reset_steps(self) -> None:
        for turn in self.turns:
            turn.steps.clear()
        self._sync_steps_from_turns()


    # 把整个 session 的所有 turns 转换成模型上下文。
    def to_messages(self, max_steps: int | None = None) -> list[ChatMessage]:
        return self._turns_to_messages(self.turns, max_steps=max_steps)

    # 获取 session 中要给模型的消息
    def get_items(self, limit: int | None = None) -> list[ChatMessage]:
        return self.replay(limit=limit)


    # 把一组 ChatMessage 写入 session。遇到 user 就开启新 turn；
    # 后面的 tool_call、tool_response、assistant 会作为这个 turn 的 step messages 保存。
    def add_items(self, items: list[Any]) -> None:
        pending_messages: list[ChatMessage] = []
        for raw_item in items:
            item = session_item_to_message(raw_item)
            if item.role == "user":
                if pending_messages:
                    self.add_step(StepRecord(
                        step_number=len(self.steps) + 1,
                        messages=pending_messages,
                    ))
                    pending_messages = []
                self.add_task(item.content)
            else:
                pending_messages.append(item)
        if pending_messages:
            self.add_step(StepRecord(
                step_number=len(self.steps) + 1,
                messages=pending_messages,
            ))

    def pop_item(self) -> ChatMessage | None:
        messages = self.replay()
        if not messages:
            return None
        last_message = messages[-1]
        if self.turns and self.turns[-1].steps:
            self.turns[-1].steps.pop()
        elif self.turns:
            self.turns.pop()
            self.task = self.turns[-1].task if self.turns else None
        self._sync_steps_from_turns()
        return last_message

    def clear_session(self) -> None:
        self.clear()

    # 按历史顺序回放会话
    def replay(self, limit: int | None = None) -> list[ChatMessage]:
        return self._turns_to_messages(self._select_turns(limit))

    def clear(self, *, keep_task: bool = False) -> None:
        kept_task = self.task if keep_task else None
        self.turns.clear()
        self.steps.clear()
        self.task = kept_task
        self.summary = None
        if kept_task is not None:
            self.turns.append(SessionTurn(task=kept_task))

    def _compact_if_needed(self) -> None:
        compressor = MemoryCompressor(
            self._effective_compaction_policy(),
            summarizer=self.summarizer,
        )
        result = compressor.compact(self.turns, summary=self.summary)
        if result is None:
            return
        self.summary = result.summary
        self.turns = result.turns
        self.task = self.turns[-1].task if self.turns else None
        self._sync_steps_from_turns()

    def _effective_compaction_policy(self) -> CompactionPolicy:
        if self.compaction_policy is not None:
            return self.compaction_policy
        return CompactionPolicy(
            compact_after_turns=self.compact_after_turns,
            keep_recent_turns=self.compact_keep_turns,
        )

    def _trim_steps(self) -> None:
        if self.max_steps is None:
            return
        for turn in self.turns:
            if self.max_steps <= 0:
                turn.steps.clear()
            else:
                del turn.steps[:-self.max_steps]

    # 限制每一轮 turn 里最多保留多少 step。
    def _trim_turns(self) -> None:
        if self.max_turns is None:
            return
        if self.max_turns <= 0:
            self.turns.clear()
            self.task = None
            return
        del self.turns[:-self.max_turns]
        self.task = self.turns[-1].task if self.turns else None

    # 保证当前一定有一个 turn 可以存 step。 如果 self.turns 为空，就自动创建一个：
    def _ensure_current_turn(self) -> SessionTurn:
        if not self.turns:
            self.turns.append(SessionTurn(task=self.task))
        return self.turns[-1]

    def _select_turns(self, limit: int | None = None) -> list[SessionTurn]:
        max_turns = self.max_turns if limit is None else limit
        if max_turns is None:
            return list(self.turns)
        if max_turns <= 0:
            return []
        return self.turns[-max_turns:]

    def _turns_to_messages(
        self,
        turns: list[SessionTurn],
        *,
        max_steps: int | None = None,
    ) -> list[ChatMessage]:
        step_limit = self.max_steps if max_steps is None else max_steps
        messages: list[ChatMessage] = []
        if self.summary is not None:
            messages.append(self.summary.to_message())
        for turn in turns:
            if turn.task is not None:
                messages.append(ChatMessage(role="user", content=turn.task))
            for step in _select_recent_steps(turn.steps, step_limit):
                _append_step_messages(messages, step)
        return messages

    # 把多轮 turns 里的 steps 展平成 self.steps
    def _sync_steps_from_turns(self) -> None:
        self.steps = [step for turn in self.turns for step in turn.steps]


class JsonSession:
    def __init__(
        self,
        path: str | Path,
        *,
        max_steps: int | None = None,
        max_turns: int | None = None,
        compaction_policy: CompactionPolicy | None = None,
        compact_after_turns: int | None = None,
        compact_keep_turns: int | None = None,
        summarizer: MemorySummarizer | None = None,
    ) -> None:
        self.path = Path(path)
        self.max_steps = max_steps
        self.max_turns = max_turns
        self.compaction_policy = compaction_policy
        self.compact_after_turns = compact_after_turns
        self.compact_keep_turns = compact_keep_turns
        self.summarizer = summarizer

    def get_items(self, limit: int | None = None) -> list[ChatMessage]:
        return self._load().get_items(limit=limit)

    def add_items(self, items: list[Any]) -> None:
        if not items:
            return
        session = self._load()
        session.add_items(items)
        self._save(session)

    def pop_item(self) -> ChatMessage | None:
        session = self._load()
        item = session.pop_item()
        if item is not None:
            self._save(session)
        return item

    def clear_session(self) -> None:
        self._save(self._empty_session())

    def _empty_session(self) -> AgentSession:
        kwargs: dict[str, Any] = {
            "max_steps": self.max_steps,
            "max_turns": self.max_turns,
        }
        if self.compact_after_turns is not None:
            kwargs["compact_after_turns"] = self.compact_after_turns
        if self.compact_keep_turns is not None:
            kwargs["compact_keep_turns"] = self.compact_keep_turns
        if self.compaction_policy is not None:
            kwargs["compaction_policy"] = self.compaction_policy
        if self.summarizer is not None:
            kwargs["summarizer"] = self.summarizer
        return AgentSession(**kwargs)

    # 从 JSON 文件恢复 AgentSession
    def _load(self) -> AgentSession:
        try:
            # 读取 JSON 文件 , 把 JSON 字符串转 Python 对象
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._empty_session() # 文件不存在时返回空 session

        # 取出真正的 session 数据
        session_data = raw.get("session", raw) if isinstance(raw, dict) else {}
        if not isinstance(session_data, dict):
            return self._empty_session()

        session = AgentSession.from_dict(session_data)
        if self.max_steps is not None:
            session.max_steps = self.max_steps
        if self.max_turns is not None:
            session.max_turns = self.max_turns
        if self.compact_after_turns is not None:
            session.compact_after_turns = self.compact_after_turns
        if self.compact_keep_turns is not None:
            session.compact_keep_turns = self.compact_keep_turns
        if self.compaction_policy is not None:
            session.compaction_policy = self.compaction_policy
            session.compact_after_turns = self.compaction_policy.compact_after_turns
            session.compact_keep_turns = self.compaction_policy.keep_recent_turns
        if self.summarizer is not None:
            session.summarizer = self.summarizer
        session.__post_init__()
        return session

    # 把 AgentSession 保存到 JSON 文件
    def _save(self, session: AgentSession) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "session": session.to_dict()} # 把 AgentSession 包装成一个 JSON 结构
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), # 把 payload 转成 JSON 字符串
            encoding="utf-8",
        )
