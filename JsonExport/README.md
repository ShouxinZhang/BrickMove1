# JsonExport 说明

该目录提供从 JSON 与 Lean 代码之间互相导出/回填的实用脚本，便于批量生成主定理（main theorem statement）块文件、以及将 Lean 文件内容合并回 JSON。

## 脚本概览

- `export_main_statements.py`: 从包含字段 `"main theorem statement"` 的 JSON，导出为顺序编号的 `Block_XXX.lean` 文件。
- `export_from_lean_dir.py`: 从 `Block_XXX.lean` 文件夹生成新的 JSON，或将内容回填到已有模板 JSON 中。
- `insert_main_statements.py`: 读取形如 `formalProof_<id>.lean` 的文件，根据 JSON 中的 `id` 字段，把内容写回到 `"main theorem statement"` 字段。

## 常见工作流

### 1) 从 JSON 导出为 Lean 块

适用于已有 `sfs4_reshape_with_main.json` 并想批量生成 `Block_XXX.lean`：

```bash
python3 JsonExport/export_main_statements.py \
  --input LeanJson/sfs4_reshape_with_main.json \
  --outdir sfs4_new_blocks \
  --start 1 \
  --overwrite
```

- 输出文件形如 `sfs4_new_blocks/Block_001.lean, Block_002.lean, ...`。
- 默认从 1 开始编号；已存在同名文件若未加 `--overwrite` 会被跳过。

VS Code 任务：
- `Export main theorem statements`
  - 等价命令：`python3 JsonExport/export_main_statements.py --input sfs4_reshape_with_main.json --outdir sfs4_new_blocks --overwrite`

### 2) 从 Lean 目录生成/回填 JSON

- 生成全新 JSON：

```bash
python3 JsonExport/export_from_lean_dir.py \
  --indir sfs4_new_blocks \
  --output LeanJson/sfs4_from_lean.json \
  --overwrite
```

- 将 Lean 内容回填到现有模板（按顺序/起始编号对齐）：

```bash
python3 JsonExport/export_from_lean_dir.py \
  --indir sfs4_new_blocks \
  --template LeanJson/sfs4_reshape_with_main.json \
  --output LeanJson/sfs4_reshape_with_main.updated.json \
  --start 1 \
  --overwrite
```

VS Code 任务：
- `Lean→JSON: fresh export`
  - `python3 JsonExport/export_from_lean_dir.py --indir sfs4_new_blocks --output sfs4_from_lean.json --overwrite`
- `Lean→JSON: backfill template`
  - `python3 JsonExport/export_from_lean_dir.py --indir sfs4_new_blocks --template sfs4_reshape_with_main.json --output sfs4_reshape_with_main.updated.json --start 1 --overwrite`

可选参数：
- `--field`: 变更写入字段名，默认 `"main theorem statement"`。
- `--limit N`: 仅处理前 N 个（调试用）。

### 3) 用 `formalProof_<id>.lean` 回填 JSON 的主定理字段

如果你有一批按 `formalProof_<id>.lean` 命名的 Lean 文件（如去注释版本），可以将其内容写回 JSON 中与 `id` 对应的记录：

```bash
python3 JsonExport/insert_main_statements.py \
  --json LeanJson/sfs4_reshape_with_main.json \
  --src-dir build_check_logs/MTS_formalProof_all_stripped \
  --out LeanJson/sfs4_reshape_with_main.updated.json
```

- 用 `--inplace` 可直接覆盖原 JSON。
- 输出会报告成功写入的数量、缺失的 `id`、以及 `id` 不是整数的记录。

## 注意事项

- 路径建议使用仓库根目录为基准，确保脚本中的相对路径有效。
- `export_from_lean_dir.py` 仅识别以 `Block_` 开头、后接编号的 `.lean` 文件。
- 写入 JSON 时统一使用 UTF-8 编码，并保留换行。
- 如果输出文件已存在而未加 `--overwrite`，脚本会终止以防覆盖（或在导出块文件时选择跳过）。

## 相关目录

- `sfs4_new_blocks/`: 导出的 `Block_XXX.lean` 目标目录。
- `LeanJson/`: 放置输入/输出 JSON 的常用位置。
- `build_check_logs/`: 其他工具的构建与检查日志，不直接被本目录脚本修改，但可作为 `insert_main_statements.py` 的 `--src-dir` 来源。

如需把上述步骤做成一键脚本或任务，我可以帮你在 `.vscode/tasks.json` 或 Makefile 中配置。