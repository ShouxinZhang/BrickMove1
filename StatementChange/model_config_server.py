#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import http.server
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List
import cgi
from urllib.parse import urlparse
import sys
import threading
from typing import Optional, Dict, Any

BASE = Path(__file__).parent
PUBLIC = BASE / 'html'
CFG = BASE / 'model_config.json'
ROOT = BASE.parent
UPLOADS = BASE / 'uploads'
DOWNLOADS = BASE / 'downloads'
UPLOADS.mkdir(parents=True, exist_ok=True)

# Ensure we can import modules from repo root (e.g., LeanCheck)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Global state for last output dir and build progress
LAST_OUTPUT_DIR: Optional[Path] = None
_build_lock = threading.Lock()
BUILD_STATUS: Dict[str, Any] = {
    'state': 'idle',  # idle | running | done | error
    'total': 0,
    'completed': 0,
    'successes': 0,
    'failures': [],
    'logs': [],
    'summary': None,
}
_build_thread: Optional[threading.Thread] = None

def _reset_build_status():
    with _build_lock:
        BUILD_STATUS.update({
            'state': 'idle',
            'total': 0,
            'completed': 0,
            'successes': 0,
            'failures': [],
            'logs': [],
            'summary': None,
        })

def _append_log(msg: str):
    with _build_lock:
        logs = BUILD_STATUS.get('logs', [])
        logs.append(msg)
        # cap logs to last 500 lines
        if len(logs) > 500:
            del logs[:len(logs)-500]
        BUILD_STATUS['logs'] = logs

def _start_build(blocks_dir: Path, workers: int = 100):
    from LeanCheck.parallel_build_checker import run_parallel_build_check

    def progress_cb(evt: Dict[str, Any]):
        typ = evt.get('phase')
        if typ == 'init':
            with _build_lock:
                BUILD_STATUS['state'] = 'running'
                BUILD_STATUS['total'] = int(evt.get('total', 0))
                BUILD_STATUS['completed'] = 0
                BUILD_STATUS['successes'] = 0
                BUILD_STATUS['failures'] = []
            _append_log(f"Start building {BUILD_STATUS['total']} files with {workers} workers")
        elif typ == 'file':
            block_id = evt.get('block_id', '')
            success = bool(evt.get('success', False))
            with _build_lock:
                BUILD_STATUS['completed'] += 1
                if success:
                    BUILD_STATUS['successes'] += 1
                else:
                    fails = BUILD_STATUS.get('failures', [])
                    fails.append(block_id)
                    BUILD_STATUS['failures'] = fails
            _append_log(("✓ " if success else "✗ ") + str(block_id))
        elif typ == 'done':
            with _build_lock:
                BUILD_STATUS['state'] = 'done'
                BUILD_STATUS['summary'] = evt.get('summary')
            _append_log('Build finished')

    def worker():
        try:
            _reset_build_status()
            # Execute build check
            run_parallel_build_check(str(blocks_dir), str(ROOT / 'build_check_logs'), None, workers, progress_cb=progress_cb)
        except Exception as e:
            with _build_lock:
                BUILD_STATUS['state'] = 'error'
            _append_log(f'Build error: {e}')

    global _build_thread
    _build_thread = threading.Thread(target=worker, daemon=True)
    _build_thread.start()

BLOCK_COMMENT_RE = re.compile(r"/-[\s\S]*?-/", re.MULTILINE)
LINE_COMMENT_RE = re.compile(r"(^|\s)--.*?$", re.MULTILINE)

def strip_lean_comments(src: str) -> str:
    no_block = re.sub(BLOCK_COMMENT_RE, "", src)
    no_line = re.sub(LINE_COMMENT_RE, "", no_block)
    return no_line

def timestamp_tag() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def safe_name(name: str) -> str:
    name = name.replace("\\", "/")
    name = name.split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name or "file.lean"

def get_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    key_file = BASE.parent / ".openrouter_key"
    if key_file.exists():
        try:
            for line in key_file.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s or s.startswith('#'):
                    continue
                return s
        except Exception:
            pass
    return ""

