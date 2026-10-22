# pdfoenix

把**扫描版 PDF**（尤其是数学密集的古旧书）重建为现代排版的 **EPUB**。

核心目标不是复刻原书版式，而是：

- **文字绝对准确**（唯一硬指标）
- 公式 LaTeX 忠实、保留原书编号
- 图、表、公式可寻址、可复核、可重做
- 成本可记账、质量可对比，方便持续做成本-质量实验

设计细节见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 特性

- **确定性优先**：渲染、侦察、合并、校验、构建全部是纯脚本；LLM 只出现在“看图转写”和“看图复核”。
- **文件即状态**：所有中间产物、注册表、日志落盘，中断可续跑。
- **页锚点**：转写阶段写入 `<!-- page:N -->`，merge 后建立公式/图/表与页图的双向索引。
- **对象模型**：图 = 裁图文件；表 = Markdown；公式 = 可寻址 LaTeX 对象。
- **成本账本**：每次 LLM 调用记录模型、思考档、text/vision/output token，可 JOIN 纠错记录。
- **对抗式复核**：转写与复核默认使用不同模型；按风险抽页，抽中整页全核。
- **可插拔**：`Transcriber` 协议、抽样策略、风险权重、阈值与概率都是配置项。

## 安装

要求 Python 3.12+，并安装 `pandoc`（用于生成 EPUB）。

```bash
pip install -e .
```

模型调用使用本地 [OpenCode](https://opencode.ai/)：

```bash
npx opencode --version
# 或使用已全局安装的 opencode
```

`rich` 用于 `pdfoenix watch` 看板；未安装时自动回退为纯文本。

## 快速开始

```bash
# 1. 创建一本书的目录、配置和预设
pdfoenix init books/my_book \
  --pdf path/to/source.pdf \
  --title "Book Title" \
  --author "Author" \
  --page-offset 0

cd books/my_book

# 2. 侦察结构并渲染页面
pdfoenix scout
pdfoenix render

# 3. 分批转写
pdfoenix transcribe

# 4. 合并为章节 Markdown，并注册公式/图/表
pdfoenix merge

# 5. 裁图（可选；复核页 300 dpi 重渲染由 verify 自动完成）
pdfoenix assets

# 6. 抽样复核 + 全局审计
pdfoenix verify

# 7. 校验、构建、验收
pdfoenix check
pdfoenix build
pdfoenix inspect
```

所有命令都接受全局 `-C/--book`，也可以在书目录内直接运行：

```bash
pdfoenix -C books/an_introduction check
```

## 常用命令

| 命令 | 作用 |
|------|------|
| `pdfoenix init <book> --pdf a.pdf` | 脚手架：目录、`config.toml`、预设、空注册表 |
| `pdfoenix scout` | 扫描 PDF 文字层 → `state/scout.json` |
| `pdfoenix render` | 渲染 150 dpi 页图 → `working/pages/` |
| `pdfoenix transcribe` | 分页批转写 → `working/batches/` |
| `pdfoenix merge` | 合并批次、切节、注册对象 → `manuscript/` |
| `pdfoenix verify` | 风险抽样复核；`--full` 全量复核 |
| `pdfoenix assets` | 图裁图；自动检测或使用注册表里的 `crop` |
| `pdfoenix check` | 编号、脚注、链接、注册表、残留检查 |
| `pdfoenix build` | 拼装 → pandoc → EPUB → 后处理 |
| `pdfoenix inspect` | EPUB 结构验收（nav/spine/MathML/章末注） |
| `pdfoenix watch` | 只读实时看板 |
| `pdfoenix report --by model\|chapter` | 成本汇总 |
| `pdfoenix experiment --by model --metric cost,error_rate` | 成本-质量对比 |
| `pdfoenix calibrate` | 公式/页风险分布报告 |

## 目录结构

```text
pdfoenix/                 # 包源代码
├── core/                 # 确定性内核（无 LLM）
├── transcribe/           # Transcriber 协议 + OpenCode 适配
├── verify/               # 抽样、页内容恢复、全局审计
├── assets/               # 图裁图、复核页渲染
├── presets/              # epub.css / opencode.jsonc / 任务书 / 单价 / 默认配置
├── cli.py                # 命令编排
├── scaffold.py           # init
├── report.py             # 成本/实验报告
├── calibrate.py          # 风险分布
├── inspect.py            # EPUB 验收
└── watch.py              # 看板
books/
└── an_introduction/      # 第一个测试对象/回归 fixture
tests/                    # 单元与集成测试
```

一本书的目录：

```text
<book>/
├── config.toml
├── presets/              # init 复制的预设
├── known_dangling.txt    # 合法悬空锚点白名单
├── styles/epub.css
├── source/               # PDF、封面
├── manuscript/           # chNN/00_chapter.md、secX-Y.md、tabX-Y.md
├── figures/              # figX-Y.png
├── working/              # pages/、verify/、zoom/、batches/
├── state/                # scout/formulas/figures/tables/corrections/progress
├── logs/                 # pipeline.log、pipeline.db
├── build/                # book.md、*.epub
└── legacy/               # 旧人工流水线归档（不参与构建）
```

## 配置

`config.toml` 只需写与默认值不同的项，缺失项由 `pdfoenix/presets/config.schema.toml` 补齐。关键配置：

```toml
[book]
title = "Book Title"
author = "Author"
pdf = "source/book.pdf"
page_offset = 18          # PDF 页 = 印刷页 + offset

[pipeline]
batch_pages = 5
render_dpi = 150
verify_dpi = 300
verify_chunk_size = 5

[models.transcribe]
model = "provider/model"
thinking = "default"

[models.verify]
model = "provider/other-model"   # 必须与 transcribe 不同
thinking = "default"

[sampling]
page_risk = "max"
budget = "20%"           # 硬上限；概率目标约 10%
low_max = 50
high_min = 125
low_prob = 0.01
medium_prob = 0.09
high_prob = 0.60
seed = 0
```

风险分档来自实测标定：公式风险中位数 28、P90 77；页风险分档 `≤50 / 51–125 / >125`，对应抽样概率 `1% / 9% / 60%`，期望约 10% 页，`budget` 仅作硬封顶。

## 开发

```bash
pytest
ruff check pdfoenix tests
pylint --disable=all --enable=duplicate-code pdfoenix
```

- 代码注释与 docstring 使用英文。
- 每个模块配套测试；改注释/重构后必须 `pytest` 全绿。
- 禁止 `\tag{}`、裸 `■`、无锚点标题；见 [ARCHITECTURE.md](ARCHITECTURE.md) §14。

## 仓库说明

`books/an_introduction` 是第一个真实测试对象，保留：

- 已验证的 `manuscript/`、`figures/`、`build/`
- 重新索引的 `state/formulas.json`（1531 条公式）与 `state/progress.json`
- `legacy/` 下的旧人工流水线文档、批次、手工复核图

大体积且可再生的文件（页图、legacy zoom、日志、构建缓存）已在 `.gitignore` 中排除。
