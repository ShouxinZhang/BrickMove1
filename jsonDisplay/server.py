#!/usr/bin/env python3
import json
import os
import subprocess
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# ---- Paths ----
ROOT = Path(__file__).resolve().parent.parent   # Lean project root (JSON & .lean blocks live here)
JSON_PATH = ROOT / 'sfs4_reshape_with_main.json'
BLOCKS_DIR = ROOT / 'sfs4_new_blocks'
SERVE_DIR = Path(__file__).resolve().parent
LOCK = threading.Lock()
TEMP_FILE = ROOT / 'MTS_temp.lean'
ACTIVE_TEMP_INDEX = None  # 1-based index currently associated with temp file

# ---- Lean server process / LSP framing ----
LEAN_PROC = None
LEAN_LOCK = threading.Lock()
LEAN_EVENTS = []     # push-only SSE stream for frontend
LEAN_SEQ = 1
ROOT_URI = None
DOC_VERSIONS = {}    # textDocument version per URI

def _ensure_lean_server():
    """Spawn `lake env lean --server` and start a background reader that de-frames LSP 'Content-Length' packets."""
    global LEAN_PROC
    with LEAN_LOCK:
        if LEAN_PROC and LEAN_PROC.poll() is None:
            return LEAN_PROC
        cmd = ['lake', 'env', 'lean', f'--root={str(ROOT)}', '--server']
        try:
            proc = subprocess.Popen(cmd, cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        except Exception as e:
            print('Failed to start lean server:', e)
            LEAN_PROC = None
            return None
        LEAN_PROC = proc

        def _reader():
            import re
            try:
                while True:
                    # Read LSP headers
                    header = b''
                    while b'\r\n\r\n' not in header:
                        ch = proc.stdout.read(1)
                        if not ch:
                            return
                        header += ch
                    head, rest = header.split(b'\r\n\r\n', 1)
                    m = re.search(br'Content-Length:\s*(\d+)', head, re.I)
                    if not m:
                        continue
                    length = int(m.group(1))
                    body = rest
                    while len(body) < length:
                        chunk = proc.stdout.read(length - len(body))
                        if not chunk:
                            return
                        body += chunk
                    try:
                        msg = json.loads(body.decode('utf-8'))
                    except Exception:
                        msg = {'raw': body.decode('utf-8', 'ignore')}
                    with LEAN_LOCK:
                        LEAN_EVENTS.append(msg)
                        if len(LEAN_EVENTS) > 10000:
                            del LEAN_EVENTS[:5000]
            except Exception:
                return

        threading.Thread(target=_reader, daemon=True).start()
        _lsp_initialize()
        return LEAN_PROC

def _lsp_send(msg: dict) -> bool:
    proc = LEAN_PROC
    if proc is None or proc.stdin is None:
        return False
    try:
        data = json.dumps(msg, ensure_ascii=False).encode('utf-8')
        header = f'Content-Length: {len(data)}\r\n\r\n'.encode('ascii')
        proc.stdin.write(header + data)
        proc.stdin.flush()
        return True
    except Exception:
        return False

def _lsp_initialize():
    global ROOT_URI, LEAN_SEQ
    if ROOT_URI is None:
        ROOT_URI = f"file://{ROOT}"
    init = {
        'jsonrpc':'2.0','id': LEAN_SEQ, 'method':'initialize',
        'params': {
            'processId': None,
            'rootUri': ROOT_URI,
            'capabilities': {
                'textDocument': {
                    'synchronization': {'didSave': True, 'willSave': False},
                    'hover': {'contentFormat': ['markdown','plaintext']},
                    'definition': {},
                    'semanticTokens': {'requests': {'range': True, 'full': True}}
                },
                'workspace': {}
            },
            'clientInfo': {'name':'jsonDisplay-web','version':'1.0'}
        }
    }
    _lsp_send(init)
    LEAN_SEQ += 1
    _lsp_send({'jsonrpc':'2.0','method':'initialized','params':{}})

def _read_json():
    with LOCK:
        return json.loads(JSON_PATH.read_text(encoding='utf-8'))

def _write_json(data):
    txt = json.dumps(data, ensure_ascii=False, indent=2)
    with LOCK:
        JSON_PATH.write_text(txt, encoding='utf-8')

def _write_block(idx: int, code: str):
    path = BLOCKS_DIR / f'Block_{idx:03d}.lean'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code if code.endswith('\n') else code + '\n', encoding='utf-8')
    return path

def _compile_block(path: Path, timeout: int = 30):
    cmd = ['lake', 'env', 'lean', f'--root={str(ROOT)}', str(path)]
    try:
        res = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout)
        return {'returncode': res.returncode, 'success': res.returncode == 0, 'stdout': res.stdout, 'stderr': res.stderr, 'command': ' '.join(cmd)}
    except subprocess.TimeoutExpired:
        return {'returncode': -1, 'success': False, 'stdout': '', 'stderr': f'timeout after {timeout}s', 'command': ' '.join(cmd)}

