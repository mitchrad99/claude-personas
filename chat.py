#!/usr/bin/env python3
"""
Claude Persona CLI — multi-persona chat with persistent memory
Usage: python chat.py
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("Missing dependency. Run: pip install anthropic")
    sys.exit(1)

PERSONAS_DIR = Path(__file__).parent / "personas"
MODEL = "claude-sonnet-4-6"
MAX_HISTORY_TURNS = 20      # turns kept in live context
SUMMARY_THRESHOLD = 15      # summarize when history exceeds this


# ── Persona I/O ─────────────────────────────────────────────────────────────

def load_persona(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def save_persona(path: Path, persona: dict) -> None:
    with open(path, "w") as f:
        json.dump(persona, f, indent=2)


def list_personas() -> list[tuple[Path, dict]]:
    paths = sorted(PERSONAS_DIR.glob("*.json"))
    return [(p, load_persona(p)) for p in paths]


# ── History helpers ──────────────────────────────────────────────────────────

def build_messages(persona: dict, new_user_msg: str) -> list[dict]:
    """Assemble the full messages array for the API call."""
    messages = []

    # Inject memory summary as a synthetic assistant turn if it exists
    if persona.get("memory_summary"):
        messages.append({
            "role": "user",
            "content": "[Context from previous sessions]"
        })
        messages.append({
            "role": "assistant",
            "content": persona["memory_summary"]
        })

    # Recent conversation history
    messages.extend(persona.get("history", []))

    # Current user message
    messages.append({"role": "user", "content": new_user_msg})
    return messages


def append_to_history(persona: dict, user_msg: str, assistant_msg: str) -> None:
    persona["history"].append({"role": "user", "content": user_msg})
    persona["history"].append({"role": "assistant", "content": assistant_msg})


# ── Summarization ────────────────────────────────────────────────────────────

def summarize_history(client: anthropic.Anthropic, persona: dict) -> str:
    """Compress old history into a memory summary, preserve recent turns."""
    history = persona["history"]
    if len(history) <= SUMMARY_THRESHOLD:
        return persona.get("memory_summary", "")

    # Keep the most recent MAX_HISTORY_TURNS turns; summarize the rest
    to_summarize = history[:-MAX_HISTORY_TURNS]
    persona["history"] = history[-MAX_HISTORY_TURNS:]

    prior_summary = persona.get("memory_summary", "")
    conversation_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in to_summarize
    )

    prompt = f"""You are summarizing a conversation for long-term memory storage.

Prior memory summary (if any):
{prior_summary or '(none)'}

New conversation to incorporate:
{conversation_text}

Write a concise memory summary (200-300 words) capturing:
- Key decisions made or conclusions reached
- Important facts, names, or context established
- Any ongoing tasks or open questions
- Tone and working style preferences observed

