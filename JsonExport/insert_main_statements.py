#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Tuple, List


def load_json(path: Path):
    with path.open('r', encoding='utf-8') as f:
        # Allow either array or object with top-level key holding array
        data = json.load(f)
    return data


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ingest_main_statements(records, src_dir: Path) -> Tuple[int, List[int], List[int]]:
    """
    For each record with integer 'id', read `formalProof_<id>.lean` from src_dir
    and assign its contents to the key 'main theorem statement'.

    Returns: (updated_count, missing_ids, non_int_ids)
    """
    updated = 0
    missing: List[int] = []
    nonint: List[int] = []

    def update_record(rec) -> bool:
        nonlocal updated
        if not isinstance(rec, dict):
            return False
        rid = rec.get('id')
        if not isinstance(rid, int):
            nonint.append(rid)
            return False
        lean_path = src_dir / f"formalProof_{rid}.lean"
        if not lean_path.exists():
            missing.append(rid)
            return False
        try:
            content = lean_path.read_text(encoding='utf-8')
        except Exception:
            missing.append(rid)
            return False
        rec['main theorem statement'] = content
        updated += 1
        return True

    if isinstance(records, list):
        for rec in records:
            update_record(rec)
    elif isinstance(records, dict):
        # Try conventional layout { "data": [ ... ] }
        if 'data' in records and isinstance(records['data'], list):
            for rec in records['data']:
                update_record(rec)
        else:
            # Fallback: treat dict itself as one record
            update_record(records)
    else:
        pass

    return updated, missing, nonint


def main() -> int:
    ap = argparse.ArgumentParser(description='Populate "main theorem statement" from stripped Lean files')
    ap.add_argument('--json', type=Path, required=True, help='Path to input JSON file')
    ap.add_argument('--src-dir', type=Path, required=True, help='Directory containing stripped Lean files')
    ap.add_argument('--out', type=Path, default=None, help='Output JSON file (if omitted and --inplace not set, defaults to <input>.updated.json)')
    ap.add_argument('--inplace', action='store_true', help='Overwrite the input JSON file in place')
    args = ap.parse_args()

    if not args.json.exists():
        print(f"ERROR: JSON not found: {args.json}")
        return 2
    if not args.src_dir.exists():
        print(f"ERROR: src-dir not found: {args.src_dir}")
        return 2

    data = load_json(args.json)
    updated, missing, nonint = ingest_main_statements(data, args.src_dir)

    if args.inplace:
        out_path = args.json
    else:
        out_path = args.out or args.json.with_suffix('.updated.json')

    save_json(out_path, data)

    print(f"Updated records: {updated}")
    if missing:
        print(f"Missing Lean files for ids ({len(missing)}): {sorted(set(missing))[:50]}{' ...' if len(set(missing))>50 else ''}")
    if nonint:
        print(f"Non-integer ids encountered ({len(nonint)}): {sorted(set([x for x in nonint if x is not None]))}")
    print(f"Wrote: {out_path}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