class Handler(SimpleHTTPRequestHandler):
    def _set_headers(self, code=200, content_type='application/json; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.end_headers()

    def do_OPTIONS(self): self._set_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/data':
            if not JSON_PATH.exists():
                self._set_headers(404); self.wfile.write(b'{"error":"json not found"}'); return
            self._set_headers(200); self.wfile.write(json.dumps(_read_json()).encode('utf-8')); return

        if parsed.path == '/lean_events':
            # SSE stream for LSP packets
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream')
            self.send_header('Cache-Control','no-store, no-cache, must-revalidate, max-age=0')
            self.send_header('Connection','keep-alive')
            self.send_header('Access-Control-Allow-Origin','*')
            self.end_headers()
            proc = _ensure_lean_server()
            if proc is None:
                try: self.wfile.write(b'data: {"error":"lean server not available"}\n\n'); self.wfile.flush()
                except Exception: pass
                return
            last_idx = 0
            try:
                while True:
                    with LEAN_LOCK:
                        batch = LEAN_EVENTS[last_idx:] if last_idx < len(LEAN_EVENTS) else []
                        last_idx = len(LEAN_EVENTS)
                    for evt in batch:
                        try:
                            self.wfile.write(f'data: {json.dumps(evt)}\n\n'.encode('utf-8')); self.wfile.flush()
                        except Exception:
                            return
                    time.sleep(0.05)
            except Exception: return

        if parsed.path == '/temp_events':
            # Stream temp-file snapshots to web for VSCode <-> web sync
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream')
            self.send_header('Cache-Control','no-store, no-cache, must-revalidate, max-age=0')
            self.send_header('Connection','keep-alive')
            self.send_header('Access-Control-Allow-Origin','*')
            self.end_headers()
            try:
                last_mtime = None
                init = {'exists': TEMP_FILE.exists(), 'index': ACTIVE_TEMP_INDEX}
                if TEMP_FILE.exists():
                    init['mtime'] = TEMP_FILE.stat().st_mtime
                    init['code'] = TEMP_FILE.read_text(encoding='utf-8')
                self.wfile.write(f'data: {json.dumps(init)}\n\n'.encode('utf-8')); self.wfile.flush()
                while True:
                    if TEMP_FILE.exists():
                        st = TEMP_FILE.stat()
                        if last_mtime is None or st.st_mtime != last_mtime:
                            last_mtime = st.st_mtime
                            payload = {'exists': True, 'index': ACTIVE_TEMP_INDEX, 'mtime': last_mtime,
                                       'code': TEMP_FILE.read_text(encoding='utf-8')}
                            self.wfile.write(f'data: {json.dumps(payload)}\n\n'.encode('utf-8')); self.wfile.flush()
                    else:
                        if last_mtime is not None:
                            last_mtime = None
                            self.wfile.write(b'data: {"exists": false}\n\n'); self.wfile.flush()
                    time.sleep(0.05)
            except Exception: return

        if parsed.path == '/temp_read':
            exists = TEMP_FILE.exists(); idx = ACTIVE_TEMP_INDEX
            if not exists:
                self._set_headers(200); self.wfile.write(json.dumps({'exists': False, 'index': idx}).encode('utf-8')); return
            try: txt = TEMP_FILE.read_text(encoding='utf-8')
            except Exception as e: self._set_headers(500); self.wfile.write(json.dumps({'error': str(e)}).encode('utf-8')); return
            st = TEMP_FILE.stat()
            out = {'exists': True, 'index': idx, 'mtime': st.st_mtime, 'path': str(TEMP_FILE), 'code': txt}
            self._set_headers(200); self.wfile.write(json.dumps(out).encode('utf-8')); return

        if parsed.path.startswith('/read_file'):
            # Read a file by URI/path, restricted to ROOT
            length = int(self.headers.get('Content-Length','0') or '0')
            raw = self.rfile.read(length) if length>0 else b''
            try: payload = json.loads(raw.decode('utf-8')) if raw else {}
            except Exception: payload = {}
            uri = payload.get('uri') or ''
            path_str = payload.get('path') or ''
            if uri.startswith('file://'): path_str = uri[len('file://'):]
            if not path_str:
                self._set_headers(400); self.wfile.write(b'{"ok":false,"error":"missing uri/path"}'); return
            p = Path(path_str).resolve()
            if not str(p).startswith(str(ROOT.resolve())) or not p.is_file():
                self._set_headers(403); self.wfile.write(b'{"ok":false,"error":"forbidden or not a file"}'); return
            try: txt = p.read_text(encoding='utf-8')
            except Exception as e:
                self._set_headers(500); self.wfile.write(json.dumps({'ok': False, 'error': str(e)}).encode('utf-8')); return
            self._set_headers(200); self.wfile.write(json.dumps({'ok': True, 'uri': f'file://{p}', 'path': str(p), 'code': txt}).encode('utf-8')); return

        # Static file serving
        rel = parsed.path.lstrip('/') or 'index.html'
        target = (SERVE_DIR / rel).resolve()
        if not str(target).startswith(str(SERVE_DIR)):
            self._set_headers(403); self.wfile.write(b'{"error":"forbidden"}'); return
        if target.exists() and target.is_file():
            ctype = 'application/octet-stream'
            if target.suffix == '.html': ctype = 'text/html; charset=utf-8'
            elif target.suffix == '.css': ctype = 'text/css; charset=utf-8'
            elif target.suffix == '.js': ctype = 'application/javascript; charset=utf-8'
            self.send_response(200); self.send_header('Content-Type', ctype)
            self.send_header('Cache-Control','no-store, no-cache, must-revalidate, max-age=0'); self.end_headers()
            self.wfile.write(target.read_bytes()); return
        self._set_headers(404); self.wfile.write(b'{"error":"not found"}')

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get('Content-Length','0') or '0')
        raw = self.rfile.read(length) if length>0 else b''
        try: payload = json.loads(raw.decode('utf-8')) if raw else {}
        except Exception: payload = {}

        if parsed.path == '/update':
            idx = int(payload.get('index') or 0); code = payload.get('code') or ''
            if idx <= 0 or code == '':
                self._set_headers(400); self.wfile.write(b'{"ok":false,"error":"invalid index/code"}'); return
            data = _read_json()
            if idx > len(data) or not isinstance(data[idx-1], dict):
                self._set_headers(404); self.wfile.write(b'{"ok":false,"error":"index out of range"}'); return
            data[idx-1]['main theorem statement'] = code; _write_json(data)
            _write_block(idx, code)
            self._set_headers(200); self.wfile.write(b'{"ok":true}'); return

        if parsed.path == '/compile':
            idx = int(payload.get('index') or 0); code = payload.get('code') or ''
            if idx <= 0 or code == '':
                self._set_headers(400); self.wfile.write(b'{"ok":false,"error":"invalid index/code"}'); return
            path = _write_block(idx, code)
            out = _compile_block(path); out['ok'] = True
            self._set_headers(200); self.wfile.write(json.dumps(out).encode('utf-8')); return

        if parsed.path == '/prepare_temp':
            global ACTIVE_TEMP_INDEX
            idx = int(payload.get('index') or 0); code = payload.get('code') or ''; open_code = bool(payload.get('open_vscode') or False)
            if idx <= 0:
                self._set_headers(400); self.wfile.write(b'{"ok":false,"error":"invalid index"}'); return
            try:
                TEMP_FILE.write_text(code if code.endswith('\n') else code + '\n', encoding='utf-8')
                ACTIVE_TEMP_INDEX = idx
                if open_code:
                    for cmd in [['code','-r',str(TEMP_FILE)], ['code-insiders','-r',str(TEMP_FILE)], ['codium','-r',str(TEMP_FILE)], ['xdg-open',str(TEMP_FILE)]]:
                        try: subprocess.Popen(cmd); break
                        except Exception: continue
                self._set_headers(200); self.wfile.write(json.dumps({'ok': True, 'index': idx, 'path': str(TEMP_FILE)}).encode('utf-8'))
            except Exception as e:
                self._set_headers(500); self.wfile.write(json.dumps({'ok': False, 'error': str(e)}).encode('utf-8'))
            return

        if parsed.path == '/lean_rpc':
            msg = payload if isinstance(payload, dict) else {}
            if not msg: self._set_headers(400); self.wfile.write(b'{"ok":false,"error":"invalid payload"}'); return
            proc = _ensure_lean_server()
            if proc is None: self._set_headers(503); self.wfile.write(b'{"ok":false,"error":"lean server unavailable"}'); return
            if 'jsonrpc' not in msg: msg['jsonrpc'] = '2.0'
            ok = _lsp_send(msg)
            self._set_headers(200 if ok else 503); self.wfile.write(json.dumps({'ok': ok}).encode('utf-8')); return

        if parsed.path == '/sync_lean':
            idx = int(payload.get('index') or 0); code = payload.get('code') or ''
            if idx <= 0:
                self._set_headers(400); self.wfile.write(b'{"ok":false,"error":"invalid index"}'); return
            path = _write_block(idx, code)
            uri = f'file://{path}'
            _ensure_lean_server()
            ver = DOC_VERSIONS.get(uri)
            if ver is None:
                DOC_VERSIONS[uri] = 1
                ok = _lsp_send({'jsonrpc':'2.0','method':'textDocument/didOpen','params': {'textDocument': {'uri': uri,'languageId':'lean4','version':1,'text': code}}})
            else:
                ver += 1; DOC_VERSIONS[uri] = ver
                ok = _lsp_send({'jsonrpc':'2.0','method':'textDocument/didChange','params': {'textDocument': {'uri': uri,'version': ver}, 'contentChanges': [{'text': code}]}})
            self._set_headers(200 if ok else 503); self.wfile.write(json.dumps({'ok': ok, 'path': str(path), 'uri': uri, 'version': DOC_VERSIONS[uri]}).encode('utf-8')); return

        self._set_headers(404); self.wfile.write(b'{"error":"unknown endpoint"}')

def main():
    os.chdir(str(SERVE_DIR))
    host, port = '127.0.0.1', 8000
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving jsonDisplay on http://{host}:{port}/")
    print("API: GET /data, POST /update, POST /compile, POST /sync_lean, POST /lean_rpc, POST /read_file")
    httpd.serve_forever()

if __name__ == '__main__':
    main()
