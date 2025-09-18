#!/usr/bin/env python3
import argparse
import os
import sys
import time
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Sequence, Tuple
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
5. Keep the rest minimal: remove auxiliary lemma that are not the main statement; keep only the single main statement with its header context.
6. Retain minimal supporting `def`/`instance` declarations that the main theorem’s statement depends on (e.g., a subgroup or structure definition referenced by name in the theorem). Place these immediately before the theorem, preserve original names/signatures, and do not include their proofs/bodies beyond what is strictly necessary for the statement to typecheck. Examples: keep `def G (p) ...` if `theorem order_of_G` quantifies over `G p`; keep an `instance` if it is referenced in the theorem’s types.
7. Do not include any comments or docstrings in the final output.
8. Place the single main theorem at the very end of the file, after any minimal supporting declarations, and ensure it is declared with `theorem`.
9. Output only Lean code for the full final file. Do NOT include markdown code fences or natural-language text.
10. Any retained code (imports, opens, namespaces, and minimal supporting declaration signatures) must be copied verbatim from the original input; do not alter names, reorder content, or add new helpers beyond what is strictly necessary for the file to typecheck.

Examples of transformation intent:

* From `def` or `structure` that encodes a main result → replace with a theorem stating existence of such object and an isomorphism to the intended structure.
* From long proof scripts → condense into the single main theorem with `:= by sorry`.

Return policy: Output ONLY the final Lean source, nothing else.

关于def或者instance的，可以将其替换为\exists X : xxxtype, X.carrier = xxx, 或者X.toFun = xxx, 或者 X = xxx 
也可以Nonempty (XXX) 例子有:
/-- The algebra isomorphism between multivariable polynomials in `Fin (n + 1)` and
polynomials over multivariable polynomials in `Fin n`.
-/
noncomputable def finSuccEquiv (R : Type u) [CommSemiring R] (n : ℕ) :
  MvPolynomial (Fin (n + 1)) R ≃ₐ[R] Polynomial (MvPolynomial (Fin n) R) :=
    MvPolynomial.finSuccEquiv R n
--->
import Mathlib
theorem finSuccEquiv1 (R : Type u) [CommSemiring R] (n : ℕ) :
  Nonempty (MvPolynomial (Fin (n + 1)) R ≃ₐ[R] Polynomial (MvPolynomial (Fin n) R)) := by sorry
还有
/-- Prove that `R` is an integral domain. -/
instance : IsDomain R := Subring.instIsDomainSubtypeMem R
-- 》
theorem test : Nonempty (IsDomain R) := by sorry
还有:
def initialZ : IsInitial (RingCat.of ℤ) := by
  -- Use the helper method 'ofUniqueHom' to establish the initial object by providing the unique homomorphism.
  refine IsInitial.ofUniqueHom ?_ ?_
  -- For an arbitrary ring R', define the canonical ring homomorphism from ℤ to R'
  intro R'
  let tof : ℤ →+* R' := by
    exact Int.castRingHom R'
  -- Convert the homomorphism to the form expected by RingCat using 'ofHom'.
  exact ofHom tof
  -- To prove uniqueness, assume any homomorphism g and show it equals the canonical one.
  intro R g
  apply RingCat.hom_ext
  ext r
  simp only [eq_intCast, hom_ofHom]

/--
-- The terminal object in the category of rings is the zero ring.
-- We represent the zero ring using `PUnit` (with a shifted universe level) and show that
-- for any ring R', there is a unique ring homomorphism from R' to the zero ring.
-/
def terminalzero : IsTerminal (RingCat.of PUnit.{u + 1}) := by
  -- Again, use 'ofUniqueHom' to construct the terminal object by providing the unique homomorphism.
  refine IsTerminal.ofUniqueHom ?_ ?_
  -- For any ring R', construct the unique homomorphism to the zero ring using 'RingHom.smulOneHom'.
  intro R'
  let tof : R' →+* PUnit := by
    exact RingHom.smulOneHom
  -- Convert this homomorphism to the appropriate form using 'ofHom'.
  exact ofHom tof
  -- To show uniqueness, assume any homomorphism g and prove that it is the same as the canonical one.
  intro R' g
  apply RingCat.hom_ext
  ext r
