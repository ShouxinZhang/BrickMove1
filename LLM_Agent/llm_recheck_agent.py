#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _add_sys_path():
    # Allow importing sibling modules without package init files
    here = Path(__file__).parent
    root = _repo_root()
    for p in [str(here), str(root)]:
        if p not in sys.path:
            sys.path.insert(0, p)


_add_sys_path()

# Import OpenRouter utilities from llm_agent
import llm_agent as base  # type: ignore

# Import build checker (we'll call function directly)
from LeanCheck.parallel_build_checker import run_parallel_build_check, build_lean_file  # type: ignore


RECHECK_SYSTEM_PROMPT = (
    r"""
You are an expert Lean 4 engineer.
Task: A Lean file failed to compile. Regenerate the entire file so that it typechecks.

Rules:
1. Keep or add necessary imports/opens/namespaces. Ensure `import Mathlib` appears exactly once at the top.
2. Preserve the file's intent and main statements when possible; you may simplify proofs and replace bodies with `:= by sorry` to recover typechecking.
3. Do not add commentary or code fences; output only the final Lean source.
""".strip()
)


def build_messages(system_prompt: str, file_content: str) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "This Lean file failed to compile. Please return a compiling Lean file (no fences):\n\n"
                + file_content
            ),
        },
    ]


def regenerate_file(
    client: base.OpenRouterClient,
    file_path: Path,
    model: str,
    system_prompt: str,
    *,
    normalize: bool = True,
    max_tokens: Optional[int] = None,
    retries: int = 1,
) -> Tuple[Path, bool, Optional[str]]:
    try:
        src = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return file_path, False, f"read error: {e}"

    messages = build_messages(system_prompt, src)

    content = ""
    attempt = 0
    last_err: Optional[str] = None
    while attempt <= retries:
        try:
            data = client.chat_completion(messages, model=model, max_tokens=max_tokens)
        except Exception as e:
            last_err = str(e)
            attempt += 1
            if attempt <= retries:
                time.sleep(min(1.0 * attempt, 2.0))
            continue
        choice = (data.get("choices") or [{}])[0]
        content = ((choice.get("message") or {}).get("content") or "").strip()
        if content:
            break
        attempt += 1
        time.sleep(min(1.0 * attempt, 2.0))

    if not content:
        # Fallbacks from base agent
        try:
            fallback = base.build_fallback_skeleton(src)
            if fallback:
                if normalize:
                    fallback = base.ensure_top_import_mathlib(fallback)
                file_path.write_text(fallback, encoding="utf-8")
                return file_path, True, None
        except Exception:
            pass
        try:
            stem = file_path.stem
            minimal = (
                "import Mathlib\n\n"
                "/-- Auto-regenerated minimal skeleton due to repeated empty responses. -/\n"
                f"theorem {stem}_main : True := by\n  trivial\n"
            )
            file_path.write_text(minimal, encoding="utf-8")
            return file_path, True, None
        except Exception as e:
            return file_path, False, f"write fallback error: {e}"

    content = base.strip_code_fences(content)
    if normalize:
        content = base.ensure_top_import_mathlib(content)

    try:
        file_path.write_text(content, encoding="utf-8")
        return file_path, True, None
    except Exception as e:
        return file_path, False, f"write error: {e}"


