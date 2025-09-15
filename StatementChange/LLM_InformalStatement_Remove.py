#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量调用 OpenRouter Chat Completions（默认模型 x-ai/grok-code-fast-1），并行处理 `.lean` 文件：
1) 删除注释；2) 基于注释语义重命名 theorem/lemma/def。
支持通过命令行与 `StatementChange/model_config.json` 配置并行数、输入/输出长度、系统提示与历史对话。
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple, Any

try:
    import aiohttp
    from aiohttp import ClientTimeout
except ImportError:
    aiohttp = None  # type: ignore

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None  # type: ignore

OPENROUTER_DEFAULT_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_ENDPOINT = "/chat/completions"

SYSTEM_PROMPT_ZH = (
    "你是一名 Lean4 代码重构助手。请对给定的 Lean4 源码执行两件事：\n"
    "1) 移除所有注释（包含行注释 `-- ...` 与块注释 `/- ... -/`，也包括文档注释 `/-- ... -/`）。\n"
    "2) 检查每个 theorem/lemma/def 的命名，尽量依据其紧邻或对应的原注释语义，\n"
    "   将标识符改为更贴切、简洁的一致风格。保持参数与证明主体结构不变。\n"
    "要求：仅输出修改后的 Lean 源代码文本，不要附加解释或Markdown标记；保持 import 与 set_option 等设置不变；\n"
    "当你重命名标识符时，务必在同一文件内同步定义与所有引用；若无法推断更好名称，则保留原名。\n"
)

USER_PROMPT_TPL = (
    "请对下列 Lean4 文件执行上述转换：\n"
    "【文件路径】: {path}\n"
    "【源码开始】\n"
    "{code}\n"
    "【源码结束】\n"
    "请直接返回新的 Lean 源文件全文。"
)

BLOCK_COMMENT_RE = re.compile(r"/-[\s\S]*?-/", re.MULTILINE)
LINE_COMMENT_RE = re.compile(r"(^|\s)--.*?$", re.MULTILINE)


def strip_lean_comments(src: str) -> str:
    no_block = re.sub(BLOCK_COMMENT_RE, "", src)
    no_line = re.sub(LINE_COMMENT_RE, "", no_block)
    return no_line


def find_lean_files(root: Path, include_glob: str = "**/*.lean") -> List[Path]:
    return sorted([p for p in root.glob(include_glob) if p.is_file()])


async def call_openrouter(
    session: Any,
    api_key: str,
    model: str,
    base_url: str,
    endpoint: str,
    system_prompt: str,
    user_prompt: str,
    timeout: int = 120,
    temperature: float = 0.0,
    top_p: float = 1.0,
    max_tokens: int = 0,
    history: Optional[List[dict]] = None,
) -> str:
    url = base_url.rstrip("/") + endpoint
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "http://localhost",
        "X-Title": "BrickMove1 Lean Batch Refactor",
        "Content-Type": "application/json",
    }
    messages: List[dict] = [{"role": "system", "content": system_prompt}]
    if history:
        for m in history:
            try:
                r = m.get("role")
                c = m.get("content")
                if r in ("system", "user", "assistant") and isinstance(c, str):
                    messages.append({"role": r, "content": c})
            except Exception:
                continue
    messages.append({"role": "user", "content": user_prompt})

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "stream": False,
    }
    if max_tokens and max_tokens > 0:
        payload["max_tokens"] = max_tokens

    async with session.post(url, headers=headers, json=payload, timeout=ClientTimeout(total=timeout)) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"OpenRouter API 请求失败: HTTP {resp.status} | {text}")
        data = await resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            raise RuntimeError(f"解析 OpenRouter 返回失败: {e}\n原始返回: {json.dumps(data)[:500]}...")


