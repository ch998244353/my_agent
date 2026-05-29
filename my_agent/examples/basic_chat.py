from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import (  # noqa: E402
    Agent,
    AgentMemory,
    AgentSession,
    ChatRuntime,
    ChatMessage,
    ModelResponse,
)


class DemoChatModel:
    def __init__(self) -> None:
        self.turn_count = 0

    def get_response(self, messages, tool_specs):
        self.turn_count += 1
        user_text = "\n".join(
            message.content for message in messages if message.role == "user"
        )
        if "What did I tell you" in user_text and "Ada" in user_text:
            output_text = "Yes, Ada. You said you are learning agents."
        else:
            output_text = "Hi Ada. I will remember that you are learning agents."
        return ModelResponse(
            response_id=f"demo_chat_{self.turn_count}",
            output=[],
            output_text=output_text,
            tool_calls=[],
        )


def build_demo_agent() -> Agent:
    return Agent(
        memory=AgentMemory(),
        model=DemoChatModel(),
        instructions="Answer briefly and use the conversation session.",
    )


def run_demo() -> tuple[list[str], list[ChatMessage]]:
    session = AgentSession()
    runtime = ChatRuntime(agent=build_demo_agent(), session=session)
    prompts = [
        "My name is Ada and I am learning agents.",
        "What did I tell you about myself?",
    ]
    answers: list[str] = []
    for prompt in prompts:
        turn = runtime.run_turn(prompt)
        answers.append(turn.answer or "")
    return answers, session.replay()


def main() -> None:
    answers, replay = run_demo()
    for index, answer in enumerate(answers, start=1):
        print(f"assistant[{index}]: {answer}")
    print("session replay:")
    for message in replay:
        print(f"{message.role}: {message.content}")


if __name__ == "__main__":
    main()