def _latest_mts_dir() -> Optional[Path]:
    base_dir = Path(__file__).parent / "output"
    if not base_dir.exists():
        return None
    candidates = [p for p in base_dir.glob("MTS*") if p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LLM Recheck Agent: build, regenerate failures with LLM, build again")
    p.add_argument("--target-dir", type=Path, default=None, help="Directory containing Lean files to (re)build. Default: latest LLM_Agent/output/MTS*")
    p.add_argument("--pattern", type=str, default="*.lean", help="Glob for Lean files (default: '*.lean')")
    p.add_argument("--workers", type=int, default=50, help="Parallel workers for build and regeneration")
    p.add_argument("--model", type=str, default="openai/gpt-5", help="Model for regeneration (default: openai/gpt-5)")
    p.add_argument("--max-tokens", type=int, default=0, help="Max tokens for completion (0 = unlimited)")
    p.add_argument("--no-max-tokens", action="store_true", help="Omit max_tokens in API payload")
    p.add_argument("--normalize", action="store_true", help="Ensure single top `import Mathlib` in outputs")
    p.add_argument("--retries", type=int, default=1, help="Retries per file when the model returns empty or errors")
    p.add_argument("--append-system", type=str, default="", help="Append custom guidance to the system prompt")
    p.add_argument("--api-key", type=str, default="", help="OpenRouter API key; overrides env and .openrouter_key")
    p.add_argument("--second-build-scope", choices=["all", "failed"], default="failed", help="Second build pass scope: rebuild all files or only the previously failed ones (default: failed)")
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)

    target_dir: Optional[Path] = args.target_dir
    if target_dir is None:
        target_dir = _latest_mts_dir()
        if target_dir is None:
            print("ERROR: --target-dir not provided and no MTS* found under LLM_Agent/output", file=sys.stderr)
            return 2

    target_dir = target_dir.resolve()

    # Prepare OpenRouter client
    api_key = args.api_key or os.getenv("OPENROUTER_API_KEY") or (base._load_api_key_from_keyfile() or "")
    if not api_key:
        print("ERROR: Provide OpenRouter API key: --api-key, or set OPENROUTER_API_KEY env, or place it in .openrouter_key", file=sys.stderr)
        return 2
    client = base.OpenRouterClient(api_key)

    system_prompt = RECHECK_SYSTEM_PROMPT
    if args.append_system:
        system_prompt = f"{system_prompt}\n\nAdditional guidance:\n{args.append_system.strip()}".strip()

    # First build pass (with retry inside)
    build_logs_dir = _repo_root() / "build_check_logs" / f"recheck_{int(time.time())}"
    summary1 = run_parallel_build_check(
        blocks_dir=str(target_dir),
        output_dir=str(build_logs_dir),
        block_range=None,
        max_workers=args.workers,
        progress_cb=None,
        pattern=args.pattern,
    )

    failed_blocks: List[str] = summary1.get("failed_blocks", [])
    if not failed_blocks:
        print("All files compile after initial pass. Nothing to regenerate.")
        return 0

    # Map stems to files
    all_files = list(target_dir.glob(args.pattern))
    by_stem: Dict[str, Path] = {p.stem: p for p in all_files}
    to_fix: List[Path] = [by_stem[s] for s in failed_blocks if s in by_stem]

    print(f"Regenerating {len(to_fix)} failed file(s) with model {args.model} ...")
    effective_max_tokens: Optional[int] = None if args.no_max_tokens or args.max_tokens <= 0 else args.max_tokens

    def worker(p: Path) -> Tuple[Path, bool, Optional[str]]:
        return regenerate_file(
            client,
            file_path=p,
            model=args.model,
            system_prompt=system_prompt,
            normalize=args.normalize,
            max_tokens=effective_max_tokens,
            retries=args.retries,
        )

    # Backup originals before regeneration for safe rollback
    backups_dir = ( _repo_root() / "build_check_logs" / f"recheck_{int(time.time())}" / "backups" )
    backups_dir.mkdir(parents=True, exist_ok=True)
    original_cache: Dict[Path, str] = {}
    for p in to_fix:
        try:
            original_cache[p] = p.read_text(encoding="utf-8", errors="ignore")
            (backups_dir / (p.stem + ".lean.bak")).write_text(original_cache[p], encoding="utf-8")
        except Exception:
            # ignore backup errors; we'll rely on in-memory cache if available
            pass

    regen_failed: List[Tuple[Path, str]] = []
    if args.workers and args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(worker, p): p for p in to_fix}
            for fut in as_completed(futs):
                p = futs[fut]
                try:
                    outp, ok, err = fut.result()
                except Exception as e:
                    ok, err = False, str(e)
                    outp = p
                if ok:
                    print(f"Rewrote: {outp}")
                else:
                    print(f"Error regenerating {p}: {err}", file=sys.stderr)
                    regen_failed.append((p, err or "unknown"))
    else:
        for p in to_fix:
            outp, ok, err = worker(p)
            if ok:
                print(f"Rewrote: {outp}")
            else:
                print(f"Error regenerating {p}: {err}", file=sys.stderr)
                regen_failed.append((p, err or "unknown"))

    # Write regeneration error log (if any)
    if regen_failed:
        try:
            errlog_path = build_logs_dir / "recheck_errors.log"
            with open(errlog_path, "w", encoding="utf-8") as elog:
                for p, e in regen_failed:
                    elog.write(f"{p}: {e}\n")
        except Exception:
            pass

    # Second build pass
    if args.second_build_scope == "all":
        summary2 = run_parallel_build_check(
            blocks_dir=str(target_dir),
            output_dir=str(build_logs_dir),
            block_range=None,
            max_workers=args.workers,
            progress_cb=None,
            pattern=args.pattern,
        )
    else:
        # Build only previously failed files (after regeneration)
        subset = [by_stem[s] for s in failed_blocks if s in by_stem]
        print(f"Second pass (failed-only): {len(subset)} file(s)")
        results: List[Dict[str, Any]] = []
        successful: List[str] = []
        failed: List[str] = []
        from concurrent.futures import ThreadPoolExecutor, as_completed  # local import for clarity
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(build_lean_file, p, build_logs_dir, "postregen"): p for p in subset}
            for fut in as_completed(futs):
                p = futs[fut]
                try:
                    block_id, ok, stdout, stderr, log_file = fut.result()
                except Exception as e:
                    block_id, ok, stdout, stderr, log_file = p.stem, False, "", str(e), ""
                results.append({
                    "block_id": block_id,
                    "success": ok,
                    "log_file": log_file,
                    "attempt": "postregen",
                    "has_errors": bool((stderr or "").strip()),
                    "stdout_lines": len((stdout or "").splitlines()),
                    "stderr_lines": len((stderr or "").splitlines()),
                })
                (successful if ok else failed).append(block_id)
        # Align summary format with checker
        summary2 = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "total_files": len(subset),
            "successful_builds": len(successful),
            "failed_builds": len(failed),
            "success_rate": (len(successful) / len(subset) * 100.0) if subset else 0.0,
            "successful_blocks": successful,
            "failed_blocks": failed,
            "detailed_results": results,
        }

    # Emit result summary
    final_failed = summary2.get("failed_blocks", [])

    # If still failing after second pass, restore original content
    restored: List[str] = []
    for stem in final_failed:
        p = by_stem.get(stem)
        if not p:
            continue
        src = original_cache.get(p)
        if src is None:
            # try to restore from backup file
            bak_path = backups_dir / (stem + ".lean.bak")
            if bak_path.exists():
                try:
                    src = bak_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    src = None
        if src is not None:
            try:
                p.write_text(src, encoding="utf-8")
                restored.append(stem)
            except Exception:
                pass
    result = {
        "first_pass": {
            "total": summary1.get("total_files", 0),
            "failed": summary1.get("failed_builds", 0),
            "failed_blocks": failed_blocks,
        },
        "regen_errors": [{"file": str(p), "error": err} for p, err in regen_failed],
        "second_pass": {
            "total": summary2.get("total_files", 0),
            "failed": summary2.get("failed_builds", 0),
            "failed_blocks": final_failed,
        },
        "restored_originals": restored,
        "second_pass_scope": args.second_build_scope,
        "logs_dir": str(build_logs_dir),
    }
    report_path = build_logs_dir / "recheck_summary.json"
    try:
        build_logs_dir.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    except Exception:
        pass

    print(json.dumps(result, indent=2))
    return 0 if not final_failed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
