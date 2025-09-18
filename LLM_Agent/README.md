# LLM_Agent 使用说明（中文）

本目录包含用于处理 Lean 4 代码块的 LLM 工具链：
- 将 Lean 代码块提炼为“主定理骨架”的生成器（llm_agent.py）
- 对编译失败的 Lean 文件进行自动回写与二次构建的复检器（llm_recheck_agent.py）
- 去除 Lean 注释的小工具（strip_comments.py）
- 统一管理脚本（manager.py）：一键管理“生成 + 复检”流水线

适用场景：当你有一批 Lean 代码块（如 `sfs4_new_blocks` 中的 `Block_*.lean`），希望用 LLM 自动生成仅包含“主定理声明”的最小可编译骨架，并对无法通过编译的文件进行自动回写与复检。

---

## 快速开始

1) 准备 OpenRouter API Key（任选一种）
- 环境变量：`export OPENROUTER_API_KEY=你的Key`
- 或在仓库根/本目录放置 `.openrouter_key` 文件，第一行写入 Key（支持忽略空行和以 `#` 开头的注释）。

2) 运行主定理骨架生成器（将输入目录替换为你的 Lean 代码目录）
```bash
# 例：处理仓库中的 sfs4_new_blocks
python3 LLM_Agent/llm_agent.py \
  --input-dir sfs4_new_blocks \
  --match "Block_*.lean" \
  --overwrite \
  --normalize \
  --fewshot \
  --workers 16
```
- 输出默认写入：`LLM_Agent/output/MTS<时间戳>/Block_XXX.lean`

3) 对生成结果做并行构建检查并自动回写失败文件
```bash
python3 LLM_Agent/llm_recheck_agent.py \
  --target-dir LLM_Agent/output/MTS<时间戳> \
  --pattern "*.lean" \
  --workers 16 \
  --normalize \
  --second-build-scope failed
```
- 复检日志与报告写入：`build_check_logs/recheck_<unix_ts>/`
  - `recheck_summary.json`：总体结果
  - `backups/`：回写前的备份

---

## 目录与脚本说明

- `llm_agent.py`
  - 功能：调用 OpenRouter，将 Lean 文件转换为仅包含“主定理声明”的最小可编译骨架。
  - 关键行为：
    - 顶部确保 `import Mathlib` 出现且仅出现一次（启用 `--normalize` 时）。
    - 允许使用 few-shot 引导（`--fewshot` 或 `--fewshot-json`）。
    - 当模型返回空内容时，内置回退：尝试从原文件提取首个 lemma/theorem 的签名生成骨架；仍失败则生成保底的 `theorem <stem>_main : True := by trivial`。

- `llm_recheck_agent.py`
  - 功能：
    1. 并行构建指定目录中的 Lean 文件；
    2. 对构建失败的文件调用 OpenRouter 重新生成完整文件（可用 `:= by sorry` 简化，保证类型检查通过）；
    3. 二次构建（可选择仅构建失败文件或全部文件），并将最终结果与日志写入 `build_check_logs/`。
  - 关键行为：
    - 回写前自动备份；如二次构建仍失败，会尝试恢复原始内容。

- `strip_comments.py`
  - 功能：去除 Lean 源码中的行注释 `--` 与块注释 `/- -/`（含文档注释 `/-- -/`）。
  - 支持保留换行、紧凑空行等选项，便于在不破坏行号的前提下净化输入。

- `output/`、`PromptExample/`
  - `output/`：`llm_agent.py` 的默认输出目录（自动按时间戳分桶）。
  - `PromptExample/`：提示词示例（若有）。

- `manager.py`
  - 功能：统一管理生成与复检流程，支持一键流水线操作。
  - 关键行为：
    - `pipeline` 模式下，自动依次调用 `llm_agent.py` 与 `llm_recheck_agent.py`，并传递参数。
    - 支持仅执行“生成”或“复检”阶段。

---

## 依赖与环境

- Python 3.9+
- 访问 OpenRouter API 的网络与账号
- Lean 4 / Mathlib 构建环境（用于复检阶段的实际编译）
  - 仓库根部通常已有 `lean-toolchain`、`lakefile.lean`；请确保本机安装 Lean/Lake 并可构建。

可选：建议使用虚拟环境（如 `python -m venv .venv && source .venv/bin/activate`）。

---

## llm_agent.py 常用参数

