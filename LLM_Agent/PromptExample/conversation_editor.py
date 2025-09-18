#!/usr/bin/env python3
"""Lightweight conversation editor server for few-shot prompt curation."""

from __future__ import annotations

import argparse
import json
import random
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE_DIR = Path(__file__).parent.resolve()
DEFAULT_JSON = BASE_DIR / "history.json"
RANDOM_JSON_DIR = BASE_DIR / "RandomExample" / "jsonData"

FORMAL_KEYS = [
    "formalProof",
    "formal_proof",
    "lean",
    "leanCode",
    "lean_code",
    "proof",
    "code",
]
MAIN_KEYS = [
    "main theorem statement",
    "main_theorem_statement",
    "main_theorem",
    "statement",
]


def _resolve_target(path_value: str | None) -> Path:
    candidate = DEFAULT_JSON if not path_value else Path(path_value)
    if not candidate.is_absolute():
        candidate = (BASE_DIR / candidate).resolve()
    else:
        candidate = candidate.resolve()
    try:
        candidate.relative_to(BASE_DIR)
    except ValueError as exc:
        raise ValueError("Path outside allowed directory") from exc
    if candidate.suffix.lower() != ".json":
        raise ValueError("Only .json files are supported")
    return candidate


def _extract_field(obj: dict, keys: list[str]) -> str | None:
    for key in keys:
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _sample_random_messages() -> dict:
    if not RANDOM_JSON_DIR.exists():
        raise FileNotFoundError("RandomExample/jsonData 目录不存在")
    sources = [p for p in RANDOM_JSON_DIR.glob("*.json") if p.is_file()]
    if not sources:
        raise FileNotFoundError("RandomExample/jsonData 中没有可用的 JSON 文件")

    chosen_file = random.choice(sources)
    try:
        raw = chosen_file.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:
        raise ValueError(f"无法读取或解析 {chosen_file.name}: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError(f"{chosen_file.name} 顶层必须是数组(list)")

    candidates: list[tuple[int, str, str | None]] = []
    for idx, entry in enumerate(data):
        if not isinstance(entry, dict):
            continue
        formal = _extract_field(entry, FORMAL_KEYS)
        if not formal:
            continue
        main = _extract_field(entry, MAIN_KEYS)
        candidates.append((idx, formal, main if main is not None else None))

    if not candidates:
        raise ValueError(f"{chosen_file.name} 中没有包含 formalProof 的条目")

    idx, formal, main = random.choice(candidates)
    formal = formal.strip()
    user_content = (
        "Here is a Lean file. Transform it per the rules and return only the final Lean source.\n\n"
        + formal
    )
    assistant_content = (main or "").strip()
    if not assistant_content:
        assistant_content = "-- 示例缺少 main theorem statement"

    rel_path = chosen_file.relative_to(BASE_DIR)
    return {
        "messages": [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": assistant_content},
        ],
        "source": str(rel_path),
        "index": idx,
    }


class ConversationHandler(BaseHTTPRequestHandler):
    server_version = "ConversationEditor/0.1"

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler naming)
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self._serve_file(BASE_DIR / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/styles.css":
            self._serve_file(BASE_DIR / "styles.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/api/conversation":
            self._handle_get_conversation(parsed)
            return
        if parsed.path == "/api/random-example":
            self._handle_random_example()
            return
        self.send_error(404, "Not found")

    def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler naming)
        parsed = urlparse(self.path)
        if parsed.path == "/api/conversation":
            self._handle_post_conversation(parsed)
            return
        self.send_error(404, "Not found")

    # Serve index/css -------------------------------------------------
    def _serve_file(self, file_path: Path, content_type: str) -> None:
        if not file_path.exists():
            self.send_error(404, "File not found")
            return
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # Conversation API ------------------------------------------------
    def _handle_get_conversation(self, parsed) -> None:
        query = parse_qs(parsed.query)
        path_value = query.get("path", [None])[0]
        try:
            target = _resolve_target(path_value)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("[]\n", encoding="utf-8")
            messages = []
        else:
            try:
                raw = target.read_text(encoding="utf-8")
                messages = json.loads(raw) if raw.strip() else []
                if not isinstance(messages, list):
                    raise ValueError("JSON root must be a list of messages")
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                self._send_json(500, {"error": f"Failed to read JSON: {exc}"})
                return

        self._send_json(200, {"messages": messages, "path": str(target.relative_to(BASE_DIR))})

    def _handle_post_conversation(self, parsed) -> None:
        query = parse_qs(parsed.query)
        path_value = query.get("path", [None])[0]
        try:
            target = _resolve_target(path_value)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0:
            self._send_json(400, {"error": "Empty request body"})
            return
        payload = self.rfile.read(content_length)
        try:
            parsed_body = json.loads(payload)
        except json.JSONDecodeError as exc:
            self._send_json(400, {"error": f"Invalid JSON: {exc}"})
            return

        messages = parsed_body.get("messages") if isinstance(parsed_body, dict) else parsed_body
        if not isinstance(messages, list):
            self._send_json(400, {"error": "Body must contain a list of messages"})
            return

        normalized: list[dict[str, str]] = []
        for idx, entry in enumerate(messages):
            if not isinstance(entry, dict):
                self._send_json(400, {"error": f"Message at index {idx} is not an object"})
                return
            role = entry.get("role")
            content = entry.get("content", "")
            if role not in {"system", "user", "assistant"}:
                self._send_json(400, {"error": f"Unsupported role at index {idx}: {role!r}"})
                return
            if not isinstance(content, str):
                self._send_json(400, {"error": f"Content at index {idx} must be a string"})
                return
            normalized.append({"role": role, "content": content})

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._send_json(200, {"ok": True, "path": str(target.relative_to(BASE_DIR)), "count": len(normalized)})

    def _handle_random_example(self) -> None:
        try:
            payload = _sample_random_messages()
        except FileNotFoundError as exc:
            self._send_json(404, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(500, {"error": str(exc)})
            return
        self._send_json(200, payload)

    # Helpers ---------------------------------------------------------
    def _send_json(self, status: int, data) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args) -> None:  # pragma: no cover - silence default logging
        sys.stderr.write("[server] " + format % args + "\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Few-shot conversation editor server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8020, help="Port to bind (default: 8020)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    server = ThreadingHTTPServer((args.host, args.port), ConversationHandler)
    print(f"Serving conversation editor on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
