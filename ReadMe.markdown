# BrickMove — Lean4 Blocks ↔ JSON Pipeline

This repo helps you turn a JSON dataset of Lean items into single-file “main theorem statement” blocks, iterate on those blocks (compile, inspect, tweak), and export the results back to JSON — plus optional LLM-based assistance to generate/repair minimal main-theorem skeletons.


## What’s Inside

- JsonExport: CLI tools to convert between JSON and Lean blocks
	- `export_main_statements.py`: JSON → `sfs4_new_blocks/Block_XXX.lean`
	- `export_from_lean_dir.py`: Lean blocks → JSON (fresh export or backfill a template)
	- `insert_main_statements.py`: Backfill “main theorem statement” from `formalProof_*.lean` files
- jsonDisplay: Lightweight web UI to review/edit “main theorem statement” strings
	- `server.py` serves the UI, syncs edits to JSON and `sfs4_new_blocks/`, and talks to Lean LSP
- LeanCheck: Parallel build checker for Lean blocks using `lake env lean`
- LLM_Agent: Tools to ask an LLM to produce/repair main-theorem skeletons
	- `llm_agent.py`: transform blocks into “main theorem statement” files
	- `llm_recheck_agent.py`: rebuild, regenerate failures with LLM, rebuild again
- StatementChange: Simple server UI for model-config and batch processing helpers
- LeanJson: Input/output JSON files (ignored by Git)
- sfs4_new_blocks: Generated Lean files `Block_001.lean`, … (ignored by Git)


## Prerequisites

- Lean 4 + Lake installed and on PATH.
- Mathlib via Lake cache/build (first build will fetch/build).
- Python 3.10+.
- Optional for LLM flows: an OpenRouter API key in the env `OPENROUTER_API_KEY` or a file `.openrouter_key` at repo root containing the key on the first non-empty line.
- Optional for StatementChange UI: `pip install -r StatementChange/requirements.txt` (aiohttp, tqdm).


## Quick Start

1) Prepare Lean deps (recommended once):

```bash
lake exe cache get
lake build
```

2) Export “main theorem statement” blocks from your JSON:

```bash
python3 JsonExport/export_main_statements.py \
	--input LeanJson/sfs4_reshape_with_main.json \
	--outdir sfs4_new_blocks --overwrite
```

3) Build-check the blocks in parallel:

```bash
python3 LeanCheck/parallel_build_checker.py \
	--blocks-dir sfs4_new_blocks --workers 8
```

4) Interactive web UI for reviewing/editing:

```bash
python3 jsonDisplay/server.py
# open http://127.0.0.1:8000
```

- Load a JSON (uses `LeanJson/sfs4_reshape_with_main.json` by default if present).
- Edit the right-hand “main theorem statement”, compile, inspect diagnostics, and export the updated JSON.

5) Export Lean → JSON (fresh or backfill template):

- Fresh export:
```bash
python3 JsonExport/export_from_lean_dir.py \
	--indir sfs4_new_blocks \
	--output LeanJson/sfs4_from_lean.json --overwrite
```

- Backfill template (preserve original records while replacing the field):
```bash
python3 JsonExport/export_from_lean_dir.py \
	--indir sfs4_new_blocks \
	--template LeanJson/sfs4_reshape_with_main.json \
	--output LeanJson/sfs4_reshape_with_main.updated.json \
	--start 1 --overwrite
```


## VS Code Tasks

If you’re using this repo in VS Code, the following tasks are available:

