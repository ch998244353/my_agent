from __future__ import annotations

import argparse

from .chat import ChatTurn, chat_turn_status_text
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
/status   Show the current runtime status.
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
        _print_chat_turn(turn)


def _print_chat_turn(turn: ChatTurn) -> None:
    if turn.answer is not None:
        print(f"assistant> {turn.answer}")
        return
    print(f"assistant> {chat_turn_status_text(turn)}")


def _handle_chat_command(command: str, runtime: ChatRuntime) -> bool:
    command = command.lower()
    if command in EXIT_COMMANDS:
        return False
    if command == "/help":
        print(HELP_TEXT)
    elif command == "/clear":
        if not runtime.session_enabled:
            print("Session is disabled; nothing to clear.")
        elif not runtime.clear_session():
            print("Session is unavailable; nothing to clear.")
        else:
            print("Session cleared. Future turns start without previous context.")
    elif command == "/history":
        _print_history(runtime)
    elif command == "/status":
        _print_status(runtime)
    else:
        print(f"Unknown command: {command}")
    return True


def _print_status(runtime: ChatRuntime) -> None:
    diagnostics = runtime.diagnostics
    print(f"Turns: {diagnostics.turn_count}")
    print(f"Session: {runtime.session_status_text}")
    turn = diagnostics.last_turn
    if turn is None:
        print("No chat turns have run yet.")
        return

    has_reply = turn.answer is not None or turn.has_final_answer
    print(f"Last reply: {'yes' if has_reply else 'no'}")
    if diagnostics.status_text is not None:
        print(f"Status: {diagnostics.status_text}")
    if diagnostics.error_summary is not None:
        print(f"Error: {diagnostics.error_summary}")
    if turn.stop_reason is not None:
        print(f"Stop reason: {turn.stop_reason}")


def _print_history(runtime: ChatRuntime) -> None:
    if not runtime.session_enabled:
        print("Session is disabled; no history is stored.")
        return

    items = runtime.history(limit=HISTORY_LIMIT)
    if not items:
        print("No session history.")
        return

    for index, item in enumerate(items, start=1): # 遍历一个可迭代对象时，同时拿到下标和元素值
        role = _history_role(item)
        content = _history_content(item)
        print(f"{index}. {role}: {content}")


def _history_role(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("role", "item"))
    return str(getattr(item, "role", "item"))


def _history_content(item: object) -> str:
    if isinstance(item, dict):
        return _short_text(item.get("content", item))
    return _short_text(getattr(item, "content", item))


# 把多行历史压成单行，并把过长内容截断
def _short_text(value: object, limit: int = 80) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


# 定义这个命令行程序支持哪些参数
def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agents.chat_cli")
    parser.add_argument("--model", default="gpt-5.4")
    session_group = parser.add_mutually_exclusive_group()
    session_group.add_argument("--session")
    session_group.add_argument("--no-session", action="store_true")
    parser.add_argument("--instructions")
    parser.add_argument("--max-steps", type=_positive_int, default=5)
    parser.add_argument("--max-turns", type=_positive_int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    config = ChatRuntimeConfig(
        model=args.model,
        instructions=args.instructions,
        session_path=args.session,
        use_session=not args.no_session,
        max_steps=args.max_steps,
        max_turns=args.max_turns,
    )
    run_chat_cli(build_chat_runtime(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