Write in second person ("You discussed...", "The user prefers..."). Be specific — this summary will be used to restore context in future sessions."""

    response = client.messages.create(
        model=MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text


# ── Core chat ────────────────────────────────────────────────────────────────

def chat_turn(client: anthropic.Anthropic, persona: dict, user_msg: str) -> str:
    messages = build_messages(persona, user_msg)
    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=persona["system_prompt"],
        messages=messages
    )
    return response.content[0].text


# ── UI helpers ───────────────────────────────────────────────────────────────

COLORS = {
    "fundraising": "\033[33m",   # amber
    "policy":      "\033[36m",   # cyan
    "linkedin":    "\033[34m",   # blue
    "reset":       "\033[0m",
    "dim":         "\033[2m",
    "bold":        "\033[1m",
    "green":       "\033[32m",
    "red":         "\033[31m",
}

def c(color: str, text: str) -> str:
    return f"{COLORS.get(color, '')}{text}{COLORS['reset']}"


def print_header(persona: dict, persona_key: str) -> None:
    name = persona["name"]
    desc = persona["description"]
    turns = len(persona.get("history", [])) // 2
    has_memory = bool(persona.get("memory_summary"))

    print()
    color = persona_key.split(".")[0] if "." not in persona_key else "bold"
    print(c("bold", f"  {name}"))
    print(c("dim", f"  {desc}"))
    print(c("dim", f"  {turns} turns in history  {'· memory summary loaded' if has_memory else '· no prior memory'}"))
    print(c("dim", "  " + "─" * 50))
    print(c("dim", "  Commands: /switch  /history  /clear  /save  /quit"))
    print()


def pick_persona() -> tuple[Path, dict] | None:
    personas = list_personas()
    if not personas:
        print(c("red", "No personas found in personas/ folder."))
        return None

    print()
    print(c("bold", "  Choose a persona:"))
    print()
    for i, (path, p) in enumerate(personas, 1):
        turns = len(p.get("history", [])) // 2
        mem = "· memory" if p.get("memory_summary") else ""
        print(f"  {c('bold', str(i))}  {p['name']}")
        print(c("dim", f"     {p['description']}  ·  {turns} turns {mem}"))
    print()

    while True:
        try:
            choice = input("  Enter number: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(personas):
                return personas[idx]
            print(c("red", "  Invalid choice."))
        except (ValueError, KeyboardInterrupt):
            return None


# ── Main loop ────────────────────────────────────────────────────────────────

def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(c("red", "\n  ANTHROPIC_API_KEY not set."))
        print(c("dim", "  Export it: export ANTHROPIC_API_KEY=sk-ant-...\n"))
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print(c("bold", "\n  Claude Persona CLI"))
    print(c("dim",  "  Multi-persona chat with persistent memory\n"))

    result = pick_persona()
    if not result:
        return

    persona_path, persona = result
    persona_key = persona_path.stem

    while True:
        print_header(persona, persona_key)

        # Session loop
        while True:
            try:
                user_input = input(c("green", "  You: ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not user_input:
                continue

            # Commands
            if user_input.startswith("/"):
                cmd = user_input.lower()

                if cmd == "/quit":
                    save_persona(persona_path, persona)
                    print(c("dim", "\n  Session saved. Goodbye.\n"))
                    return

                elif cmd == "/switch":
                    save_persona(persona_path, persona)
                    print(c("dim", "\n  Saved. Switching persona...\n"))
                    result = pick_persona()
                    if result:
                        persona_path, persona = result
                        persona_key = persona_path.stem
                    break

                elif cmd == "/history":
                    history = persona.get("history", [])
                    if not history:
                        print(c("dim", "  No history yet.\n"))
                    else:
                        print()
                        for msg in history[-10:]:
                            role = "You" if msg["role"] == "user" else persona["name"]
                            print(c("dim", f"  {role}: {msg['content'][:120]}..."))
                        print()

                elif cmd == "/clear":
                    confirm = input(c("red", "  Clear all history and memory? (yes/no): ")).strip()
                    if confirm.lower() == "yes":
                        persona["history"] = []
                        persona["memory_summary"] = ""
                        save_persona(persona_path, persona)
                        print(c("dim", "  Cleared.\n"))

                elif cmd == "/save":
                    save_persona(persona_path, persona)
                    print(c("dim", "  Saved.\n"))

                else:
                    print(c("dim", "  Unknown command. Try /switch /history /clear /save /quit\n"))
                continue

            # Normal chat turn
            print(c("dim", f"\n  {persona['name']}: "), end="", flush=True)
            try:
                reply = chat_turn(client, persona, user_input)
            except anthropic.APIError as e:
                print(c("red", f"\n  API error: {e}\n"))
                continue

            # Print reply with indentation
            lines = reply.strip().split("\n")
            print()
            for line in lines:
                print(f"  {line}")
            print()

            # Update history
            append_to_history(persona, user_input, reply)

            # Auto-summarize if history is getting long
            if len(persona["history"]) > SUMMARY_THRESHOLD * 2:
                print(c("dim", "  [Compressing memory...] "), end="", flush=True)
                persona["memory_summary"] = summarize_history(client, persona)
                print(c("dim", "done\n"))

            # Auto-save after every turn
            save_persona(persona_path, persona)


if __name__ == "__main__":
    main()
