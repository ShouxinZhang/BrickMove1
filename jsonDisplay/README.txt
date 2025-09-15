jsonDisplay_compat2 — Lean4 web UI (LSP兼容fallback)

- 支持 /lean_rpc_sync（优先）与 /lean_rpc（SSE回传）双通道，自动fallback，避免 404。
- 右侧 InfoView：plainGoal → plainTermGoal → hover 三级回退；解析 “Try this: …”。
- 行号开启、自动换行；TextMate 语法可从 ./grammars/lean4.json 加载。

运行：
  python3 server.py
  打开 http://127.0.0.1:8000/

若 LSP 仍离线：
  1) 确认已运行此包内的 server.py（旧 server 没有 /lean_rpc_sync 或 /lean_events）
  2) 终端看是否能启动：`lake env lean --server`
  3) 浏览器网络面板检查 /lean_rpc_sync 或 /lean_events 是否 404