-->
theorem terminalzero : Nonempty (IsTerminal (RingCat.of PUnit.{u + 1})) := by sorry
还有
def conj_in_aut {G : Type*} [Group G] (a : G) : G ≃* G := (MulAut.conj a)
-->
def conj_in_aut {G : Type*} [Group G] (a : G) : ∃ f : G ≃* G, f.toFun = (MulAut.conj a) := by sorry
"""
.strip()
)

VALID_MESSAGE_ROLES = {"system", "user", "assistant"}

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


def load_fewshot_messages(path: Optional[Path]) -> List[Dict[str, str]]:
    if path is None:
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Few-shot JSON not found: {path}") from exc
    except OSError as exc:
        raise OSError(f"Unable to read few-shot JSON {path}: {exc}") from exc

    try:
        data = json.loads(raw) if raw.strip() else []
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError(f"Few-shot JSON must contain a list, got {type(data).__name__}")

    normalized: List[Dict[str, str]] = []
    skipped = 0
    for idx, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError(f"Entry at index {idx} is not an object: {entry!r}")
        role = entry.get("role")
        content = entry.get("content", "")
        if role not in VALID_MESSAGE_ROLES:
            skipped += 1
            continue
        if not isinstance(content, str):
            content = "" if content is None else str(content)
        normalized.append({"role": role, "content": content})

    if skipped:
        print(f"Warning: skipped {skipped} unsupported message role(s) in {path}", file=sys.stderr)
    return normalized


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
                # Retry on 429 (rate limit) and 5xx
                if e.code == 429 or (500 <= e.code < 600):
                    last_err = e
                    retry_after = 0.0
                    try:
                        # Honor standard and OpenRouter-specific headers
                        ra = e.headers.get("Retry-After") if hasattr(e, "headers") else None
                        if ra:
                            retry_after = float(ra)
                        else:
                            xrr = e.headers.get("X-RateLimit-Reset") if hasattr(e, "headers") else None
                            if xrr:
                                # Could be epoch or seconds; best-effort parse
                                try:
                                    val = float(xrr)
                                    # If it's a timestamp in the future, convert to delta
                                    now = time.time()
                                    retry_after = max(0.0, val - now) if val > 1e6 else val
                                except Exception:
                                    retry_after = 0.0
                    except Exception:
                        retry_after = 0.0
                    sleep_for = max(retry_after, delay)
                    try:
                        print(f"WARN: HTTP {e.code} from OpenRouter; retrying in {sleep_for:.1f}s", file=sys.stderr)
                    except Exception:
                        pass
                    time.sleep(sleep_for)
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


FEWSHOT_USER_EXAMPLE = (
    r"""
import Mathlib