- `--input-dir`：输入 Lean 文件目录（默认 `sfs4_blocks`，本仓库通常为 `sfs4_new_blocks`）。
- `--match`：Glob 模式（默认 `Block_*.lean`）。
- `--output-dir`：输出目录（默认 `LLM_Agent/output/MTS<时间戳>`）。
- `--model`：OpenRouter 模型（默认 `moonshotai/kimi-k2-0905`）。
- `--max-tokens` / `--no-max-tokens`：限制返回 Token 数或不限制。
- `--sleep`：每个文件之间的延时（限速友好）。
- `--overwrite`：允许覆盖已有输出文件。
- `--normalize`：标准化输出，确保顶部仅一个 `import Mathlib`。
- `--limit`：最多处理 N 个文件。
- `--append-system`：向系统提示词追加自定义说明。
- `--api-key`：直接传入 API Key（优先级高于环境变量与 `.openrouter_key`）。
- `--continue-on-error`：遇错继续处理其它文件。
- `--retries`：当模型返回空结果时的重试次数（默认 2）。
- `--workers`：并行线程数（注意服务端速率限制）。
- `--fewshot`：启用内置 few-shot 示例。
- `--fewshot-json`：从 JSON 文件加载额外对话轮（`[{"role": "user|assistant|system", "content": "..."}, ...]`）。
- `--fail-out` / `--error-log`：失败 id 列表与详细错误日志保存路径（默认写到输出目录）。

---

## llm_recheck_agent.py 常用参数

- `--target-dir`：目标 Lean 文件目录（默认自动选择 `LLM_Agent/output` 下最新的 `MTS*` 目录）。
- `--pattern`：Glob 模式（默认 `*.lean`）。
- `--workers`：并行构建/回写线程数（默认 50）。
- `--model`：回写模型（默认 `openai/gpt-5`）。
- `--max-tokens` / `--no-max-tokens`：同上。
- `--normalize`：回写时确保顶部仅一个 `import Mathlib`。
- `--retries`：模型空返回或错误时的重试次数（默认 1）。
- `--append-system`：附加系统提示词。
- `--api-key`：OpenRouter API Key。
- `--second-build-scope`：二次构建范围：`all`（全部）或 `failed`（仅失败，默认）。

输出与日志：
- `build_check_logs/recheck_<unix_ts>/recheck_summary.json` 汇总本次两轮构建、回写情况；
- `.../backups/` 保存回写前备份；
- `.../recheck_errors.log`（若存在）记录回写阶段的错误。

---

## manager.py 用法示例

使用 `manager.py` 可以统一调用生成与复检流程：

- 一键流水线（生成 + 复检）
```bash
python3 LLM_Agent/manager.py pipeline \
  --input-dir sfs4_new_blocks \
  --match "Block_*.lean" \
  --normalize --overwrite \
  --gen-fewshot \
  --gen-workers 16 \
  --recheck-workers 16 \
  --second-build-scope failed
```

- 仅生成
```bash
python3 LLM_Agent/manager.py generate \
  --input-dir sfs4_new_blocks \
  --match "Block_*.lean" \
  --normalize --overwrite \
  --fewshot --workers 16
```

- 仅复检（对已有 MTS 输出目录）
```bash
LATEST_DIR=$(ls -td LLM_Agent/output/MTS* | head -n1)
python3 LLM_Agent/manager.py recheck \
  --target-dir "$LATEST_DIR" \
  --pattern "*.lean" \
  --normalize \
  --workers 16 \
  --second-build-scope failed
```

参数说明：
- `pipeline` 支持分别配置生成阶段（以 `--gen-*` 前缀）与复检阶段（以 `--recheck-*` 前缀）的参数。
- 若不提供 `--output-dir`，流水线会自动选择最新的 `LLM_Agent/output/MTS*` 作为复检输入。

---

## strip_comments.py 用法示例

- 对单文件就地去注释：
```bash
python3 LLM_Agent/strip_comments.py --path sfs4_new_blocks/Block_001.lean --inplace --preserve-lines
```

- 对目录输出到新位置（保留目录结构）：
```bash
python3 LLM_Agent/strip_comments.py \
  --path sfs4_new_blocks \
  --outdir /tmp/lean_stripped \
  --preserve-lines \
  --compact-blank-lines 1
```

参数要点：
- `--preserve-lines`：去除注释时保留换行，其它字符以空格占位，便于行号对应。
- `--remove-blank-lines`：移除空白行。
- `--compact-blank-lines N`：最多保留 N 个连续空行（N=0 等价于移除所有空行）。

---

## 常见问题

- API Key 未配置：脚本会给出报错提示；按“快速开始”第 1 步配置。
- 速率限制/5xx：使用 `--sleep`、降低 `--workers`，或更换模型；错误详情见 `--error-log` 文件。
- 编译环境问题：确保本地 Lean/Lake 可用，并在仓库根部进行构建测试。
- 仍无法生成有效骨架：脚本会回退到最小可编译骨架；必要时使用 `--append-system` 追加更具体的约束。

---

## 参考示例（一键流程）

```bash
# 1) 生成主定理骨架
python3 LLM_Agent/llm_agent.py \
  --input-dir sfs4_new_blocks \
  --match "Block_*.lean" \
  --overwrite --normalize --fewshot --workers 16

# 2) 复检并自动回写失败文件
LATEST_DIR=$(ls -td LLM_Agent/output/MTS* | head -n1)
python3 LLM_Agent/llm_recheck_agent.py \
  --target-dir "$LATEST_DIR" \
  --pattern "*.lean" \
  --workers 16 --normalize --second-build-scope failed
```

如需更多自定义，请查看脚本内的参数说明与源码注释。
