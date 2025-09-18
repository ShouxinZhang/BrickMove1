#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
随机从一个 JSON 文件中抽取 5 条用于“formalProof vs main theorem statement”的对比样本：
- 其中 3 条 formalProof 行数 < 60；
- 其中 2 条 formalProof 行数 > 100。

用法：
  python3 LLM_Agent/tmp_extract_prompt_samples.py \
    --json /path/to/supple_formal_statement_5.updated....234711.json \
    --seed 42 \
    --out LLM_Agent/output/prompt_samples.md

若不提供 --json，将默认尝试用户下载目录中的文件名：
  ~/Downloads/supple_formal_statement_5.updated.20250916-231748.export.20250916-234711.json
"""
import argparse
import json
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_DOWNLOADS_NAME = "supple_formal_statement_5.updated.20250916-231748.export.20250916-234711.json"


def _default_json_path() -> Path:
    home = Path.home()
    return home / "Downloads" / DEFAULT_DOWNLOADS_NAME


def _get(obj: Dict[str, Any], keys: List[str]) -> Optional[str]:
    for k in keys:
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return None


def _line_count(s: str) -> int:
    return len(s.splitlines())


@dataclass
class Item:
    idx: int
    id: Any
    formal: str
    main: Optional[str]
    lines: int


def load_items(path: Path) -> List[Item]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise SystemExit(f"无法读取或解析 JSON: {e}")
    if not isinstance(data, list):
        raise SystemExit("输入 JSON 顶层必须是数组(list)")

    items: List[Item] = []
    for i, obj in enumerate(data):
        if not isinstance(obj, dict):
            continue
        formal = _get(obj, [
            "formalProof", "formal_proof", "lean", "leanCode", "lean_code", "proof", "code"
        ])
        if not formal:
            continue
        main = _get(obj, [
            "main theorem statement", "main_theorem_statement", "main_theorem", "statement"
        ])
        items.append(Item(
            idx=i,
            id=obj.get("id", i+1),
            formal=formal,
            main=main,
            lines=_line_count(formal),
        ))
    return items


def sample_pairs(
    items: List[Item],
    *,
    small_max: int = 60,
    large_min: int = 100,
    small_count: int = 3,
    large_count: int = 2,
    seed: Optional[int] = None,
) -> Tuple[List[Item], List[Item]]:
    rng = random.Random(seed)
    small = [it for it in items if it.lines < small_max]
    large = [it for it in items if it.lines > large_min]

    if len(small) < small_count:
        print(f"警告: small(<{small_max}) 可用 {len(small)} < 需要 {small_count}", file=sys.stderr)
    if len(large) < large_count:
        print(f"警告: large(>{large_min}) 可用 {len(large)} < 需要 {large_count}", file=sys.stderr)

    rng.shuffle(small)
    rng.shuffle(large)
    chosen_small = small[:small_count]
    chosen_large = large[:large_count]

    # 若不足，尝试从其余样本中补齐（不再强制阈值）
    needed = small_count - len(chosen_small)
    if needed > 0:
        pool = [it for it in items if it not in chosen_small and it not in chosen_large]
        rng.shuffle(pool)
        chosen_small += pool[:needed]

    needed = large_count - len(chosen_large)
    if needed > 0:
        pool = [it for it in items if it not in chosen_small and it not in chosen_large]
        rng.shuffle(pool)
        chosen_large += pool[:needed]

    return chosen_small, chosen_large


def to_markdown(small: List[Item], large: List[Item]) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts: List[str] = []
    parts.append(f"# Prompt Few-shot 候选样本（{ts}）\n")
    parts.append(f"- 小样本（formalProof 行数 < 60）：{len(small)} 条")
    parts.append(f"- 大样本（formalProof 行数 > 100）：{len(large)} 条\n")

    def block(title: str, items: List[Item]):
        parts.append(f"## {title}\n")
        for k, it in enumerate(items, 1):
            parts.append(f"### {k}. id={it.id}  行数={it.lines}  (idx={it.idx})")
            parts.append("**FormalProof:**\n")
            parts.append("```lean")
            parts.append(it.formal.rstrip())
            parts.append("```")
            parts.append("")
            parts.append("**Main Theorem Statement:**\n")
            parts.append("```lean")
            parts.append((it.main or "").rstrip())
            parts.append("```")
            parts.append("")

    block("小样本 (< 60 行)", small)
    block("大样本 (> 100 行)", large)
    return "\n".join(parts)


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description="随机提取 5 条 formalProof vs main theorem statement 对比样本")
    ap.add_argument("--json", type=Path, default=None, help="JSON 文件路径（默认尝试 ~/Downloads/...234711.json）")
    ap.add_argument("--seed", type=int, default=None, help="随机种子")
    ap.add_argument("--small-max", type=int, default=60, help="小样本行数上限（不含）")
    ap.add_argument("--large-min", type=int, default=100, help="大样本行数下限（不含）")
    ap.add_argument("--small-count", type=int, default=3, help="小样本数量")
    ap.add_argument("--large-count", type=int, default=2, help="大样本数量")
    ap.add_argument("--out", type=Path, default=None, help="输出 Markdown 文件（默认写到 LLM_Agent/output/ 下）")
    args = ap.parse_args(argv)

    json_path = args.json or _default_json_path()
    if not json_path.exists():
        raise SystemExit(f"找不到 JSON 文件: {json_path}")

    items = load_items(json_path)
    if not items:
        raise SystemExit("没有可用的 formalProof 记录")

    small, large = sample_pairs(
        items,
        small_max=args.small_max,
        large_min=args.large_min,
        small_count=args.small_count,
        large_count=args.large_count,
        seed=args.seed,
    )

    md = to_markdown(small, large)
    out_path = args.out
    if out_path is None:
        base = Path(__file__).parent / "output"
        base.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = base / f"prompt_samples_{ts}.md"
    out_path.write_text(md, encoding="utf-8")
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
