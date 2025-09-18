#!/usr/bin/env python3
"""
Backfill JSON entries with repaired Lean proofs by id.

Behavior:
- Reads an input JSON array of objects that include an integer field `id` and a string field `formalProof`.
- For each entry, if a file named `formalProof_<id>.lean` exists in the same directory as this script,
  replaces the entry's `formalProof` with the file's content.
- If no such file exists (i.e., repair failed), drops that entry from the output.
- Writes the processed JSON to the `output/` subfolder under this dataset directory.

Usage examples:
  python3 lean_to_json.py \
	--input json/easy_failed.json \
	--output output/easy_failed.fixed.json

Defaults:
- `--input` defaults to `json/easy_failed.json` relative to this script's directory.
- `--output` defaults to `output/easy_failed.fixed.json` relative to this script's directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def read_text(path: Path) -> str:
	try:
		return path.read_text(encoding="utf-8")
	except UnicodeDecodeError:
		# Fallback to latin-1 if encoding is unexpected
		return path.read_text(encoding="latin-1")


def process(input_path: Path, output_path: Path) -> int:
	base_dir = Path(__file__).resolve().parent

	# Resolve paths relative to the dataset folder (the directory of this script)
	if not input_path.is_absolute():
		input_path = (base_dir / input_path).resolve()
	if not output_path.is_absolute():
		output_path = (base_dir / output_path).resolve()

	# Where repaired Lean files live: same directory as this script
	lean_dir = base_dir

	if not input_path.exists():
		print(f"[ERROR] Input JSON not found: {input_path}", file=sys.stderr)
		return 2

	try:
		data = json.loads(input_path.read_text(encoding="utf-8"))
	except Exception as e:
		print(f"[ERROR] Failed to read/parse JSON {input_path}: {e}", file=sys.stderr)
		return 3

	if not isinstance(data, list):
		print(f"[ERROR] Expected a JSON array at top level in {input_path}", file=sys.stderr)
		return 4

	fixed_items = []
	missing_ids = []
	total = 0

	for item in data:
		total += 1
		if not isinstance(item, dict):
			continue
		id_val = item.get("id")
		if not isinstance(id_val, int):
			continue
		lean_file = lean_dir / f"formalProof_{id_val}.lean"
		if lean_file.exists():
			try:
				content = read_text(lean_file)
			except Exception as e:
				print(f"[WARN] Failed reading {lean_file}: {e}", file=sys.stderr)
				missing_ids.append(id_val)
				continue
			# Replace formalProof with file content
			new_item = dict(item)
			new_item["formalProof"] = content
			fixed_items.append(new_item)
		else:
			missing_ids.append(id_val)

	# Ensure output directory exists
	output_path.parent.mkdir(parents=True, exist_ok=True)

	# Write output with UTF-8 and nice formatting
	output_path.write_text(
		json.dumps(fixed_items, ensure_ascii=False, indent=2) + "\n",
		encoding="utf-8",
	)

	kept = len(fixed_items)
	dropped = total - kept
	print(
		f"Processed {total} items: kept {kept}, dropped {dropped}. Output: {output_path}")
	if missing_ids:
		# Show a brief summary to stderr to aid debugging
		preview = ", ".join(map(str, missing_ids[:20]))
		more = "" if len(missing_ids) <= 20 else f", ... (+{len(missing_ids) - 20} more)"
		print(f"[INFO] Missing/failed ids: {preview}{more}", file=sys.stderr)
	return 0


def main(argv: list[str]) -> int:
	parser = argparse.ArgumentParser(description="Backfill JSON with repaired Lean proofs by id")
	parser.add_argument(
		"--input",
		type=Path,
		default=Path("json/easy_failed.json"),
		help="Input JSON path (relative to this dataset dir by default)",
	)
	parser.add_argument(
		"--output",
		type=Path,
		default=Path("output/easy_failed.fixed.json"),
		help="Output JSON path (relative to this dataset dir by default)",
	)
	args = parser.parse_args(argv)
	return process(args.input, args.output)


if __name__ == "__main__":
	raise SystemExit(main(sys.argv[1:]))

