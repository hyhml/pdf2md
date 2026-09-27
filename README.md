# pdf2md

一个可作为 Codex skill 安装的本地分层 PDF 转 Markdown 工具。简单文件走快速路径；低置信度、扫描件或复杂版面使用 Docling，输出带明确 PDF 页码标记的结构化 Markdown。

## 处理层级

| 层级 | 工具 | 用途 | 输出 |
|---|---|---|---|
| 0 | pypdf | PDF 已有可靠文字层 | 纯文本 Markdown |
| 1 | RapidOCR + ONNX Runtime | 普通扫描件、只需轻量文字识别 | 纯文本 Markdown |
| 2 | Docling + RapidOCR | 低置信度、目录、表格、标题、脚注或复杂版面 | 带 PDF 页码的结构化 Markdown |

自动模式先检查文字层，再检查轻量 OCR 的覆盖率、置信度、异常字符和复杂版面；质量门槛未通过时升级到 Docling。每次处理都会写出 `*.report.json`，记录实际选择的层级和升级原因。

## 安装为 Codex skill

```bash
git clone https://github.com/hyhml/pdf2md.git ~/.codex/skills/pdf2md
cd ~/.codex/skills/pdf2md
scripts/setup.sh --all
```

也可分开安装：

```bash
scripts/setup.sh --light
scripts/setup.sh --strong
```

- 轻量环境位于 `.venv/`。
- Docling 独立环境位于 `.venv-docling/`，避免与轻量 OCR 依赖冲突。
- Linux 下 setup 会先安装 CPU 版 PyTorch，再安装 Docling。
- Docling 首次转换会下载版面模型；模型缓存在 skill 的 `.cache/` 下。

## 使用

检查运行环境：

```bash
scripts/pdf2md doctor --json
```

使用自动路由：

```bash
scripts/pdf2md scan.pdf -o scan.md --json
```

明确使用 Docling：

```bash
scripts/pdf2md scan.pdf -o scan.md --mode strong --json
```

批量处理：

```bash
scripts/pdf2md batch ./pdfs -o ./markdown --recursive --mode strong --json
```

强解析采用以下本地配置：

- Docling standard pipeline
- RapidOCR 中文识别
- full-page OCR
- 表格结构识别
- CPU 运行
- 图片占位符模式

强解析结果为每个 PDF 页面加入：

```markdown
<!-- PDF_PAGE: 1 -->

# PDF 第 1 页
```

这样可以从 Markdown 直接追溯到原 PDF 页码。

## 输出

以 `scan.pdf` 为例：

- `scan.md`：最终 Markdown。
- `scan.md.report.json`：路由层级、质量指标、状态和错误信息。
- `scan.light.md`：只有自动路由要求强解析但 Docling 不可用时才会保留的候选文本；它不是验证后的最终结果。

## 开发检查

```bash
python3 -m venv .venv-test
.venv-test/bin/pip install -e ".[light,dev]"
.venv-test/bin/pytest
```

真实 Docling 冒烟测试：

```bash
scripts/setup.sh --strong
PDF2MD_CLI="$PWD/.venv/bin/pdf2md" scripts/pdf2md sample.pdf \
  -o sample.md --mode strong --json
```

## 基准测试

`benchmarks/` 保存真实扫描 PDF 的工具横向测试报告、未经人工修改的工具输出和统一页码副本。虚拟环境、模型缓存和原始 PDF 不提交到仓库。
