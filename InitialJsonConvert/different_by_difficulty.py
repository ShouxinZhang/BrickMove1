#!/usr/bin/env python3
"""Split JSON items into difficulty buckets based on formalProof line count."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from convert_initial_json import ensure_newline, load_json, make_lean_filename

DEFAULT_THRESHOLD = 100


def count_lines(text: str) -> int:
    return len(text.splitlines()) if text else 0


def partition_items(
    data: Iterable[Dict[str, Any]],
    *,
    threshold: int = DEFAULT_THRESHOLD,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    easy: List[Dict[str, Any]] = []
    normal: List[Dict[str, Any]] = []
    for item in data:
        proof = item.get("formalProof")
        lines = count_lines(proof) if isinstance(proof, str) else 0
        target = easy if lines <= threshold else normal
        target.append(item)
    return easy, normal


def export_group(
    items: List[Dict[str, Any]],
    *,
    base_dir: Path,
    label: str,
) -> Tuple[Path, Path, List[Dict[str, Any]]]:
    json_path = base_dir / f"{label}.json"
    lean_dir = base_dir / f"{label}_lean"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    lean_dir.mkdir(parents=True, exist_ok=True)

    json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    used_names: set[str] = set()
    mapping: List[Dict[str, Any]] = []
    for idx, item in enumerate(items):
        proof = item.get("formalProof")
        if not isinstance(proof, str) or not proof.strip():
            continue
        filename = make_lean_filename(item, idx, used_names)
        path = lean_dir / filename
        path.write_text(ensure_newline(proof), encoding="utf-8")
        mapping.append(
            {
                "index": idx,
                "filename": filename,
                "stem": Path(filename).stem,
                "path": path,
                "item": item,
            }
        )
    return json_path, lean_dir, mapping


def split_and_export(
    input_path: Path,
    output_root: Path,
    *,
    threshold: int = DEFAULT_THRESHOLD,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    data = load_json(input_path)
    easy, normal = partition_items(data, threshold=threshold)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_name = f"split_{timestamp}"
    base_dir = output_root / "difficulty" / session_name
    base_dir.mkdir(parents=True, exist_ok=True)

    groups = []
    metadata_groups: List[Dict[str, Any]] = []
    for label, subset in ("easy", easy), ("normal", normal):
        json_path, lean_dir, mapping = export_group(subset, base_dir=base_dir, label=label)
        groups.append(
            {
                "label": label,
                "count": len(subset),
                "json_path": str(json_path),
                "lean_dir": str(lean_dir),
            }
        )
        metadata_groups.append(
            {
                "label": label,
                "items": subset,
                "json_path": json_path,
                "lean_dir": lean_dir,
                "mapping": mapping,
            }
        )

    result = {
        "base_dir": str(base_dir),
        "groups": groups,
        "threshold": threshold,
        "total": len(data),
        "session_name": session_name,
    }
    metadata = {
        "base_dir": base_dir,
        "groups": metadata_groups,
        "session_name": session_name,
        "threshold": threshold,
        "total": len(data),
    }
    return result, metadata


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按 formalProof 行数拆分 JSON")
    parser.add_argument("input", type=Path, help="输入 JSON 文件路径")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parent / "output",
        help="输出根目录 (默认: InitialJsonConvert/output)",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        help="easy/normal 阈值 (按行数，默认 100)",
    )
    args = parser.parse_args(argv)

    input_path = args.input
    if not input_path.exists():
        raise SystemExit(f"找不到输入文件: {input_path}")

    result, _metadata = split_and_export(input_path, args.output_root, threshold=max(args.threshold, 0))
    print(
        "拆分完成：total={total}, easy={easy}, normal={normal}, 输出目录={base_dir}".format(
            total=result["total"],
            easy=next((g["count"] for g in result["groups"] if g["label"] == "easy"), 0),
            normal=next((g["count"] for g in result["groups"] if g["label"] == "normal"), 0),
            base_dir=result["base_dir"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
