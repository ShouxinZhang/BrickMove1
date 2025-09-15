#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 sfs4_reshape_with_main.json 中提取每条记录的 "main theorem statement" 字段，
按顺序写入 sfs4_new_blocks/Block_XXX.lean 文件。

用法:
  python3 export_main_statements.py \
    --input ../sfs4_reshape_with_main.json \
    --outdir ../sfs4_new_blocks \
    [--start 1] [--overwrite]

说明:
- 默认从 1 开始编号，文件名形如 Block_001.lean, Block_002.lean, ...
- 若存在同名文件，默认跳过并提示；加 --overwrite 则覆盖。
- 若某条记录缺少该字段或为空，跳过并打印 warning。
"""
import argparse
import json
import os
import sys
from typing import List, Dict, Any


def load_json(path: str) -> List[Dict[str, Any]]:
    with open(path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"JSON 解析失败: {e}", file=sys.stderr)
            sys.exit(1)
    if not isinstance(data, list):
        print("JSON 顶层应为数组(list)", file=sys.stderr)
        sys.exit(1)
    return data


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def make_filename(index: int) -> str:
    return f"Block_{index:03d}.lean"


def write_block(outdir: str, index: int, content: str, overwrite: bool) -> str:
    filename = make_filename(index)
    dst = os.path.join(outdir, filename)
    if os.path.exists(dst) and not overwrite:
        print(f"跳过已存在: {filename}")
        return dst
    with open(dst, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"写入: {filename} ({len(content)} bytes)")
    return dst


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 main theorem statement 到 Lean block 文件")
    parser.add_argument('--input', required=True, help='输入 JSON 路径 (sfs4_reshape_with_main.json)')
    parser.add_argument('--outdir', required=True, help='输出目录 (sfs4_new_blocks)')
    parser.add_argument('--start', type=int, default=1, help='起始编号，默认 1')
    parser.add_argument('--overwrite', action='store_true', help='允许覆盖已有文件')
    args = parser.parse_args()

    items = load_json(args.input)
    ensure_dir(args.outdir)

    idx = args.start
    exported = 0
    first_file = None
    last_file = None

    for obj in items:
        stmt = obj.get('main theorem statement')
        if not stmt or not isinstance(stmt, str) or stmt.strip() == '':
            for alt in ("main_theorem_statement", "main_theorem", "statement"):
                if isinstance(obj.get(alt), str) and obj[alt].strip():
                    stmt = obj[alt]
                    break
        if not stmt or not isinstance(stmt, str) or stmt.strip() == '':
            print(f"警告: 跳过一条记录（缺少 main theorem statement）: {obj.get('id', '?')}")
            continue
        path = write_block(args.outdir, idx, stmt, args.overwrite)
        if first_file is None:
            first_file = os.path.basename(path)
        last_file = os.path.basename(path)
        idx += 1
        exported += 1

    print(f"完成: 共导出 {exported} 条，范围 {first_file} -> {last_file}")


if __name__ == '__main__':
    main()