/--
Let $G$ be a group and $H$ a subgroup of $G$.
If for all $a, b \in G$, the implication $(aH = bH \implies Ha = Hb)$ holds,
then $H$ is a normal subgroup of $G$.
The condition $aH = bH \implies Ha = Hb$ is equivalent to $a^{-1}b \in H \implies ab^{-1} \in H$.
-/
lemma normal_subgroup_of_coset_implication
    {G : Type*} [Group G] (H : Subgroup G)
    -- Precondition: For all $a, b \in G$, if $a^{-1}b \in H$, then $ab^{-1} \in H$.
    (hyp : ∀ a b : G, (a⁻¹ * b ∈ H) → (a * b⁻¹ ∈ H)) :
    -- Conclusion: H is a normal subgroup of G
    H.Normal := by
  -- Goal: Prove H is normal by proving the `conj_mem` property.
  -- Use refine' to focus on the core field `conj_mem` of H.Normal.
  refine' { conj_mem := ?_ }
  -- Current subgoal: $\forall h \in H, \forall g \in G, g h g^{-1} \in H$.
  intro h h_in_H g
  -- Current subgoal: For the given $h \in H$ and $g \in G$, prove $g h g^{-1} \in H$.

  -- Construct elements a and b to apply the hypothesis `hyp`.
  let a : G := g
  let h_inv : G := h⁻¹
  -- Subgoal: Prove $h^{-1} \in H$. Needed for the premise of `hyp`.
  have h_inv_in_H : h_inv ∈ H := H.inv_mem h_in_H
  let b : G := g * h_inv

  -- Subgoal: Prove $a^{-1}b \in H$. This is the premise needed to apply `hyp`.
  -- First establish the equality $a^{-1}b = h^{-1}$.
  have eq_calc : a⁻¹ * b = h_inv := by
    calc
      a⁻¹ * b = g⁻¹ * (g * h_inv) := rfl
      _ = (g⁻¹ * g) * h_inv := by rw [mul_assoc]
      _ = 1 * h_inv         := by simp -- simp applies $g^{-1}g = 1$
      _ = h_inv             := by rw [one_mul]
  -- Now prove $a^{-1}b \in H$ using the equality and $h^{-1} \in H$.
  have a_inv_b_in_H : a⁻¹ * b ∈ H := by
    rw [eq_calc]
    exact h_inv_in_H

  -- Subgoal: Prove $ab^{-1} \in H$. This follows directly from applying `hyp` to `a_inv_b_in_H`.
  have a_b_inv_in_H : a * b⁻¹ ∈ H := hyp a b a_inv_b_in_H

  -- Subgoal: Prove $ab^{-1} = g h g^{-1}$. This connects the result from `hyp` to the final goal.
  have calc_a_b_inv : a * b⁻¹ = g * h * g⁻¹ := by
    calc
      a * b⁻¹ = g * (g * h_inv)⁻¹ := rfl
      _ = g * ( h_inv⁻¹ * g⁻¹ ) := by rw [mul_inv_rev]
      _ = g * ( h * g⁻¹ ) := by rw [inv_inv h] -- Uses $(h^{-1})^{-1} = n$
      _ = g * h * g⁻¹ := by rw [mul_assoc]

  -- Conclude the proof using $ab^{-1} = g h g^{-1}$ and $ab^{-1} \in H$.
  rw [calc_a_b_inv] at a_b_inv_in_H
  exact a_b_inv_in_H

/--when H is not the subgroup of G then aH=bH, then  follow that Ha≠Hb-/
theorem contrapositive_of_coset_implication {G : Type*} [Group G] (H : Subgroup G) :
    (¬ H.Normal) →
    (∃ a b : G, (a⁻¹ * b ∈ H) ∧ (a * b⁻¹ ∉ H)) := by
    --contrapositive
    contrapose!
    intro hyp_from_contra
    --recall normal_subgroup_of_coset_implication
    exact normal_subgroup_of_coset_implication H hyp_from_contra
"""
).strip()

FEWSHOT_ASSISTANT_EXAMPLE = (
        r"""
import Mathlib

theorem contrapositive_of_coset_implication {G : Type*} [Group G] (H : Subgroup G) :
  (¬ H.Normal) →
  (∃ a b : G, (a⁻¹ * b ∈ H) ∧ (a * b⁻¹ ∉ H)) := by
  sorry