- Export main theorem statements: `python3 JsonExport/export_main_statements.py --input sfs4_reshape_with_main.json --outdir sfs4_new_blocks --overwrite`
- Lean→JSON: fresh export: `python3 JsonExport/export_from_lean_dir.py --indir sfs4_new_blocks --output sfs4_from_lean.json --overwrite`
- Lean→JSON: backfill template: `python3 JsonExport/export_from_lean_dir.py --indir sfs4_new_blocks --template sfs4_reshape_with_main.json --output sfs4_reshape_with_main.updated.json --start 1 --overwrite`
- Start model config server (venv): `. .venv/bin/activate && python StatementChange/model_config_server.py` (serves at http://127.0.0.1:8001)

Use Terminal → Run Task… to execute any of the above.


## JSON ↔ Lean Workflows

- JSON → Lean blocks (main theorem statements only):
	- `export_main_statements.py --input <json> --outdir sfs4_new_blocks [--start N] [--overwrite]`
	- Creates `Block_001.lean`, `Block_002.lean`, … from the JSON field `"main theorem statement"`.

- Lean blocks → JSON (two modes):
	- Fresh: `export_from_lean_dir.py --indir sfs4_new_blocks --output <out.json>`
	- Backfill: `export_from_lean_dir.py --indir sfs4_new_blocks --template <in.json> --output <out.json> [--start N] [--field "main theorem statement"]`

- From stripped proofs to “main theorem statement” in JSON:
	- `insert_main_statements.py --json <in.json> --src-dir <dir with formalProof_*.lean> [--inplace | --out <out.json>]`


## LLM Assisted Flows (Optional)

The LLM agent can turn an existing Lean file into a single “main theorem statement” skeleton (keeping minimal imports/context) and also attempt to repair non-compiling files.

1) Generate main-theorem skeletons for blocks:

```bash
export OPENROUTER_API_KEY=sk-...   # or write it to .openrouter_key
python3 LLM_Agent/llm_agent.py \
	--input-dir sfs4_new_blocks \
	--output-dir LLM_Agent/output/MTS_run \
	--model moonshotai/kimi-k2-0905 \
	--workers 8 --normalize --continue-on-error
```

2) Rebuild and auto-regenerate failures with LLM, then rebuild again:

```bash
export OPENROUTER_API_KEY=sk-...
python3 LLM_Agent/llm_recheck_agent.py \
	--target-dir LLM_Agent/output/MTS_run \
	--workers 8 --normalize --model openai/gpt-5
```

Tips:
- API key discovery order: `--api-key` CLI → env `OPENROUTER_API_KEY` → `.openrouter_key` file.
- Outputs are written under `LLM_Agent/output/` (git-ignored). Use `LeanCheck/` to verify.


## Build Checking (LeanCheck)

Run parallel builds with logs and a summary:

```bash
python3 LeanCheck/parallel_build_checker.py \
	--blocks-dir sfs4_new_blocks \
	--output-dir build_check_logs \
	--workers 8
```

- Uses `lake env lean --root=<repo>` to elaborate each file.
- Will try `lake exe cache get` + `lake build` first to prepare Mathlib.
- Logs and `build_summary.json` go to `build_check_logs/`.


## Web UI (jsonDisplay)

```bash
python3 jsonDisplay/server.py
# open http://127.0.0.1:8000
```

Features:
- Edit the right panel’s “main theorem statement”, compile it, and see diagnostics.
- Talks to Lean’s LSP (`lake env lean --server`) and streams events to the browser.
- Export the current working JSON via the “Export JSON” button.

Endpoints (for reference):
- `GET /data` (serve current JSON)
- `GET /current_json`, `GET /export_json`
- `POST /import_json` (select working JSON files under `LeanJson/` with timestamped copies)
- `POST /update`, `POST /compile`, `POST /sync_lean`, `POST /lean_rpc`, `POST /read_file`


## StatementChange UI (Optional)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r StatementChange/requirements.txt
python StatementChange/model_config_server.py
# open http://127.0.0.1:8001
```

- Upload `.lean` files and process them locally (comment stripping) or via LLM mode (needs API key).
- Can start a background build on produced blocks; progress and summaries are streamed.


## Notes & Conventions

- Git ignores generated folders: `sfs4_new_blocks/`, `LeanJson/`, `LLM_Agent/output/`, `build_check_logs/`.
- `--start` controls how `Block_XXX.lean` indexes line up with JSON records.
- Field name defaults to `"main theorem statement"` but can be changed with `--field`.
- For CLI scripts, outputs are UTF-8 with a trailing newline for editor friendliness.


## License

See `LICENSE`.
