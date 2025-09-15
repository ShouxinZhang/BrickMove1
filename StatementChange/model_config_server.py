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

BASE = Path(__file__).parent
PUBLIC = BASE / 'html'
CFG = BASE / 'model_config.json'
ROOT = BASE.parent
UPLOADS = BASE / 'uploads'
DOWNLOADS = BASE / 'download'
UPLOADS.mkdir(parents=True, exist_ok=True)

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
        return super().do_POST()

if __name__ == '__main__':
    port=int(os.environ.get('PORT','8001'))
    with http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler) as httpd:
        print(f'Serving on http://127.0.0.1:{port}')
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
