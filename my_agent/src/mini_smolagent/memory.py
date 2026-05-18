from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import ChatMessage, StepRecord, ToolCall
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


@dataclass
class AgentSession(AgentMemory):
    max_steps: int | None = None
    max_turns: int | None = None
    turns: list[SessionTurn] = field(default_factory=list)

    # 在 __init__ 执行完之后自动调用的后处理函数
    def __post_init__(self) -> None:
        if not self.turns and (self.task is not None or self.steps):
            self.turns.append(SessionTurn(task=self.task, steps=list(self.steps)))
        if self.turns:
            self.task = self.turns[-1].task
        self._trim_turns()
        self._trim_steps()
        self._sync_steps_from_turns()

    def add_task(self, task: str) -> None:
        self.task = task
        self.turns.append(SessionTurn(task=task))
        self._trim_turns()
        self._trim_steps()
        self._sync_steps_from_turns()

    def append(self, step: StepRecord) -> None:
        self.add_step(step)

    def add_step(self, step: StepRecord) -> None:
        current_turn = self._ensure_current_turn() #  找到当前 turn
        current_turn.steps.append(step)
        self._trim_turns()
        self._trim_steps()
        self._sync_steps_from_turns()

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

    # 按历史顺序回放会话
    def replay(self, limit: int | None = None) -> list[ChatMessage]:
        return self._turns_to_messages(self._select_turns(limit))

    def clear(self, *, keep_task: bool = False) -> None:
        kept_task = self.task if keep_task else None
        self.turns.clear()
        self.steps.clear()
        self.task = kept_task
        if kept_task is not None:
            self.turns.append(SessionTurn(task=kept_task))

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
        for turn in turns:
            if turn.task is not None:
                messages.append(ChatMessage(role="user", content=turn.task))
            for step in _select_recent_steps(turn.steps, step_limit):
                _append_step_messages(messages, step)
        return messages

    # 把多轮 turns 里的 steps 展平成 self.steps
    def _sync_steps_from_turns(self) -> None:
        self.steps = [step for turn in self.turns for step in turn.steps]
