#!/usr/bin/env python3
import argparse
from pathlib import Path
from typing import Iterable


def strip_lean_comments(text: str, preserve_lines: bool = False) -> str:
    """
    Remove Lean comments from source text.
    - Single-line comments: "-- ...\n"
    - Block comments: "/- ... -/" (including doc comments "/-- ... -/")
    - Handles nested block comments.
    - Respects string ("...") and char ('...') literals with escaping.

    If preserve_lines=True, newlines inside removed comments are kept, and
    other removed characters become spaces to keep line/column alignment loosely.
    """
    i = 0
    n = len(text)
    out_chars = []

    in_string = False
    string_delim = ""
    escape = False

    block_level = 0

    def put(ch: str):
        out_chars.append(ch)

    def put_removed(ch: str):
        if preserve_lines and ch == "\n":
            out_chars.append("\n")
        elif preserve_lines:
            out_chars.append(" ")
        # else: drop

    while i < n:
        ch = text[i]

        # Inside block comment: consume until matching -/
        if block_level > 0:
            if ch == "/" and i + 1 < n and text[i + 1] == "-":
                # potential nested start '/-'
                block_level += 1
                put_removed("/")
                put_removed("-")
                i += 2
                continue
            if ch == "-" and i + 1 < n and text[i + 1] == "/":
                # block end '-/'
                block_level -= 1
                put_removed("-")
                put_removed("/")
                i += 2
                continue
            put_removed(ch)
            i += 1
            continue

        # Not in block; if in string/char literal, just copy
        if in_string:
            put(ch)
            if escape:
                escape = False
            else:
                if ch == "\\":
                    escape = True
                elif ch == string_delim:
                    in_string = False
                    string_delim = ""
            i += 1
            continue

        # Detect string or char literal start
        if ch in ('"', "'"):
            in_string = True
            string_delim = ch
            put(ch)
            i += 1
            continue

        # Detect start of block comment '/-' or doc '/--'
        if ch == "/" and i + 1 < n and text[i + 1] == "-":
            block_level = 1
            # consume '/-'
            put_removed("/")
            put_removed("-")
            i += 2
            continue

        # Detect single-line comment '--'
        if ch == "-" and i + 1 < n and text[i + 1] == "-":
            # consume until end of line
            while i < n and text[i] != "\n":
                put_removed(text[i])
                i += 1
            # newline (if any) will be handled below normally
            continue

        # Normal character
        put(ch)
        i += 1

    return "".join(out_chars)


def iter_lean_files(path: Path) -> Iterable[Path]:
    if path.is_file() and path.suffix == ".lean":
        yield path
        return
    if path.is_dir():
        for p in path.rglob("*.lean"):
            if p.is_file():
                yield p


def main() -> int:
    ap = argparse.ArgumentParser(description="Strip Lean comments from files or folders")
    ap.add_argument("--path", type=Path, required=True, help="Path to a .lean file or a directory")
    ap.add_argument("--inplace", action="store_true", help="Modify files in place")
    ap.add_argument("--outdir", type=Path, default=None, help="Output directory (if not inplace). Mirrors input structure.")
    ap.add_argument("--preserve-lines", action="store_true", help="Preserve newlines (and replace removed chars with spaces)")
    args = ap.parse_args()

    if not args.path.exists():
        print(f"Path not found: {args.path}")
        return 2

    if not args.inplace and args.outdir is None and args.path.is_dir():
        print("Provide --inplace or --outdir for directory processing")
        return 2

    for src in iter_lean_files(args.path):
        try:
            txt = src.read_text(encoding="utf-8", errors="ignore")
            stripped = strip_lean_comments(txt, preserve_lines=args.preserve_lines)
        except Exception as e:
            print(f"ERROR reading {src}: {e}")
            continue

        if args.inplace:
            try:
                src.write_text(stripped, encoding="utf-8")
                print(f"Stripped (inplace): {src}")
            except Exception as e:
                print(f"ERROR writing {src}: {e}")
        else:
            if args.outdir is None:
                # Single file to stdout
                if args.path.is_file():
                    print(stripped)
                else:
                    print(f"Skipped {src} (no --outdir)")
                continue
            rel = src.relative_to(args.path if args.path.is_dir() else src.parent)
            dst = args.outdir / rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(stripped, encoding="utf-8")
                print(f"Stripped: {src} -> {dst}")
            except Exception as e:
                print(f"ERROR writing {dst}: {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
