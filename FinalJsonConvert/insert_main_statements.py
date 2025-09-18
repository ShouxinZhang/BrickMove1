#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Insert "main theorem statement" into records of an input JSON by matching ids
with Lean source files under a specified subfolder.

- Each Lean file is expected to be named like `formalProof_<id>.lean`.
- For each JSON object having an "id" field, if a corresponding Lean file exists,
  its entire contents will be inserted/overwritten as the value of the
  "main theorem statement" field.
- The result is written to FinalJsonConvert/mainStatementJson as
  `<lean_subdir>.<YYYYMMDD-HHMMSS>.json` by default.

Usage:
  python3 insert_main_statements.py \
    --input FinalJsonConvert/OriginalJson/easy_success.json \
    --lean-subdir MTS20250917_214637 \
    [--lean-root FinalJsonConvert/Lean] \
    [--outdir FinalJsonConvert/mainStatementJson] \
    [--output-name custom_name.json] \
    [--overwrite]
"""

from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

DEFAULT_LEAN_ROOT = Path("FinalJsonConvert/Lean")
DEFAULT_OUTDIR = Path("FinalJsonConvert/mainStatementJson")


def read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        raise RuntimeError(f"Failed reading {p}: {e}")


def load_json(p: Path):
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise RuntimeError(f"Failed loading JSON {p}: {e}")


def save_json(obj, p: Path, overwrite: bool = False):
    if p.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing file: {p}. Use --overwrite to allow.")
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def build_id_to_lean_map(lean_dir: Path) -> dict[int, Path]:
    mapping: dict[int, Path] = {}
    for p in lean_dir.glob("formalProof_*.lean"):
        try:
            stem = p.stem  # e.g., formalProof_5447
            id_str = stem.split("_")[1]
            _id = int(id_str)
            mapping[_id] = p
        except Exception:
            # Skip unexpected filenames
            continue
    return mapping


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Insert 'main theorem statement' from Lean files into JSON by id.")
    parser.add_argument("--input", required=True, help="Path to the source JSON file.")
    parser.add_argument("--lean-subdir", required=True, help="Subfolder under Lean/ containing formalProof_*.lean files, e.g. MTS20250917_214637")
    parser.add_argument("--lean-root", default=str(DEFAULT_LEAN_ROOT), help="Root folder containing Lean subfolders (default: FinalJsonConvert/Lean)")
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR), help="Output directory for updated JSON (default: FinalJsonConvert/mainStatementJson)")
    parser.add_argument("--output-name", default=None, help="Optional explicit output filename. If omitted, uses '<lean-subdir>.<timestamp>.json'.")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting existing output file.")

    args = parser.parse_args(argv)

    input_path = Path(args.input)
    lean_root = Path(args.lean_root)
    outdir = Path(args.outdir)
    lean_dir = lean_root / args.lean_subdir

    if not input_path.exists():
        print(f"Input JSON not found: {input_path}", file=sys.stderr)
        return 2
    if not lean_dir.exists():
        print(f"Lean subdir not found: {lean_dir}", file=sys.stderr)
        return 2

    data = load_json(input_path)
    if not isinstance(data, list):
        print(f"Expected input JSON to be a list of objects, got {type(data)}", file=sys.stderr)
        return 2

    id_to_file = build_id_to_lean_map(lean_dir)
    if not id_to_file:
        print(f"No 'formalProof_*.lean' files found in {lean_dir}", file=sys.stderr)
        return 2

    updated = 0
    missing = 0

    for idx, obj in enumerate(data):
        if not isinstance(obj, dict):
            continue
        _id = obj.get("id")
        if not isinstance(_id, int):
            continue
        lp = id_to_file.get(_id)
        if lp is None:
            missing += 1
            continue
        lean_text = read_text(lp)
        obj["main theorem statement"] = lean_text
        updated += 1

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.output_name:
        output_path = outdir / args.output_name
    else:
        # sanitize subdir for filename (replace path separators just in case)
        safe_sub = str(args.lean_subdir).replace("/", "_")
        output_path = outdir / f"{safe_sub}.{timestamp}.json"

    save_json(data, output_path, overwrite=args.overwrite)

    print(f"Done. Updated: {updated}, missing Lean file: {missing}")
    print(f"Output: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
