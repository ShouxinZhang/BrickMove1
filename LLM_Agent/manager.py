#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path
import json


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _add_sys_path():
    here = Path(__file__).parent
    root = _repo_root()
    for p in [str(here), str(root)]:
        if p not in sys.path:
            sys.path.insert(0, p)


_add_sys_path()

import llm_agent  # type: ignore
import llm_recheck_agent  # type: ignore


def cmd_generate(args: argparse.Namespace) -> int:
    argv = []
    argv += ["--input-dir", str(args.input_dir)]
    if args.match:
        argv += ["--match", args.match]
    if args.output_dir:
        argv += ["--output-dir", str(args.output_dir)]
    if args.model:
        argv += ["--model", args.model]
    if args.max_tokens is not None:
        argv += ["--max-tokens", str(args.max_tokens)]
    if args.no_max_tokens:
        argv += ["--no-max-tokens"]
    if args.sleep:
        argv += ["--sleep", str(args.sleep)]
    if args.overwrite:
        argv += ["--overwrite"]
    if args.normalize:
        argv += ["--normalize"]
    if args.limit:
        argv += ["--limit", str(args.limit)]
    if args.append_system:
        argv += ["--append-system", args.append_system]
    if args.api_key:
        argv += ["--api-key", args.api_key]
    if args.continue_on_error:
        argv += ["--continue-on-error"]
    if args.retries is not None:
        argv += ["--retries", str(args.retries)]
    if args.workers is not None:
        argv += ["--workers", str(args.workers)]
    if args.fewshot:
        argv += ["--fewshot"]
    if args.fewshot_json:
        argv += ["--fewshot-json", str(args.fewshot_json)]
    else:
        # Default-inject PromptExample/history.json when not specified
        default_hist = Path(__file__).parent / "PromptExample" / "history.json"
        if default_hist.exists():
            argv += ["--fewshot-json", str(default_hist)]
    if args.fail_out:
        argv += ["--fail-out", str(args.fail_out)]
    if args.error_log:
        argv += ["--error-log", str(args.error_log)]
    return llm_agent.main(argv)


def cmd_recheck(args: argparse.Namespace) -> int:
    argv = []
    if args.target_dir:
        argv += ["--target-dir", str(args.target_dir)]
    if args.pattern:
        argv += ["--pattern", args.pattern]
    if args.workers is not None:
        argv += ["--workers", str(args.workers)]
    if args.model:
        argv += ["--model", args.model]
    if args.max_tokens is not None:
        argv += ["--max-tokens", str(args.max_tokens)]
    if args.no_max_tokens:
        argv += ["--no-max-tokens"]
    if args.normalize:
        argv += ["--normalize"]
    if args.retries is not None:
        argv += ["--retries", str(args.retries)]
    if args.append_system:
        argv += ["--append-system", args.append_system]
    if args.api_key:
        argv += ["--api-key", args.api_key]
    if args.second_build_scope:
        argv += ["--second-build-scope", args.second_build_scope]
    # Few-shot support for recheck (pass-through to llm_recheck_agent)
    if getattr(args, "fewshot", False):
        argv += ["--fewshot"]
    if getattr(args, "fewshot_json", None):
        argv += ["--fewshot-json", str(args.fewshot_json)]
    else:
        default_hist = Path(__file__).parent / "PromptExample" / "history.json"
        if default_hist.exists():
            argv += ["--fewshot-json", str(default_hist)]
    return llm_recheck_agent.main(argv)


