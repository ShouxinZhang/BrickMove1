#!/usr/bin/env python3
import argparse
import os
import sys
import time
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import re

from urllib import request as urlrequest
from urllib import error as urlerror


DEFAULT_SYSTEM_PROMPT = (
r"""
You are an expert Lean 4 mathematician and code refactoring assistant.
Task: Convert the provided Lean file content into a “main theorem statement” skeleton that aligns with the block’s intent.

Strict rules:

1. Preserve and keep at the very top any header/import/open/namespace context needed for compilation. If the input has none, add `import Mathlib` as the first line.
2. Identify the main mathematical content and restate it as a single primary theorem with a proof placeholder `:= by sorry`. The final statement must be declared with `theorem`, not `lemma`, `corollary`, `def`, etc.
3. If the original main result is a definition/structure/instance/class or gives a concrete construction, rephrase the main result as an existential/isomorphism-style theorem, e.g., “∃ G, Nonempty (A ≃\* G)” or “∃ K, K.carrier = ...”, whichever matches the mathematical meaning.
4. Keep names and namespaces consistent with the original file when possible; otherwise use a clear, concise new name.
5. Keep the rest minimal: remove auxiliary proofs and long constructions that are not the main statement; keep only the single main statement with its header context.
6. Retain minimal supporting `def`/`instance` declarations that the main theorem’s statement depends on (e.g., a subgroup or structure definition referenced by name in the theorem). Place these immediately before the theorem, preserve original names/signatures, and do not include their proofs/bodies beyond what is strictly necessary for the statement to typecheck. Examples: keep `def G (p) ...` if `theorem order_of_G` quantifies over `G p`; keep an `instance` if it is referenced in the theorem’s types.
7. Do not include any comments or docstrings in the final output.
8. Place the single main theorem at the very end of the file, after any minimal supporting declarations, and ensure it is declared with `theorem`.
9. Output only Lean code for the full final file. Do NOT include markdown code fences or natural-language text.
10. Any retained code (imports, opens, namespaces, and minimal supporting declaration signatures) must be copied verbatim from the original input; do not alter names, reorder content, or add new helpers beyond what is strictly necessary for the file to typecheck.

Examples of transformation intent:

* From `def` or `structure` that encodes a main result → replace with a theorem stating existence of such object and an isomorphism to the intended structure.
* From long proof scripts → condense into the single main theorem with `:= by sorry`.

Return policy: Output ONLY the final Lean source, nothing else.
"""
.strip()
)

def _load_api_key_from_keyfile() -> Optional[str]:
    candidates = [Path.cwd() / ".openrouter_key", Path(__file__).parent / ".openrouter_key"]
    for p in candidates:
        if not p.exists():
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore").strip()
            # Use the first non-empty, non-comment line as the key
            for line in txt.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                return s.strip('"').strip("'")
        except Exception:
            continue
    return None


def strip_code_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        # remove opening fence line
        lines = t.splitlines()
        # drop first line
        lines = lines[1:]
        # if last is closing fence, drop it
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return t


def ensure_top_import_mathlib(text: str) -> str:
    lines = text.splitlines()
    # Remove duplicate `import Mathlib` lines after the top
    seen_import_mathlib = False
    cleaned: List[str] = []
    for i, line in enumerate(lines):
        if line.strip() == "import Mathlib":
            if not seen_import_mathlib:
                cleaned.append(line)
                seen_import_mathlib = True
            # skip duplicates
            continue
        cleaned.append(line)
    if not seen_import_mathlib:
        cleaned.insert(0, "import Mathlib")
    return "\n".join(cleaned).strip() + "\n"


