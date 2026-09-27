# pdf2md

这是一个可作为 Codex skill 安装的本地分层 PDF 文字提取工具：简单文件尽量走轻量路径，只有结果不可靠或版面复杂时才调用 MinerU。

## 作为 Codex skill 安装

仓库根目录就是 skill 根目录。安装后应能看到：

```text
~/.codex/skills/pdf2md/SKILL.md
```

可使用 Codex 的 skill 安装器从本仓库安装，或手动克隆：

```bash
git clone https://github.com/hyhml/pdf2md.git ~/.codex/skills/pdf2md
cd ~/.codex/skills/pdf2md
scripts/setup.sh --light
```

安装完整本地强力层及标准模型：

```bash
scripts/setup.sh --strong --models standard
```

标准模型需要额外下载约数 GB 数据。执行后可通过 `$pdf2md` 显式调用；普通 PDF 转 Markdown/OCR 请求也可自动触发该 skill。

## 分层策略

| 层级 | 工具 | 用途 | 输出 |
|---|---|---|---|
| 0 | pypdf | PDF 已有可靠文字层时直接提取，速度最快 | 纯文本 Markdown |
| 1 | RapidOCR + ONNX Runtime | 扫描件只做文字检测、方向判断和识别 | 纯文本 Markdown |
| 2 | MinerU 4.x | 低置信度、疑似多栏、表格或表单 | 结构化 Markdown |

轻量层默认采用 RapidOCR 3.9+ 内置的 PP-OCRv6 small 检测与识别模型。它不做表格重建、图片提取或格式还原，只保留文字。每次处理都会生成一个 `*.report.json`，记录实际选择的层级和判断依据。

自动路由顺序：

1. 检查现有文字层的页覆盖率、字符数量和乱码比例。
2. 不合格时，以 220 DPI 渲染并用 RapidOCR 识别。
3. 检查加权平均置信度、低置信文字占比、文字覆盖率和异常字符。
4. 若质量不足，或检测到疑似多栏/表格版面，升级到 MinerU。
5. 需要升级但 MinerU 不可用时，不会把低质量结果冒充成功；候选文本会保存为 `*.light.md`，命令返回错误。

## 安装

轻量层建议放在独立虚拟环境中：

```bash
cd pdf2md
python3 -m venv .venv
.venv/bin/pip install -e ".[light]"
.venv/bin/pdf2md doctor
```

首次安装或检查 RapidOCR：

```bash
.venv/bin/rapidocr check
```

强力层单独安装 MinerU 4.x，并确保 `mineru-kit` 或 `mineru` 命令位于 `PATH`。使用 `uv tool` 可以避免与轻量环境发生依赖冲突：

```bash
uv tool install "mineru>=4.0,<5"
mineru version --json
```

也可以像本项目当前配置一样，把 MinerU 安装在仓库根目录的
`.venv-mineru` 中；路由器会自动发现。其他自定义位置可通过
`PDF_OCR_MINERU_BIN` 指向可执行文件或其所在目录。

若使用项目内模型目录，可执行：

```bash
MINERU_HOME="$PWD/.mineru" \
  .venv-mineru/bin/mineru-kit models download --tier standard --source modelscope
```

只准备 OCR 和基础解析、希望节省磁盘时，可把 `standard` 改成 `basic`；
本工具默认的强力层是 `standard`。

MinerU 模型体积和算力需求明显高于轻量层，因此默认只在质量门槛触发时调用。

## 使用

处理单个文件：

```bash
scripts/pdf2md scan.pdf
scripts/pdf2md process scan.pdf -o output.md
```

批量处理：

```bash
scripts/pdf2md batch ./pdfs -o ./markdown --recursive
```

强制指定层级：

```bash
scripts/pdf2md scan.pdf --mode light
scripts/pdf2md scan.pdf --mode strong
```

调整判断门槛：

```bash
scripts/pdf2md scan.pdf \
  --min-confidence 0.88 \
  --max-low-confidence 0.15 \
  --dpi 240
```

如果只关心全文文字、能接受多栏页按行交错，可关闭版面升级：

```bash
scripts/pdf2md scan.pdf --no-layout-escalation
```

## 输出说明

以 `scan.pdf` 为例：

- `scan.md`：最终文本或 Markdown。
- `scan.md.report.json`：路由层级、质量指标和升级原因。
- `scan.light.md`：只有需要升级但 MinerU 不可用时产生，是待人工检查的候选文本。

默认阈值是稳健起点，不是通用真值。建议取一批自己的扫描件做抽样校对，再根据报告里的置信度分布调整参数。

## 开发检查

```bash
.venv/bin/pip install -e ".[light,dev]"
.venv/bin/pytest
```