def _summarize_success_json(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("JSON must be a list of objects")
    total = len(data)
    unique_q = len({d.get("question_id") for d in data})
    unique_task = len({d.get("task_id") for d in data})
    # Compute counts
    def _count_kw(s: str, kw: str) -> int:
        try:
            return s.count(kw)
        except Exception:
            return 0
    stats = {
        "total": total,
        "unique_question_id": unique_q,
        "unique_task_id": unique_task,
        "total_chars": 0,
        "total_lines": 0,
        "theorem_count": 0,
        "lemma_count": 0,
        "example_count": 0,
    }
    rows = []
    for d in data:
        fp = d.get("formalProof") or ""
        ch = len(fp)
        ln = fp.count("\n") + (1 if fp else 0)
        th = _count_kw(fp, "theorem ")
        le = _count_kw(fp, "lemma ")
        ex = _count_kw(fp, "example ")
        stats["total_chars"] += ch
        stats["total_lines"] += ln
        stats["theorem_count"] += th
        stats["lemma_count"] += le
        stats["example_count"] += ex
        rows.append({
            "id": d.get("id"),
            "question_id": d.get("question_id"),
            "task_id": d.get("task_id"),
            "chars": ch,
            "lines": ln,
            "theorems": th,
            "lemmas": le,
            "examples": ex,
        })
    # Write CSV next to the JSON
    import csv
    out_csv = path.with_suffix(".csv")
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "question_id", "task_id", "chars", "lines", "theorems", "lemmas", "examples"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    stats["csv_path"] = str(out_csv)
    return stats


def cmd_summarize_json(args: argparse.Namespace) -> int:
    path = args.input
    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 2
    try:
        stats = _summarize_success_json(path)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    # Pretty print summary
    print(
        json.dumps(
            {
                "file": str(path),
                **stats,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    # 1) generate
    default_hist = Path(__file__).parent / "PromptExample" / "history.json"
    default_hist_opt = default_hist if default_hist.exists() else None

    gen_ns = argparse.Namespace(
        input_dir=args.input_dir,
        match=args.match,
        output_dir=args.output_dir,
        model=args.gen_model,
        max_tokens=args.gen_max_tokens,
        no_max_tokens=args.gen_no_max_tokens,
        sleep=args.gen_sleep,
        overwrite=True if args.overwrite is None else args.overwrite,
        normalize=True if args.normalize is None else args.normalize,
        limit=args.limit,
        append_system=args.gen_append_system,
        api_key=args.api_key,
        continue_on_error=True,
        retries=args.gen_retries,
        workers=args.gen_workers,
        fewshot=args.gen_fewshot,
        fewshot_json=(args.gen_fewshot_json if args.gen_fewshot_json is not None else default_hist_opt),
        fail_out=None,
        error_log=None,
    )
    rc = cmd_generate(gen_ns)
    if rc != 0:
        return rc

    # 2) determine latest MTS dir if output not provided
    out_dir = args.output_dir
    if not out_dir:
        base = Path(__file__).parent / "output"
        candidates = [p for p in base.glob("MTS*") if p.is_dir()]
        if not candidates:
            print("No MTS output found after generation.")
            return 2
        out_dir = str(max(candidates, key=lambda p: p.stat().st_mtime))

    # 3) recheck
    re_ns = argparse.Namespace(
        target_dir=Path(out_dir),
        pattern=args.pattern,
        workers=args.recheck_workers,
        model=args.recheck_model,
        max_tokens=args.recheck_max_tokens,
        no_max_tokens=args.recheck_no_max_tokens,
        normalize=True if args.normalize is None else args.normalize,
        retries=args.recheck_retries,
        append_system=args.recheck_append_system,
        api_key=args.api_key,
        second_build_scope=args.second_build_scope,
        fewshot=args.recheck_fewshot,
        fewshot_json=(args.recheck_fewshot_json if args.recheck_fewshot_json is not None else default_hist_opt),
    )
    return cmd_recheck(re_ns)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="LLM_Agent 管理器：统一调度生成与复检")
    sub = p.add_subparsers(dest="cmd", required=True)

    # generate
    g = sub.add_parser("generate", help="调用 llm_agent 生成主定理骨架")
    g.add_argument("--input-dir", type=Path, default=Path("sfs4_new_blocks"))
    g.add_argument("--match", type=str, default="*.lean")
    g.add_argument("--output-dir", type=Path, default=None)
    g.add_argument("--model", type=str, default="moonshotai/kimi-k2-0905")
    g.add_argument("--max-tokens", type=int, default=None)
    g.add_argument("--no-max-tokens", action="store_true")
    g.add_argument("--sleep", type=float, default=0.0)
    g.add_argument("--overwrite", action="store_true")
    g.add_argument("--normalize", action="store_true")
    g.add_argument("--limit", type=int, default=0)
    g.add_argument("--append-system", type=str, default="")
    g.add_argument("--api-key", type=str, default="")
    g.add_argument("--continue-on-error", action="store_true")
    g.add_argument("--retries", type=int, default=2)
    g.add_argument("--workers", type=int, default=16)
    g.add_argument("--fewshot", action="store_true")
    g.add_argument("--fewshot-json", type=Path, default=None)
    g.add_argument("--fail-out", type=Path, default=None)
    g.add_argument("--error-log", type=Path, default=None)
    g.set_defaults(func=cmd_generate)

    # recheck
    r = sub.add_parser("recheck", help="调用 llm_recheck_agent 对失败文件回写并二次构建")
    r.add_argument("--target-dir", type=Path, default=None)
    r.add_argument("--pattern", type=str, default="*.lean")
    r.add_argument("--workers", type=int, default=50)
    r.add_argument("--model", type=str, default="openai/gpt-5")
    r.add_argument("--max-tokens", type=int, default=None)
    r.add_argument("--no-max-tokens", action="store_true")
    r.add_argument("--normalize", action="store_true")
    r.add_argument("--retries", type=int, default=1)
    r.add_argument("--append-system", type=str, default="")
    r.add_argument("--api-key", type=str, default="")
    r.add_argument("--second-build-scope", choices=["all", "failed"], default="failed")
    r.add_argument("--fewshot", action="store_true")
    r.add_argument("--fewshot-json", type=Path, default=None)
    r.set_defaults(func=cmd_recheck)

    # pipeline
    p2 = sub.add_parser("pipeline", help="一键：生成 + 复检")
    p2.add_argument("--input-dir", type=Path, default=Path("sfs4_new_blocks"))
    p2.add_argument("--match", type=str, default="*.lean")
    p2.add_argument("--output-dir", type=Path, default=None)
    p2.add_argument("--pattern", type=str, default="*.lean")
    p2.add_argument("--limit", type=int, default=0)
    p2.add_argument("--normalize", action="store_true")
    p2.add_argument("--overwrite", action="store_true")
    p2.add_argument("--api-key", type=str, default="")
    p2.add_argument("--second-build-scope", choices=["all", "failed"], default="failed")
    # generate options
    p2.add_argument("--gen-model", type=str, default="openai/gpt-5") # also can choose "moonshotai/kimi-k2-0905"
    p2.add_argument("--gen-max-tokens", type=int, default=None)
    p2.add_argument("--gen-no-max-tokens", action="store_true")
    p2.add_argument("--gen-sleep", type=float, default=0.0)
    p2.add_argument("--gen-retries", type=int, default=2)
    p2.add_argument("--gen-workers", type=int, default=16)
    p2.add_argument("--gen-append-system", type=str, default="")
    p2.add_argument("--gen-fewshot", action="store_true")
    p2.add_argument("--gen-fewshot-json", type=Path, default=None)
    # recheck options
    p2.add_argument("--recheck-model", type=str, default="openai/gpt-5")
    p2.add_argument("--recheck-max-tokens", type=int, default=None)
    p2.add_argument("--recheck-no-max-tokens", action="store_true")
    p2.add_argument("--recheck-retries", type=int, default=1)
    p2.add_argument("--recheck-workers", type=int, default=50)
    p2.add_argument("--recheck-append-system", type=str, default="")
    p2.add_argument("--recheck-fewshot", action="store_true")
    p2.add_argument("--recheck-fewshot-json", type=Path, default=None)
    p2.set_defaults(func=cmd_pipeline)

    # summarize-json
    sj = sub.add_parser("summarize-json", help="读取 success.json 并输出统计与 CSV")
    sj.add_argument("--input", type=Path, required=True, help="路径到 success.json（含列表对象，字段包含 formalProof）")
    sj.set_defaults(func=cmd_summarize_json)

    return p


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