async def process_one_file(
    path: Path,
    outdir: Optional[Path],
    inplace: bool,
    api_key: Optional[str],
    model: str,
    base_url: str,
    endpoint: str,
    semaphore: asyncio.Semaphore,
    session: Optional[Any],
    dry_run: bool,
    local_strip_only: bool,
    timeout: int,
    temperature: float,
    top_p: float,
    max_tokens: int,
    max_input_chars: int,
    system_prompt: str,
    history: Optional[List[dict]],
) -> Tuple[Path, Optional[Path], Optional[str]]:
    src = path.read_text(encoding="utf-8")

    dst_path = path if inplace else (outdir / path.name) if outdir else None

    if dry_run or local_strip_only:
        new_code = strip_lean_comments(src)
        if dry_run:
            return path, None, None
        if dst_path:
            dst_path.write_text(new_code, encoding="utf-8")
            return path, dst_path, None
        return path, None, "未指定输出位置"

    if aiohttp is None:
        return path, None, "缺少依赖 aiohttp，请先安装依赖或使用 --local-strip-only"
    if api_key is None:
        return path, None, "未设置 OPENROUTER_API_KEY 环境变量"

    code_for_llm = src[:max_input_chars] if (max_input_chars and max_input_chars > 0) else src
    user_prompt = USER_PROMPT_TPL.format(path=str(path), code=code_for_llm)
    async with semaphore:
        assert session is not None
        try:
            content = await call_openrouter(
                session=session,
                api_key=api_key,
                model=model,
                base_url=base_url,
                endpoint=endpoint,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                timeout=timeout,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                history=history,
            )
        except Exception as e:
            return path, None, str(e)

    if dst_path is None:
        return path, None, "未指定输出位置"
    dst_path.write_text(content, encoding="utf-8")
    return path, dst_path, None


async def main_async(args) -> int:
    root = Path(args.dir).resolve()
    if not root.exists():
        print(f"目录不存在: {root}", file=sys.stderr)
        return 2

    files = find_lean_files(root)
    if not files:
        print("未找到 .lean 文件", file=sys.stderr)
        return 1

    outdir: Optional[Path] = None
    if args.inplace:
        outdir = None
    else:
        outdir = Path(args.outdir).resolve() if args.outdir else (root.parent / f"{root.name}_llm")
        outdir.mkdir(parents=True, exist_ok=True)

    if tqdm is not None:
        _ = tqdm(files, desc="Processing", unit="file")

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        key_file = Path(__file__).parent.parent / ".openrouter_key"
        if key_file.exists():
            try:
                for line in key_file.read_text(encoding="utf-8").splitlines():
                    s = line.strip()
                    if not s or s.startswith('#'):
                        continue
                    api_key = s
                    break
            except Exception:
                pass

    # 读取本地配置覆盖
    cfg_path = Path(__file__).parent / "model_config.json"
    system_prompt_effective = None  # type: Optional[str]
    history_cfg: Optional[List[dict]] = None
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(cfg, dict):
                if "model" in cfg:
                    args.model = cfg.get("model", args.model)
                if "temperature" in cfg:
                    args.temperature = float(cfg["temperature"])  # type: ignore[attr-defined]
                if "top_p" in cfg:
                    args.top_p = float(cfg["top_p"])  # type: ignore[attr-defined]
                if "max_concurrency" in cfg:
                    args.max_concurrency = int(cfg["max_concurrency"])  # type: ignore[attr-defined]
                if "max_input_chars" in cfg:
                    args.max_input_chars = int(cfg["max_input_chars"])  # type: ignore[attr-defined]
                if "max_tokens" in cfg:
                    args.max_tokens = int(cfg["max_tokens"])  # type: ignore[attr-defined]
                if isinstance(cfg.get("system_prompt"), str) and cfg["system_prompt"].strip():
                    system_prompt_effective = cfg["system_prompt"].strip()
                if isinstance(cfg.get("history"), list):
                    history_cfg = cfg["history"]
        except Exception as e:
            print(f"警告：读取 model_config.json 失败：{e}", file=sys.stderr)

    # 覆盖系统提示：命令行文件优先
    if getattr(args, "system_prompt_file", None):
        spf = Path(args.system_prompt_file)
        if spf.exists():
            try:
                system_prompt_effective = spf.read_text(encoding="utf-8").strip() or system_prompt_effective
            except Exception as e:
                print(f"警告：读取 system_prompt_file 失败：{e}", file=sys.stderr)

    system_prompt_final = system_prompt_effective or SYSTEM_PROMPT_ZH

    # 会话
    session: Optional[Any] = None
    if not (args.dry_run or args.local_strip_only):
        if aiohttp is None:
            print("缺少 aiohttp 依赖，无法执行网络调用。", file=sys.stderr)
            return 3
        timeout = ClientTimeout(total=args.timeout)
        session = aiohttp.ClientSession(timeout=timeout)

    sem = asyncio.Semaphore(args.max_concurrency)

    try:
        tasks = [
            process_one_file(
                path=f,
                outdir=outdir,
                inplace=args.inplace,
                api_key=api_key,
                model=args.model,
                base_url=args.base_url,
                endpoint=args.endpoint,
                semaphore=sem,
                session=session,
                dry_run=args.dry_run,
                local_strip_only=args.local_strip_only,
                timeout=args.timeout,
                temperature=args.temperature,
                top_p=args.top_p,
                max_tokens=args.max_tokens,
                max_input_chars=args.max_input_chars,
                system_prompt=system_prompt_final,
                history=history_cfg,
            )
            for f in files
        ]
        results = [await coro for coro in asyncio.as_completed(tasks)]
    finally:
        if session is not None:
            await session.close()

    ok = 0
    failed = 0
    for src_path, dst_path, err in results:
        if err:
            failed += 1
            print(f"[失败] {src_path.name}: {err}", file=sys.stderr)
        else:
            ok += 1
            if dst_path is not None:
                print(f"[写入] {dst_path}")

    print(f"完成: 成功 {ok} 个，失败 {failed} 个。")
    return 0 if failed == 0 else 4