class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        parsed = urlparse(path)
        pure_path = parsed.path or '/'
        # Serve static from html folder
        if pure_path == '/':
            return str(PUBLIC / 'index.html')
        if pure_path.startswith('/load-config') or pure_path.startswith('/save-config') or pure_path.startswith('/process'):
            return pure_path
        p = Path(pure_path.lstrip('/'))
        return str((PUBLIC / p).resolve())

    def do_GET(self):
        if self.path.startswith('/load-config'):
            self.send_response(200)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.end_headers()
            if CFG.exists():
                try:
                    data=json.loads(CFG.read_text(encoding='utf-8'))
                except Exception:
                    data={}
            else:
                data={}
            self.wfile.write(json.dumps(data).encode('utf-8'))
            return
        if self.path.startswith('/build-status'):
            with _build_lock:
                payload = dict(BUILD_STATUS)
            self.send_response(200)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
            return
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith('/process'):
            length = int(self.headers.get('Content-Length','0') or '0')
            env = {
                'REQUEST_METHOD': 'POST',
                'CONTENT_TYPE': self.headers.get('Content-Type',''),
                'CONTENT_LENGTH': str(length),
            }
            form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ=env)

            mode = 'local'
            if 'mode' in form and getattr(form['mode'], 'value', None):
                mode = form['mode'].value.strip().lower()
            items: List[cgi.FieldStorage] = []  # type: ignore[name-defined]
            if 'files' in form:
                fld = form['files']
                if isinstance(fld, list):
                    items = fld
                else:
                    items = [fld]
            if not items:
                self.send_response(400); self.end_headers(); self.wfile.write(b'{"error":"no files"}')
                return

            ts = timestamp_tag()
            upload_dir = UPLOADS / f'upload_{ts}'
            upload_dir.mkdir(parents=True, exist_ok=True)

            saved = []
            for it in items:
                fn = safe_name(getattr(it, 'filename', '') or 'file.lean')
                if not fn.lower().endswith('.lean'):
                    continue
                dest = upload_dir / fn
                with open(dest, 'wb') as f:
                    shutil.copyfileobj(it.file, f)
                saved.append(dest)

            if not saved:
                self.send_response(400); self.end_headers(); self.wfile.write(b'{"error":"no .lean files"}')
                return

            DOWNLOADS.mkdir(parents=True, exist_ok=True)
            out_dir = DOWNLOADS / f'newBlocks{ts}'
            out_dir.mkdir(parents=True, exist_ok=True)

            logs: List[str] = []
            if mode == 'llm':
                api_key = get_api_key()
                if not api_key:
                    self.send_response(400)
                    self.send_header('Content-Type','application/json; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'ok': False,
                        'error': 'LLM 处理需要 OPENROUTER_API_KEY（或 .openrouter_key）',
                        'logs': [
                            '缺少 OPENROUTER_API_KEY 环境变量或 .openrouter_key 文件',
                            '请导出密钥：export OPENROUTER_API_KEY=sk-xxxxx',
                            '或在仓库根目录创建 .openrouter_key 并填入密钥',
                            '若提示缺少依赖，请运行：pip install -r StatementChange/requirements.txt'
                        ],
                    }).encode('utf-8'))
                    return
                script = str(BASE / 'LLM_InformalStatement_Remove.py')
                cmd = [
                    sys.executable, script,
                    '--dir', str(upload_dir),
                    '--outdir', str(out_dir)
                ]
                proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
                logs.append(' '.join(cmd))
                if proc.stdout:
                    logs.extend([ln for ln in proc.stdout.splitlines() if ln.strip()])
                if proc.returncode != 0:
                    if proc.stderr:
                        logs.extend([ln for ln in proc.stderr.splitlines() if ln.strip()])
                    self.send_response(500)
                    self.send_header('Content-Type','application/json; charset=utf-8')
                    self.end_headers()
                    self.wfile.write(json.dumps({'ok': False, 'error': 'LLM 处理失败', 'logs': logs}).encode('utf-8'))
                    return
            else:
                for p in saved:
                    try:
                        txt = p.read_text(encoding='utf-8')
                    except Exception:
                        txt = p.read_bytes().decode('utf-8', 'ignore')
                    new_code = strip_lean_comments(txt)
                    (out_dir / p.name).write_text(new_code, encoding='utf-8')
                logs.append(f'本地处理完成，共 {len(saved)} 个文件')

            # remember last output dir for downstream build
            global LAST_OUTPUT_DIR
            LAST_OUTPUT_DIR = out_dir

            self.send_response(200)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': True, 'outputDir': str(out_dir), 'logs': logs}).encode('utf-8'))
            return

        if self.path.startswith('/save-config'):
            l=int(self.headers.get('Content-Length','0'))
            raw=self.rfile.read(l) if l>0 else b'{}'
            try:
                data=json.loads(raw.decode('utf-8'))
                if not isinstance(data, dict):
                    raise ValueError('invalid')
            except Exception:
                self.send_response(400)
                self.end_headers()
                return
            # keep only relevant keys, with extended fields
            allowed_keys = (
                'model', 'temperature', 'top_p',
                'max_concurrency', 'max_input_chars', 'max_tokens',
                'system_prompt', 'history'
            )
            out={}
            for k in allowed_keys:
                if k in data:
                    out[k]=data[k]
            # normalize types for numeric fields if possible
            for k in ('temperature','top_p'):
                if k in out:
                    try:
                        out[k]=float(out[k])
                    except Exception:
                        pass
            for k in ('max_concurrency','max_input_chars','max_tokens'):
                if k in out:
                    try:
                        out[k]=int(out[k])
                    except Exception:
                        pass
            CFG.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
            self.send_response(200)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return
        if self.path.startswith('/build-start'):
            l=int(self.headers.get('Content-Length','0'))
            raw=self.rfile.read(l) if l>0 else b'{}'
            try:
                data=json.loads(raw.decode('utf-8')) if raw else {}
            except Exception:
                data={}
            workers = int(data.get('workers', 100) or 100)
            dir_arg = data.get('dir') or data.get('blocks_dir')
            blocks_dir = None
            if dir_arg:
                blocks_dir = Path(dir_arg)
            else:
                blocks_dir = LAST_OUTPUT_DIR
            if not blocks_dir or not Path(blocks_dir).exists():
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({'ok': False, 'error': 'blocks_dir 不存在'}).encode('utf-8'))
                return
            with _build_lock:
                if BUILD_STATUS.get('state') == 'running':
                    self.send_response(409)
                    self.end_headers()
                    self.wfile.write(json.dumps({'ok': False, 'error': '已有构建在运行'}).encode('utf-8'))
                    return
            _start_build(Path(blocks_dir), workers=workers)
            self.send_response(200)
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': True}).encode('utf-8'))
            return
        return super().do_POST()

if __name__ == '__main__':
    port=int(os.environ.get('PORT','8001'))
    with http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler) as httpd:
        print(f'Serving on http://127.0.0.1:{port}')
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
