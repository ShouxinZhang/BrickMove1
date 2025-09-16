#!/usr/bin/env python3
"""Utility to prettify initial JSON dumps and export embedded Lean proofs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def load_json(path: Path) -> List[Dict[str, Any]]:
    """Load a list-based JSON file and fail fast if the structure is unexpected."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - convenience for CLI failures
        raise SystemExit(f"无法解析 JSON 文件: {exc}") from exc

    if not isinstance(data, list):
        raise SystemExit("输入 JSON 的顶层结构必须是数组 (list)。")

    return data


def ensure_newline(text: str) -> str:
    """Append a trailing newline so exported Lean files are editor-friendly."""
    return text if text.endswith("\n") else text + "\n"


def make_lean_filename(item: Dict[str, Any], index: int, used: set[str]) -> str:
    """Build a stable filename using available identifiers, avoiding collisions."""
    parts: List[str] = []
    for key in ("id", "task_id", "question_id"):
        value = item.get(key)
        if value is not None:
            parts.append(str(value))
            break
    if not parts:
        parts.append(f"{index + 1:04d}")
    base = f"formalProof_{parts[0]}"
    candidate = f"{base}.lean"
    suffix = 1
    while candidate in used:
        candidate = f"{base}_{suffix}.lean"
        suffix += 1
    used.add(candidate)
    return candidate


def write_outputs(data: List[Dict[str, Any]], json_output: Path, lean_dir: Path) -> None:
    json_output.parent.mkdir(parents=True, exist_ok=True)
    lean_dir.mkdir(parents=True, exist_ok=True)

    json_output.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    used_names: set[str] = set()
    for index, item in enumerate(data):
        proof = item.get("formalProof")
        if not proof:
            continue
        name = make_lean_filename(item, index, used_names)
        (lean_dir / name).write_text(ensure_newline(proof), encoding="utf-8")


def convert(input_path: Path, output_root: Path) -> None:
    data = load_json(input_path)

    json_output = output_root / "json" / input_path.name
    lean_dir = output_root / "formalProof"

    write_outputs(data, json_output, lean_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prettify JSON and export Lean proofs")
    parser.add_argument("input", help="路径到原始 JSON 文件")
    parser.add_argument(
        "--output-root",
        default=Path(__file__).resolve().parent / "output",
        type=Path,
        help="输出根目录 (默认: InitialJsonConvert/output)",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"找不到输入文件: {input_path}")

    output_root = args.output_root
    convert(input_path, output_root)
    print(
        f"已生成可读性更好的 JSON 到 {output_root / 'json' / input_path.name}，"
        f"并导出 Lean 文件到 {output_root / 'formalProof'}"
    )


if __name__ == "__main__":
    main()