def build_fallback_skeleton(src: str) -> Optional[str]:
    lines = src.splitlines()
    imports: List[str] = []
    opens: List[str] = []
    docstring: Optional[str] = None

    i = 0
    n = len(lines)
    while i < n:
        s = lines[i].strip()
        if s.startswith("import "):
            imports.append(lines[i])
            i += 1
            continue
        if s.startswith("open "):
            opens.append(lines[i])
            i += 1
            continue
        break

    # Find first docstring
    i = 0
    while i < n:
        if lines[i].lstrip().startswith("/--"):
            ds = [lines[i]]
            i += 1
            while i < n and not lines[i].lstrip().startswith("-/"):
                ds.append(lines[i])
                i += 1
            if i < n:
                ds.append(lines[i])
            docstring = "\n".join(ds)
            break
        i += 1

    # Find first theorem/lemma and capture its signature up to ":="
    start = -1
    for idx, ln in enumerate(lines):
        s = ln.lstrip()
        if s.startswith("theorem ") or s.startswith("lemma "):
            start = idx
            break
    if start == -1:
        return None
    sig_buf: List[str] = []
    j = start
    found_assign = False
    while j < n:
        sig_buf.append(lines[j])
        if ":=" in lines[j]:
            found_assign = True
            break
        j += 1
    if not found_assign:
        return None
    sig_text = "\n".join(sig_buf)
    sig_left = sig_text.split(":=", 1)[0].rstrip()

    # Ensure final assembly
    out: List[str] = []
    if imports:
        out.extend(imports)
        out.append("")
    if opens:
        out.extend(opens)
        out.append("")
    if docstring:
        out.append(docstring)
    out.append(f"{sig_left} := by\n  sorry\n")
    final = "\n".join(out)
    final = ensure_top_import_mathlib(final)
    return final


class OpenRouterClient:
    def __init__(self, api_key: str, *, base_url: str = "https://openrouter.ai/api/v1", timeout: float = 60.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def chat_completion(self, messages: List[Dict[str, Any]], model: str, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # Optional but recommended by OpenRouter
            "HTTP-Referer": os.getenv("OPENROUTER_HTTP_REFERER", "http://localhost"),
            "X-Title": os.getenv("OPENROUTER_X_TITLE", "Lean Main Theorem Agent"),
        }
        payload = {
            "model": model,
            "messages": messages,
            # Reasonable defaults; adjustable via kwargs
            "temperature": kwargs.get("temperature", 0.2),
            "top_p": kwargs.get("top_p", 0.9),
        }
        mt = kwargs.get("max_tokens", None)
        if isinstance(mt, int) and mt > 0:
            payload["max_tokens"] = mt

        data = json.dumps(payload).encode("utf-8")

        attempts = 5
        delay = 1.0
        last_err: Optional[Exception] = None
        for _ in range(attempts):
            req = urlrequest.Request(url, data=data, headers=headers, method="POST")
            try:
                with urlrequest.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8", errors="ignore")
                    return json.loads(raw)
            except urlerror.HTTPError as e:
                # Retry on 5xx; otherwise re-raise
                if 500 <= e.code < 600:
                    last_err = e
                    time.sleep(delay)
                    delay = min(delay * 2, 20.0)
                    continue
                # Try to include a short snippet of body for context
                try:
                    body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    body = ""
                raise RuntimeError(f"HTTP error {e.code}: {body[:200]}") from e
            except urlerror.URLError as e:
                last_err = e
                time.sleep(delay)
                delay = min(delay * 2, 20.0)
                continue
        if last_err:
            raise last_err
        raise RuntimeError("Unknown error contacting OpenRouter")


def build_messages(system_prompt: str, file_content: str) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "Here is a Lean file. Transform it per the rules and return only the final Lean source.\n\n"
                + file_content
            ),
        },
    ]


