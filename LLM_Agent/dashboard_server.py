#!/usr/bin/env python3
"""Lightweight local HTTP bridge for manager_dashboard.html.

WARNING: executes incoming commands; use only in a trusted local environment.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

DEFAULT_PORT = 8765
DASHBOARD_HTML = Path(__file__).with_name("manager_dashboard.html")
CACHE_DIRNAME = ".dashboard_cache"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _slugify(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip())
    slug = slug.strip("-._")
    return slug or "upload"


def _ensure_relative_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        raise ValueError("path must be relative")
    parts = [p for p in path.parts if p not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError("path must not traverse upwards")
    if not parts:
        raise ValueError("empty path")
    return Path(*parts)


def _safe_rmtree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


class CommandRunnerHandler(BaseHTTPRequestHandler):
    server_version = "DashboardRunner/0.1"

    def do_OPTIONS(self) -> None:  # noqa: N802 (HTTP verb name)
        self.send_response(204)
        self._write_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 (HTTP verb name)
        if self.path in ('/', '/index.html'):
            dashboard: Path = getattr(self.server, 'dashboard', DASHBOARD_HTML)  # type: ignore[attr-defined]
            if dashboard.exists():
                self._write_file(dashboard)
            else:
                self.send_error(404, 'Dashboard HTML not found')
            return
        if self.path == '/favicon.ico':
            self.send_response(204)
            self._write_cors_headers()
            self.end_headers()
            return
        if self.path == '/health':
            self._write_bytes(b'ok', 'text/plain; charset=utf-8')
            return
        self.send_error(404, 'Not Found')

    def do_POST(self) -> None:  # noqa: N802 (HTTP verb name)
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            self._write_json({"error": "empty request body"}, status=400)
            return

        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._write_json({"error": f"invalid JSON: {exc}"}, status=400)
            return

        if not isinstance(payload, dict):
            self._write_json({"error": "JSON payload must be an object"}, status=400)
            return

        if self.path == "/run":
            self._handle_run(payload)
            return
        if self.path == "/upload":
            self._handle_upload(payload)
            return

        self.send_error(404, "Not Found")

    def _handle_run(self, payload: dict[str, Any]) -> None:
        argv = payload.get("argv")
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            self._write_json({"error": "argv must be a list of strings"}, status=400)
            return

        cwd = payload.get("cwd")
        server_root: Path = getattr(self.server, "root", _repo_root())  # type: ignore[attr-defined]
        if cwd:
            cwd_path = Path(cwd)
            if not cwd_path.is_absolute():
                cwd_path = (server_root / cwd_path).resolve()
        else:
            cwd_path = server_root

        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd_path),
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            self._write_json({"error": str(exc)}, status=500)
            return
        except Exception as exc:  # pragma: no cover - best effort logging
            self._write_json({"error": f"unexpected error: {exc}"}, status=500)
            return

        self._write_json(
            {
                "argv": argv,
                "cwd": str(cwd_path),
                "exit_code": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
            }
        )

    def _handle_upload(self, payload: dict[str, Any]) -> None:
        folder = payload.get("folder")
        files = payload.get("files")
        if not isinstance(folder, str) or not folder.strip():
            self._write_json({"error": "folder must be a non-empty string"}, status=400)
            return
        if not isinstance(files, list) or not files:
            self._write_json({"error": "files must be a non-empty list"}, status=400)
            return

        server_root: Path = getattr(self.server, "root", _repo_root())  # type: ignore[attr-defined]
        cache_root: Path = getattr(self.server, "cache_root", server_root / CACHE_DIRNAME)  # type: ignore[attr-defined]
        cache_root.mkdir(parents=True, exist_ok=True)

        slug = _slugify(folder)
        target = cache_root / f"{slug}-{uuid.uuid4().hex[:8]}"
        try:
            target.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            self._write_json({"error": "unable to create cache directory"}, status=500)
            return

        written = 0
        try:
            for entry in files:
                if not isinstance(entry, dict):
                    raise ValueError("each file entry must be an object")
                rel_path = entry.get("path")
                data = entry.get("data")
                encoding = entry.get("encoding", "base64")
                if not isinstance(rel_path, str) or not isinstance(data, str):
                    raise ValueError("file entries require string path and data")
                rel = _ensure_relative_path(rel_path)
                dest = target / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if encoding == "base64":
                    try:
                        content = base64.b64decode(data)
                    except (ValueError, binascii.Error) as exc:
                        raise ValueError(f"invalid base64 data for {rel}") from exc
                elif encoding == "text":
                    content = data.encode("utf-8")
                else:
                    raise ValueError("unsupported encoding")
                dest.write_bytes(content)
                written += 1
        except Exception as exc:
            _safe_rmtree(target)
            self._write_json({"error": str(exc)}, status=400)
            return

        if written == 0:
            _safe_rmtree(target)
            self._write_json({"error": "no files were cached"}, status=400)
            return

        relative = target.relative_to(server_root)
        self._write_json(
            {
                "status": "ok",
                "cache_dir": str(relative),
                "display_name": folder,
                "file_count": written,
            },
            status=201,
        )

    # Helpers -----------------------------------------------------------------
    def _write_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _write_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self._write_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_file(self, path: Path) -> None:
        data = path.read_bytes()
        content_type, _ = mimetypes.guess_type(str(path))
        if not content_type:
            content_type = "application/octet-stream"
        if content_type.startswith("text/") and "charset=" not in content_type:
            content_type = f"{content_type}; charset=utf-8"
        self._write_bytes(data, content_type)

    def _write_json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self._write_bytes(body, "application/json; charset=utf-8", status=status)

    def log_message(self, fmt: str, *args: Any) -> None:  # type: ignore[override]
        sys.stderr.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))


def serve(host: str, port: int, root: Path) -> None:
    server = ThreadingHTTPServer((host, port), CommandRunnerHandler)
    cache_root = (root / CACHE_DIRNAME).resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    server.root = root  # type: ignore[attr-defined]
    server.cache_root = cache_root  # type: ignore[attr-defined]
    server.dashboard = DASHBOARD_HTML  # type: ignore[attr-defined]
    print(f"Serving dashboard runner on http://{host}:{port} (cwd={root})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...", file=sys.stderr)
    finally:
        server.server_close()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run commands for the dashboard via HTTP")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port to bind (default: {DEFAULT_PORT})")
    parser.add_argument("--root", type=Path, default=_repo_root(), help="Working directory for executed commands")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    serve(args.host, args.port, args.root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