"""
).strip()


def build_messages(
    system_prompt: str,
    file_content: str,
    *,
    include_fewshot: bool = False,
    extra_turns: Optional[Sequence[Dict[str, str]]] = None,
) -> List[Dict[str, str]]:
    msgs: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if extra_turns:
        for entry in extra_turns:
            role = entry.get("role") if isinstance(entry, dict) else None
            content = entry.get("content") if isinstance(entry, dict) else None
            if role not in VALID_MESSAGE_ROLES:
                continue
            if not isinstance(content, str):
                content = "" if content is None else str(content)
            msgs.append({"role": role, "content": content})
    if include_fewshot:
        msgs.append(
            {
                "role": "user",
                "content": (
                    "Here is a Lean file. Transform it per the rules and return only the final Lean source.\n\n"
                    + FEWSHOT_USER_EXAMPLE
                ),
            }
        )
        msgs.append({"role": "assistant", "content": FEWSHOT_ASSISTANT_EXAMPLE})
    msgs.append(
        {
            "role": "user",
            "content": (
                "Here is a Lean file. Transform it per the rules and return only the final Lean source.\n\n"
                + file_content
            ),
        }
    )
    return msgs


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
    include_fewshot: bool,
    fewshot_turns: Optional[Sequence[Dict[str, str]]],
) -> Optional[Path]:
    if out_path.exists() and not overwrite:
        return None

    src = in_path.read_text(encoding="utf-8", errors="ignore")
    messages = build_messages(
        system_prompt,
        src,
        include_fewshot=include_fewshot,
        extra_turns=fewshot_turns,
    )

    content = ""
    attempt = 0
    last_error: Optional[Exception] = None
    while attempt <= retries:
        try:
            data = client.chat_completion(messages, model=model, max_tokens=max_tokens)
        except Exception as exc:
            last_error = exc
            attempt += 1
            if attempt <= retries:
                time.sleep(min(1.0 * attempt, 2.0))
                continue
            break
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
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(src, encoding="utf-8")
        try:
            if last_error is not None:
                print(f"WARN: No completion returned for {in_path}; preserved original file. Last error: {last_error}", file=sys.stderr)
            else:
                print(f"WARN: No completion returned for {in_path}; preserved original file.", file=sys.stderr)
        except Exception:
            pass
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
    # Leave empty to default to LLM_Agent/output/MTS<YYYYMMDD_HHMMSS>
    p.add_argument("--output-dir", type=str, default="", help="Directory to write transformed Lean files (default: LLM_Agent/output/MTS<YYYYMMDD_HHMMSS>)")
    p.add_argument("--match", type=str, default="**/*.lean", help="Glob pattern within input-dir (supports '**' for recursion)")
    p.add_argument("--model", type=str, default="openai/gpt-5", help="OpenRouter model id (default: openai/gpt-5)")
    p.add_argument("--max-tokens", type=int, default=0, help="Max tokens for completion (default: unlimited)")
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
    p.add_argument("--fewshot", action="store_true", help="Prepend a priming user/assistant example conversation to guide the model")
    p.add_argument(
        "--fewshot-json",
        type=Path,
        default=None,
        help="Path to JSON file with additional conversation turns to prepend",
    )
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

    fewshot_turns: List[Dict[str, str]] = []
    use_builtin_fewshot = bool(args.fewshot)
    if args.fewshot_json:
        json_path = args.fewshot_json
        json_path = json_path.expanduser()
        if not json_path.is_absolute():
            json_path = json_path.resolve()
        try:
            fewshot_turns = load_fewshot_messages(json_path)
        except Exception as exc:  # pragma: no cover - CLI safeguard
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        use_builtin_fewshot = False

    # Resolve output directory
    if args.output_dir:
        out_base = Path(args.output_dir)
    else:
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_base = Path(__file__).parent / "output" / f"MTS{ts}"

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
    # Determine effective max_tokens: None when --no-max-tokens is enabled or when --max-tokens <= 0
    if getattr(args, "no_max_tokens", False):
        effective_max_tokens = None
    else:
        effective_max_tokens = args.max_tokens if (isinstance(args.max_tokens, int) and args.max_tokens > 0) else None

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
                include_fewshot=use_builtin_fewshot,
                fewshot_turns=fewshot_turns,
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
                        include_fewshot=use_builtin_fewshot,
                        fewshot_turns=fewshot_turns,
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