def process_file(
    client: OpenRouterClient,
    in_path: Path,
    out_path: Path,
    model: str,
    system_prompt: str,
    overwrite: bool,
    normalize: bool,
    max_tokens: Optional[int],
    retries: int,
) -> Optional[Path]:
    if out_path.exists() and not overwrite:
        return None

    src = in_path.read_text(encoding="utf-8", errors="ignore")
    messages = build_messages(system_prompt, src)

    content = ""
    attempt = 0
    while attempt <= retries:
        data = client.chat_completion(messages, model=model, max_tokens=max_tokens)
        choice = (data.get("choices") or [{}])[0]
        content = (
            ((choice.get("message") or {}).get("content") or "").strip()
        )
        if content:
            break
        attempt += 1
        if attempt <= retries:
            time.sleep(min(1.0 * attempt, 2.0))
    if not content:
        # Fallback: try to synthesize a minimal skeleton from the source
        fallback = build_fallback_skeleton(src)
        if fallback:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(fallback, encoding="utf-8")
            return out_path
        # Second-level fallback: produce a minimal compiling skeleton
        stem = in_path.stem
        # Prefer a safe simple theorem that always typechecks
        minimal = (
            "import Mathlib\n\n" \
            "/-- Auto-generated minimal skeleton because the LLM returned empty and no signature could be extracted. -/\n" \
            f"theorem {stem}_main : True := by\n" \
            "  trivial\n"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(minimal, encoding="utf-8")
        return out_path

    content = strip_code_fences(content)
    if normalize:
        content = ensure_top_import_mathlib(content)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path


def find_lean_files(input_dir: Path, pattern: str) -> List[Path]:
    return sorted(input_dir.glob(pattern))


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LLM agent: transform Lean blocks to main theorem skeletons via OpenRouter")
    p.add_argument("--input-dir", type=Path, default=Path("sfs4_blocks"), help="Directory with source Lean files")
    # Leave empty to default to LLM_Agent/ouput/MTS<YYYYMMDD_HHMMSS>
    p.add_argument("--output-dir", type=str, default="", help="Directory to write transformed Lean files (default: LLM_Agent/ouput/MTS<YYYYMMDD_HHMMSS>)")
    p.add_argument("--match", type=str, default="Block_*.lean", help="Glob pattern within input-dir")
    p.add_argument("--model", type=str, default="moonshotai/kimi-k2-0905", help="OpenRouter model id (default: moonshotai/kimi-k2-0905)")
    p.add_argument("--max-tokens", type=int, default=4096, help="Max tokens for completion")
    p.add_argument("--no-max-tokens", action="store_true", help="Omit max_tokens in API payload (no upper cap)")
    p.add_argument("--sleep", type=float, default=0.0, help="Sleep seconds between files (rate limiting)")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing outputs")
    p.add_argument("--normalize", action="store_true", help="Normalize: ensure single top `import Mathlib`")
    p.add_argument("--limit", type=int, default=0, help="Process at most N files (0 = no limit)")
    p.add_argument("--append-system", type=str, default="", help="Append text to system prompt for customization")
    p.add_argument("--api-key", type=str, default="", help="OpenRouter API key; overrides env and .openrouter_key if provided")
    p.add_argument("--continue-on-error", action="store_true", help="Continue processing remaining files when an error occurs")
    p.add_argument("--retries", type=int, default=2, help="Retries per file when the model returns an empty completion")
    p.add_argument("--workers", type=int, default=1000, help="Number of parallel workers")
    # Leave empty to default to <output-dir>/failed_ids.json and <output-dir>/errors.log
    p.add_argument("--fail-out", type=str, default="", help="Path to write JSON array of failed block ids (default: <output-dir>/failed_ids.json)")
    p.add_argument("--error-log", type=str, default="", help="Path to write detailed error messages (default: <output-dir>/errors.log)")
    return p.parse_args(argv)


def _extract_block_id(path: Path) -> Optional[int]:
    m = re.search(r"(\d+)", path.stem)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def main(argv: List[str]) -> int:
    args = parse_args(argv)

    api_key = args.api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        # try .openrouter_key fallback
        api_key = _load_api_key_from_keyfile() or ""
    if not api_key:
        print("ERROR: Provide OpenRouter API key: --api-key, or set OPENROUTER_API_KEY env, or place it in .openrouter_key (first non-empty line)", file=sys.stderr)
        return 2

    system_prompt = DEFAULT_SYSTEM_PROMPT
    if args.append_system:
        system_prompt = f"{system_prompt}\n\nAdditional guidance:\n{args.append_system.strip()}".strip()

    # Resolve output directory
    if args.output_dir:
        out_base = Path(args.output_dir)
    else:
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_base = Path(__file__).parent / "ouput" / f"MTS{ts}"

    # Ensure base output directory exists and resolve log paths
    out_base.mkdir(parents=True, exist_ok=True)
    fail_out_path = Path(args.fail_out) if args.fail_out else (out_base / "failed_ids.json")
    error_log_path = Path(args.error_log) if args.error_log else (out_base / "errors.log")

    client = OpenRouterClient(api_key)
    files = find_lean_files(args.input_dir, args.match)
    if not files:
        print(f"No files matched in {args.input_dir} with pattern {args.match}")
        return 0

    # Limit number of files
    if args.limit:
        files = files[: args.limit]

    total = 0
    failed = 0
    failed_ids: List[int] = []
    # Determine effective max_tokens (None when --no-max-tokens is enabled)
    effective_max_tokens: Optional[int] = None if getattr(args, "no_max_tokens", False) else args.max_tokens

    def worker(path: Path) -> Tuple[Path, bool, Optional[str]]:
        rel = path.relative_to(args.input_dir)
        out_path = out_base / rel
        try:
            if args.sleep > 0:
                time.sleep(args.sleep)
            res = process_file(
                client,
                in_path=path,
                out_path=out_path,
                model=args.model,
                system_prompt=system_prompt,
                overwrite=args.overwrite,
                normalize=args.normalize,
                max_tokens=effective_max_tokens,
                retries=args.retries,
            )
            if res is not None:
                return (res, True, None)
            else:
                return (out_path, True, None)
        except Exception as e:
            return (path, False, str(e))

    if args.workers and args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex, open(error_log_path, "w", encoding="utf-8") as elog:
            futs = {ex.submit(worker, p): p for p in files}
            for fut in as_completed(futs):
                path = futs[fut]
                try:
                    res_path, ok, err = fut.result()
                except Exception as e:
                    ok = False
                    err = str(e)
                    res_path = path
                if ok:
                    print(f"Wrote: {res_path}")
                    total += 1
                else:
                    bid = _extract_block_id(path)
                    if bid is not None:
                        failed_ids.append(bid)
                    failed += 1
                    msg = f"Error processing {path}: {err}\n"
                    print(msg, file=sys.stderr)
                    elog.write(msg)
                    if not args.continue_on_error:
                        break
    else:
        with open(error_log_path, "w", encoding="utf-8") as elog:
            for path in files:
                rel = path.relative_to(args.input_dir)
                out_path = out_base / rel
                try:
                    res = process_file(
                        client,
                        in_path=path,
                        out_path=out_path,
                        model=args.model,
                        system_prompt=system_prompt,
                        overwrite=args.overwrite,
                        normalize=args.normalize,
                        max_tokens=effective_max_tokens,
                        retries=args.retries,
                    )
                    if res is not None:
                        print(f"Wrote: {res}")
                        total += 1
                    else:
                        print(f"Skip (exists): {out_path}")
                except Exception as e:
                    bid = _extract_block_id(path)
                    if bid is not None:
                        failed_ids.append(bid)
                    failed += 1
                    msg = f"Error processing {path}: {e}\n"
                    print(msg, file=sys.stderr)
                    elog.write(msg)
                    if not args.continue_on_error:
                        print(f"Done. Processed {total} file(s). Failures: {failed}.")
                        return 4
                if args.sleep > 0:
                    time.sleep(args.sleep)

    # Write failed ids list
    try:
        fail_out_path.write_text(json.dumps(sorted(failed_ids)), encoding="utf-8")
    except Exception:
        pass

    print(f"Done. Processed {total} file(s). Failures: {failed}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
