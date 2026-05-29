from __future__ import annotations

import argparse

from .chat_runtime import (
    ChatRuntime,
    ChatRuntimeConfig,
    build_chat_runtime,
)


EXIT_COMMANDS = {"/exit", "/quit"}
HISTORY_LIMIT = 10
HELP_TEXT = """Commands:
/help     Show this help.
/clear    Clear the current session.
/history  Show recent session messages.
/exit     Exit the chat.
/quit     Exit the chat."""


def run_chat_cli(runtime: ChatRuntime) -> None:
    while True:
        try:
            message = input("user> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break

        message = message.strip()
        if not message:
            continue
        if message.startswith("/") and not _handle_chat_command(message, runtime):
            break
        if message.startswith("/"):
            continue

        turn = runtime.run_turn(message)
        if turn.answer is not None:
            print(f"assistant> {turn.answer}")


def _handle_chat_command(command: str, runtime: ChatRuntime) -> bool:
    command = command.lower()
    if command in EXIT_COMMANDS:
        return False
    if command == "/help":
        print(HELP_TEXT)
    elif command == "/clear":
        if runtime.session is None:
            print("No session to clear.")
        else:
            runtime.session.clear_session()
            print("Session cleared.")
    elif command == "/history":
        _print_history(runtime)
    else:
        print(f"Unknown command: {command}")
    return True


def _print_history(runtime: ChatRuntime) -> None:
    if runtime.session is None:
        print("No session history.")
        return

    items = runtime.session.get_items(limit=HISTORY_LIMIT)
    if not items:
        print("No session history.")
        return

    for index, item in enumerate(items, start=1): # 遍历一个可迭代对象时，同时拿到下标和元素值
        role = getattr(item, "role", "item")
        content = _short_text(getattr(item, "content", item))
        print(f"{index}. {role}: {content}")


# 把多行历史压成单行，并把过长内容截断
def _short_text(value: object, limit: int = 80) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


# 定义这个命令行程序支持哪些参数
def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agents.chat_cli")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--session")
    parser.add_argument("--instructions")
    parser.add_argument("--max-turns", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    config = ChatRuntimeConfig(
        model=args.model,
        instructions=args.instructions,
        session_path=args.session,
        max_turns=args.max_turns,
    )
    run_chat_cli(build_chat_runtime(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
