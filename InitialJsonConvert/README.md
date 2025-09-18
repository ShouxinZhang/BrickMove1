# InitialJsonConvert 使用说明

该子目录包含一个小工具 `convert_initial_json.py`，用于：
- 美化（缩进/保留中文）初始 JSON 文件；
- 将 JSON 里嵌入的 Lean 代码（字段名为 `formalProof`）批量导出为 `.lean` 文件。

## 输入要求
- 输入 JSON 的顶层必须是数组（list）。脚本会在结构不符时直接退出并给出中文错误信息。
- 每个元素通常是一个对象（dict）；若对象中存在键 `formalProof`，其内容将被导出为 Lean 文件。
- 文件命名优先使用以下键作为稳定标识：`id`、`task_id`、`question_id`；若都不存在，则按序号生成。

## 输出位置与命名
运行后会在 `--output-root` 下生成两个子目录：
- `json/<输入文件名>`：美化后的 JSON；
- `formalProofYYYYMMDD_HHMMSS/`：导出的 Lean 文件目录（带时间戳，如 `formalProof20250917_153715`）。

Lean 文件命名规则：
- 形如 `formalProof_<标识>.lean`（例如 `formalProof_123.lean`）；
- 若发生重名，自动添加 `_1`、`_2` 等后缀以避免覆盖；
- 每个文件末尾保证有换行符，便于编辑器显示。

> 注：`InitialJsonConvert/output/` 已在 `.gitignore` 中忽略。

## 快速使用
假设你的输入在 `LeanJson/supple_formal_statement_5.json`：

```bash
python3 InitialJsonConvert/convert_initial_json.py LeanJson/supple_formal_statement_5.json \
  --output-root InitialJsonConvert/output
```

运行完成后终端会提示实际写入位置，例如：

- JSON：`InitialJsonConvert/output/json/supple_formal_statement_5.json`
- Lean：`InitialJsonConvert/output/formalProof20250917_153715/`

## 命令行参数
```text
convert_initial_json.py INPUT_JSON [--output-root OUTPUT_DIR]
```
- `INPUT_JSON`：原始 JSON 文件路径；
- `--output-root`：输出根目录（默认：`InitialJsonConvert/output`）。Lean 文件将写入形如 `formalProofYYYYMMDD_HHMMSS` 的时间戳目录下。

## 与仓库内其他工具的衔接
- 你可以将导出的 Lean 文件作为后续处理的输入。例如，使用 `JsonExport/insert_main_statements.py` 将 `formalProof_<id>.lean` 回填到某个 JSON 的 `"main theorem statement"` 字段：

```bash
python3 JsonExport/insert_main_statements.py \
  --json LeanJson/sfs4_reshape_with_main.json \
  --src-dir InitialJsonConvert/output/formalProof20250917_153715 \
  --out LeanJson/sfs4_reshape_with_main.updated.json
```

> 提醒：`insert_main_statements.py` 需要记录里的 `id` 为整数，并且源目录下存在对应的 `formalProof_<id>.lean` 文件。

## 常见问题
- “无法解析 JSON 文件”：输入文件不是合法 JSON 或编码异常；
- “输入 JSON 的顶层结构必须是数组 (list)。”：确保顶层为数组而非对象；
- “找不到输入文件”：检查路径是否正确。

## 运行环境
- Python 3.8+（仅依赖标准库）。

如需在 README 顶层加入一键示例或配套 VS Code 任务，也可以告诉我，我可以帮你补充。