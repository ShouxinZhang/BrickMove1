#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从一个 Lean 代码目录（例如 sfs4_new_blocks/ 中的 Block_XXX.lean 文件）
反向导出到 JSON 文件，或将内容回填到一个现有的模板 JSON 中。

两种用法：
1) 生成全新的 JSON（仅包含 id 和 main theorem statement）：
   python3 JsonExport/export_from_lean_dir.py \
     --indir sfs4_new_blocks \
     --output sfs4_from_lean.json

2) 回填到现有模板 JSON（按顺序或起始编号对齐）：
   python3 JsonExport/export_from_lean_dir.py \
     --indir sfs4_new_blocks \
     --template sfs4_reshape_with_main.json \
     --output sfs4_reshape_with_main.updated.json \
     --start 1

说明：
- 本工具假设文件名形如 Block_001.lean, Block_002.lean, ...，并按数字顺序对齐。
- 默认写入字段名为 "main theorem statement"，可用 --field 指定其它名称。
- 当提供 --template 时，会加载模板 JSON（数组），逐条根据顺序回填指定字段；
  若某 index 对应 Lean 文件缺失，将跳过并提示。
"""
import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


BLOCK_RE = re.compile(r"^Block_(\d{1,}).lean$", re.IGNORECASE)


@dataclass
class BlockFile:
    index: int
    path: Path


def scan_blocks(indir: Path) -> List[BlockFile]:
    if not indir.exists() or not indir.is_dir():
        print(f"输入目录不存在或不是目录: {indir}", file=sys.stderr)
        sys.exit(1)
    out: List[BlockFile] = []
    for p in indir.glob("**/*.lean"):
        name = p.name
        m = BLOCK_RE.match(name)
        if not m:
            continue
        try:
            idx = int(m.group(1))
        except Exception:
            continue
        out.append(BlockFile(index=idx, path=p))
    out.sort(key=lambda b: b.index)
    return out


def load_json_list(path: Path) -> List[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"解析 JSON 失败: {e}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(data, list):
        print("模板 JSON 顶层应为数组(list)", file=sys.stderr)
        sys.exit(1)
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description="从 Lean 文件夹导出/回填 JSON")
    ap.add_argument("--indir", required=True, help="包含 Block_XXX.lean 的目录")
    ap.add_argument("--output", required=True, help="输出 JSON 文件路径")
    ap.add_argument("--template", help="模板 JSON（若指定则回填该文件）")
    ap.add_argument("--field", default="main theorem statement", help="写入的字段名，默认 'main theorem statement'")
    ap.add_argument("--start", type=int, default=1, help="当按顺序对齐时的起始编号，默认 1")
    ap.add_argument("--limit", type=int, default=0, help="仅处理前 N 个（调试用），0 表示不限制")
    ap.add_argument("--overwrite", action="store_true", help="若输出已存在则覆盖")
    args = ap.parse_args()

    indir = Path(args.indir)
    output = Path(args.output)
    if output.exists() and not args.overwrite:
        print(f"输出文件已存在：{output}。使用 --overwrite 以覆盖。", file=sys.stderr)
        sys.exit(2)

    blocks = scan_blocks(indir)
    if args.limit and args.limit > 0:
        blocks = blocks[: args.limit]

    if not blocks:
        print("未在指定目录中找到 Block_XXX.lean 文件", file=sys.stderr)
        sys.exit(1)

    field = args.field
    start = args.start

    if args.template:
        # 回填模式
        template_path = Path(args.template)
        items = load_json_list(template_path)

        # 建立 index -> BlockFile 映射（严格按文件名里的数字）
        by_index: Dict[int, BlockFile] = {b.index: b for b in blocks}
        updated = 0
        skipped = 0

        # 将第 i 条（从 0 开始）映射到 Block_{start+i}
        for i, obj in enumerate(items):
            idx = start + i
            bf = by_index.get(idx)
            if not bf:
                skipped += 1
                continue
            try:
                txt = bf.path.read_text(encoding="utf-8")
            except Exception:
                txt = bf.path.read_bytes().decode("utf-8", "ignore")
            obj[field] = txt if txt.endswith("\n") else (txt + "\n")
            updated += 1

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"完成回填：更新 {updated} 条，跳过 {skipped} 条。写入 {output}")
        return

    # 新建导出模式
    result: List[dict] = []
    for b in blocks:
        try:
            txt = b.path.read_text(encoding="utf-8")
        except Exception:
            txt = b.path.read_bytes().decode("utf-8", "ignore")
        result.append({
            "id": b.index,
            field: txt if txt.endswith("\n") else (txt + "\n"),
        })

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"完成导出：共 {len(result)} 条。写入 {output}")


if __name__ == "__main__":
    main()
