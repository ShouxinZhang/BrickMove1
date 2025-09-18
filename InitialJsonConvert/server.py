#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
InitialJsonConvert local server

Endpoints:
- GET  /                  -> serve convert_initial_json_web.html
- POST /convert           -> accept a JSON file upload and run the converter
- POST /convert-break     -> split JSON by difficulty and export results

Run:
  python3 InitialJsonConvert/server.py
  open http://127.0.0.1:8010/
"""
import json
import os
import shutil
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import cgi
from datetime import datetime
import sys

# Ensure repo root is on sys.path so we can import sibling packages like LeanCheck
BASE = Path(__file__).resolve().parent
REPO_ROOT = BASE.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lean_check_runner import run_leancheck
PUBLIC_HTML = BASE / 'convert_initial_json_web.html'
OUTPUT_ROOT = BASE / 'output'
UPLOADS = BASE / 'uploads'
UPLOADS.mkdir(parents=True, exist_ok=True)

def timestamp_tag() -> str:
    return datetime.now().strftime('%Y%m%d_%H%M%S')


class Handler(SimpleHTTPRequestHandler):
    def _set_headers(self, code=200, content_type='application/json; charset=utf-8'):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/' or parsed.path == '/index.html':
            if PUBLIC_HTML.exists():
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(PUBLIC_HTML.read_bytes())
                return
            self._set_headers(404)
            self.wfile.write(b'No UI found')
            return

        # Fallback to 404
        self._set_headers(404)
        self.wfile.write(b'{"error":"not found"}')

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path not in {'/convert', '/convert-break'}:
            self._set_headers(404)
            self.wfile.write(b'{"error":"unknown endpoint"}')
            return
        is_break = parsed.path == '/convert-break'

        # Parse multipart/form-data
        length = int(self.headers.get('Content-Length', '0') or '0')
        env = {
            'REQUEST_METHOD': 'POST',
            'CONTENT_TYPE': self.headers.get('Content-Type', ''),
            'CONTENT_LENGTH': str(length),
        }
        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ=env)
        fld = form['file'] if 'file' in form else None
        leancheck_flag = form.getvalue('leancheck', '')
        run_lean_check = str(leancheck_flag).lower() not in {'', '0', 'false', 'off', 'no', 'none'}
        if fld is None or not getattr(fld, 'file', None):
            self._set_headers(400)
            self.wfile.write(b'{"ok":false,"error":"no file uploaded"}')
            return

        # Save upload
        ts = timestamp_tag()
        up_dir = UPLOADS / f'upload_{ts}'
        up_dir.mkdir(parents=True, exist_ok=True)
        fname = getattr(fld, 'filename', '') or 'input.json'
        fname = fname.replace('\\', '/').split('/')[-1]
        if not fname.lower().endswith('.json'):
            fname += '.json'
        up_path = up_dir / fname
        with open(up_path, 'wb') as f:
            shutil.copyfileobj(fld.file, f)

        # Run converter
        try:
            if is_break:
                from different_by_difficulty import split_and_export  # type: ignore
            else:
                from convert_initial_json import convert  # type: ignore
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({'ok': False, 'error': f'import error: {e}'}).encode('utf-8'))
            return

        try:
            if is_break:
                result, metadata = split_and_export(up_path, OUTPUT_ROOT)
                payload = {'ok': True}
                payload.update({k: v for k, v in result.items() if k != 'session_name'})
                payload['session_name'] = result.get('session_name')
                leancheck_meta = {
                    'session_name': result.get('session_name'),
                    'base_dir': metadata['base_dir'],
                    'groups': [
                        {
                            'label': g['label'],
                            'lean_dir': g['lean_dir'],
                            'mapping': g['mapping'],
                        }
                        for g in metadata['groups']
                    ],
                }
            else:
                conversion = convert(up_path, OUTPUT_ROOT)
                payload = {
                    'ok': True,
                    'json_output': str(conversion['json_output']),
                    'lean_dir': str(conversion['lean_dir']),
                }
                payload['session_name'] = conversion.get('session_name')
                leancheck_meta = {
                    'session_name': conversion.get('session_name', Path(conversion['lean_dir']).name),
                    'base_dir': Path(conversion['lean_dir']),
                    'groups': [
                        {
                            'label': 'default',
                            'lean_dir': conversion['lean_dir'],
                            'mapping': conversion['mapping'],
                        }
                    ],
                }

            if run_lean_check:
                try:
                    summary = run_leancheck(
                        groups=leancheck_meta['groups'],
                        session_name=leancheck_meta.get(
                            'session_name', f"check_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                        ),
                        base_dir=leancheck_meta.get('base_dir', OUTPUT_ROOT / 'lean_check'),
                    )
                except Exception as exc:
                    summary = {'enabled': False, 'reason': str(exc)}
                payload['leancheck'] = summary
            else:
                payload['leancheck'] = {'enabled': False}

            # Clean mapping references from payload before sending
            if is_break:
                for group in metadata['groups']:
                    group.pop('mapping', None)
            self._set_headers(200)
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
        except SystemExit as e:
            # convert() may raise SystemExit on validation error
            self._set_headers(400)
            self.wfile.write(json.dumps({'ok': False, 'error': str(e)}).encode('utf-8'))
        except Exception as e:
            self._set_headers(500)
            self.wfile.write(json.dumps({'ok': False, 'error': str(e)}).encode('utf-8'))


def main():
    host, port = '127.0.0.1', 8010
    with ThreadingHTTPServer((host, port), Handler) as httpd:
        print(f"Serving InitialJsonConvert UI on http://{host}:{port}/")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
