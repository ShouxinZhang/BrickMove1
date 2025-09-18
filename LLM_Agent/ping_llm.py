#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _add_sys_path():
    here = Path(__file__).parent
    root = _repo_root()
    for p in [str(here), str(root)]:
        if p not in sys.path:
            sys.path.insert(0, p)


_add_sys_path()

import llm_agent as base  # type: ignore


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ping OpenRouter via llm_agent client, mirroring llm_agent settings")
    p.add_argument("--message", type=str, default="一次性原本返回目前为止全部的历史对话记录", help="User message to send")
    p.add_argument("--model", type=str, default="moonshotai/kimi-k2-0905", help="OpenRouter model id")
    p.add_argument("--api-key", type=str, default="", help="OpenRouter API key (overrides env/.openrouter_key)")
    p.add_argument("--append-system", type=str, default="", help="Extra system instructions to prepend")
    p.add_argument("--max-tokens", type=int, default=256, help="Max tokens for completion (0 = unlimited)")
    p.add_argument("--no-max-tokens", action="store_true", help="Omit max_tokens in API payload")
    p.add_argument("--temperature", type=float, default=0.2, help="Sampling temperature")
    p.add_argument("--top-p", type=float, default=0.9, help="Top-p nucleus sampling")
    p.add_argument("--fewshot", action="store_true", help="Include built-in few-shot example like llm_agent")
    p.add_argument("--fewshot-json", type=Path, default=None, help="Path to history JSON (extra turns). Defaults to PromptExample/history.json if present")
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)

    api_key = args.api_key or os.getenv("OPENROUTER_API_KEY") or (base._load_api_key_from_keyfile() or "")
    if not api_key:
        print("ERROR: Provide OpenRouter API key: --api-key, or set OPENROUTER_API_KEY env, or place it in .openrouter_key", file=sys.stderr)
        return 2

    system_prompt = base.DEFAULT_SYSTEM_PROMPT
    if args.append_system:
        system_prompt = f"{system_prompt}\n\nAdditional guidance:\n{args.append_system.strip()}".strip()

    # Load extra conversation turns (history)
    extra_turns: List[Dict[str, str]] = []
    history_path: Optional[Path] = args.fewshot_json
    if history_path is None:
        cand = Path(__file__).parent / "PromptExample" / "history.json"
        if cand.exists():
            history_path = cand
    if history_path is not None:
        try:
            extra_turns = base.load_fewshot_messages(history_path)
        except Exception as exc:
            print(f"WARN: failed to load history from {history_path}: {exc}", file=sys.stderr)

    # Build messages like llm_agent.build_messages
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for t in extra_turns:
        role = t.get("role")
        content = t.get("content")
        if role in base.VALID_MESSAGE_ROLES and isinstance(content, str):
            messages.append({"role": role, "content": content})
    if args.fewshot:
        messages.append({
            "role": "user",
            "content": (
                "Here is a Lean file. Transform it per the rules and return only the final Lean source.\n\n"
                + base.FEWSHOT_USER_EXAMPLE
            ),
        })
        messages.append({"role": "assistant", "content": base.FEWSHOT_ASSISTANT_EXAMPLE})
    messages.append({"role": "user", "content": args.message})

    client = base.OpenRouterClient(api_key)
    max_tokens: Optional[int] = None if args.no_max_tokens or args.max_tokens <= 0 else args.max_tokens
    data = client.chat_completion(
        messages,
        model=args.model,
        max_tokens=max_tokens,
        temperature=args.temperature,
        top_p=args.top_p,
    )
    choice = (data.get("choices") or [{}])[0]
    content = ((choice.get("message") or {}).get("content") or "").strip()
    print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