def parse_args(argv: Optional[List[str]] = None):
    p = argparse.ArgumentParser(description="批量调用 OpenRouter 处理 Lean 文件：去注释 + 调整 theorem 命名")
    p.add_argument("--dir", required=True, help="输入目录（例如 sfs4_new_blocks）")
    p.add_argument("--model", default="x-ai/grok-code-fast-1", help="OpenRouter 模型 ID（默认：x-ai/grok-code-fast-1）")
    p.add_argument("--base-url", default=OPENROUTER_DEFAULT_BASE, help="OpenRouter API Base URL")
    p.add_argument("--endpoint", default=OPENROUTER_DEFAULT_ENDPOINT, help="Chat Completions 端点路径")
    p.add_argument("--max-concurrency", type=int, default=8, help="并发请求上限")
    p.add_argument("--timeout", type=int, default=120, help="每个请求的超时时间（秒）")
    p.add_argument("--temperature", type=float, default=0.0, help="采样温度（默认 0.0）")
    p.add_argument("--top-p", dest="top_p", type=float, default=1.0, help="核采样阈值top_p（默认 1.0）")
    p.add_argument("--max-input-chars", dest="max_input_chars", type=int, default=0, help="输入最大字符数（0为不限制）")
    p.add_argument("--max-tokens", dest="max_tokens", type=int, default=0, help="LLM输出最大tokens（0为不限制）")
    p.add_argument("--system-prompt-file", dest="system_prompt_file", help="系统提示文件路径（可选）")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--inplace", action="store_true", help="原地覆盖写回（谨慎使用）")
    g.add_argument("--outdir", help="输出目录（默认：<dir>_llm）")
    p.add_argument("--dry-run", action="store_true", help="干跑：不写文件，仅验证遍历流程")
    p.add_argument("--local-strip-only", action="store_true", help="仅本地去注释，不调用 LLM")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
    
