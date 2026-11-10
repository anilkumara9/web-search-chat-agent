#!/usr/bin/env python3
"""CLI: `python chat.py` for a REPL, or `python chat.py "question"` for one shot."""
import sys
from agent.core import Agent


def main():
    agent = Agent()
    if len(sys.argv) > 1:
        agent.ask(" ".join(sys.argv[1:]), stream_cb=lambda t: print(t, end="", flush=True))
        print(f"\n[{agent.last_stats['total_ms']} ms, searches: {len(agent.last_stats['searches'])}]")
        return
    print("Chat agent. Ctrl-D to exit.")
    while True:
        try:
            q = input("\nyou> ").strip()
        except EOFError:
            break
        if q:
            print("bot> ", end="", flush=True)
            agent.ask(q, stream_cb=lambda t: print(t, end="", flush=True))
            print(f"\n     [{agent.last_stats['total_ms']} ms, searches: {len(agent.last_stats['searches'])}]")


if __name__ == "__main__":
    main()
