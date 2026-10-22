# pdfoenix

**简体中文** · [English](README.en.md)

这个项目的目的是将**扫描版 PDF**重建为现代排版的 **EPUB** 格式（如果你看过那种来个上个世纪的出版物，
你一定会同意这个项目是十分必要的），这也正是PDF Phoenix（pdfoenix）所代表的含义。

设计这个项目时，我想我们无需原封不动的复刻书中的设计和排版，既然是再版，不妨放开手脚，所以在转录时，
项目将使用现代排版的规范。但书籍的内容不会遭到扭曲，我们会尽最大的努力确保文字、公式、图、表的准确。
设计细节见 [ARCHITECTURE.md](ARCHITECTURE.md)。

[特性](#特性) · [安装](#安装) · [快速开始](#快速开始) · [用法](#用法) · [配置](#配置) · [开发](#开发)

![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python&logoColor=white)
![uv](https://img.shields.io/badge/uv-package%20manager-8A2BE2)
![pandoc](https://img.shields.io/badge/pandoc-EPUB-7a4f1d)
![status](https://img.shields.io/badge/status-alpha-orange)

---

## 它是什么

`pdfoenix` 是一条把**扫描版 PDF**（尤其是数学密集的旧书）重建为**现代排版 EPUB** 的命令行流水线。

它不追求逐像素复刻原书，而是追求：

- **文字绝对准确** —— 唯一的硬指标
- **公式忠实** —— LaTeX 转写，保留原书编号
- **对象可寻址** —— 图、表、公式都能定位、复核、重做
- **成本可记账** —— 每次模型调用都入账，方便做成本-质量实验

## 特性

| 特性           | 说明                                                                         |
|----------------|------------------------------------------------------------------------------|
| **确定性优先** | 渲染、侦察、合并、校验、构建全是纯脚本，LLM 只负责「看图转写」和「看图复核」 |
| **文件即状态** | 中间产物、注册表、日志全部落盘，中断后可续跑                                 |
| **页锚点**     | 转写时写入 `<!-- page:N -->`，建立公式/图/表与页图的双向索引                 |
| **对抗式复核** | 转写与复核使用不同模型，按风险抽页，抽中即整页全核                           |
| **可插拔**     | `Transcriber` 协议、抽样策略、风险权重、阈值与概率均为配置项                 |

## 当前进度

- `init/scout/render/transcribe/merge/verify/assets/check/build/inspect/watch/report/experiment/calibrate` 全部可用。
- 抽样已加入绝对/相对下限：公式稀疏的书也会随机补齐到 `min_pages` / `min_rate`，不会几乎不抽。
- `books/an_introduction` 已完成状态重建：1531 条公式、章节进度、悬空锚点白名单。
- `pdfoenix check` 无错误；`pdfoenix build` 可生成 EPUB；`pdfoenix inspect` 通过。
- 全量 `pytest`、`ruff`、duplicate-code 检查通过。

## 工作原理

```text
PDF
 │
 ├─ scout      扫描文字层，识别章/节/图/表/公式
 ├─ render     渲染页面为图片
 ├─ transcribe 视觉模型逐页转写 → Markdown + LaTeX
 ├─ merge      合并批次、切节、注册对象
 ├─ assets     裁图（可选）
 ├─ verify     另一模型抽样复核
 ├─ check      本地规则校验
 ├─ build      pandoc 生成 EPUB
 └─ inspect    检查 EPUB 结构
```

## 安装

### 依赖

| 依赖                             | 用途           | 要求                          |
|----------------------------------|----------------|-------------------------------|
| Python                           | 运行流水线     | 3.12+                         |
| [uv](https://docs.astral.sh/uv/) | 环境与依赖管理 | 最新版                        |
| pandoc                           | 生成 EPUB      | 3.x                           |
| Node.js + OpenCode               | 调用视觉模型   | Node 20+                      |
| 模型网关                         | 转写 / 复核    | 任意 OpenCode 支持的 provider |

> PDF 需带**文字层**（供 `scout` 侦察结构）；纯图片扫描件请先 OCR，如 `ocrmypdf input.pdf output.pdf`。

### 一键配置环境

```bash
git clone https://github.com/<your-name>/pdfoenix.git
cd pdfoenix
./start.sh
source .venv/bin/activate
```

默认配置来自 MiMo、DeepSeek 的实验结果，力图在尽可能低的成本下取得尽可能高的质量。
这是一个权衡结果，而不是质量最优解。如果你想要更好的质量，可以换用更强的模型或其它 agent。
本项目配置项较多；如果你决定更换模型，可以委托智能体修改配置并协助运行。

`start.sh` 只负责环境配置，不运行书稿流程：安装 `uv`、创建 `.venv`（Python 3.12）、
安装依赖，并在缺失时自动安装 `pandoc` 与 `opencode`。

### 配置模型（项目本地）

pdfoenix 使用**每本书自己的** `presets/opencode.jsonc`，不会要求你修改
`~/.config/opencode`。`pdfoenix init` 会从包内模板复制一份到
`books/my_book/presets/opencode.jsonc`。

Key 推荐放进环境变量：

```bash
export MIMO_API_KEY="your-key"
```

然后编辑 `books/my_book/presets/opencode.jsonc`，把
`providers.mimo.settings.baseURL` 改成你的网关地址。
也可以直接把 `apiKey` 写成明文；`books/` 已被 `.gitignore` 忽略，不会误提交。

已有书如果还是旧模板，可覆盖一次：

```bash
cp pdfoenix/presets/opencode.jsonc books/my_book/presets/opencode.jsonc
```

## 快速开始

```bash
# 1. 创建一本书
pdfoenix init books/my_book \
  --pdf /path/to/book.pdf \
  --title "Book Title" \
  --author "Author" \
  --page-offset 18

# 2. 先只跑一章，确认无误
pdfoenix -C books/my_book scout
pdfoenix -C books/my_book transcribe --chapter ch01
pdfoenix -C books/my_book merge --chapter ch01
pdfoenix -C books/my_book verify --chapter ch01 --full
pdfoenix -C books/my_book build

# 3. 确认后跑完整本书
pdfoenix -C books/my_book transcribe
pdfoenix -C books/my_book merge
pdfoenix -C books/my_book assets
pdfoenix -C books/my_book verify
pdfoenix -C books/my_book check
pdfoenix -C books/my_book build
pdfoenix -C books/my_book inspect
```

产物在 `books/my_book/build/book.epub`。

### 可选：封面

如果有封面，直接放进书的 `source/` 目录即可：

```text
books/my_book/source/cover.jpg
```

`build` 会自动把 `source/cover.*` 作为 EPUB 封面，不需要改配置。支持 `.jpg`、`.jpeg`、`.png`
等格式；请只放一个封面文件，排序后的第一个会被使用。

> 所有阶段幂等，中断后重跑同一命令即可续跑；`--force` 才会重做已完成的部分。

## 用法

### `-C / --book`

`-C`（等价于 `--book`）用于指定**要操作的书目录**，默认是当前目录 `.`。

```bash
# 等价写法
cd books/my_book && pdfoenix check
pdfoenix -C books/my_book check
```

有了 `-C`，你可以从仓库任意位置、甚至脚本里操作任意一本书，不必先 `cd` 进去。

### 命令一览

| 命令                                                       | 作用                                                   |
|------------------------------------------------------------|--------------------------------------------------------|
| `pdfoenix init <book> --pdf a.pdf`                         | 脚手架：目录、`config.toml`、预设、空注册表            |
| `pdfoenix scout`                                           | 扫描 PDF 文字层 → `state/scout.json`                   |
| `pdfoenix render`                                          | 渲染页图 → `working/pages/`（可选，transcribe 会补缺） |
| `pdfoenix transcribe [--chapter ch01] [--force]`           | 分批转写 → `working/batches/`                          |
| `pdfoenix merge [--chapter ch01]`                          | 合并、切节、注册对象 → `manuscript/`                   |
| `pdfoenix assets [--force] [--no-auto]`                    | 裁图；自动检测或使用注册表 `crop`                      |
| `pdfoenix verify [--full\|--pages 19-23] [--chapter ch01]` | 抽样复核；`--full` 全量复核                            |
| `pdfoenix check`                                           | 编号、脚注、链接、注册表、残留检查                     |
| `pdfoenix build [--out book.epub]`                         | 拼装 → pandoc → EPUB → 后处理                          |
| `pdfoenix inspect [--epub path]`                           | EPUB 结构验收                                          |
| `pdfoenix watch`                                           | 只读实时看板                                           |
| `pdfoenix report --by model\|chapter`                      | 成本汇总                                               |
| `pdfoenix experiment --by model --metric cost,error_rate`  | 成本-质量对比                                          |
| `pdfoenix calibrate`                                       | 公式/页风险分布报告                                    |

每个命令可用 `pdfoenix <command> --help` 查看完整参数。

## 配置

每本书只有一个 `config.toml`，只写与默认值不同的项，缺省项由 `presets/config.schema.toml` 补齐。

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
model = "mimo/mimo-v2.6-flash"
thinking = "default"

[models.verify]
model = "mimo/mimo-v2.6-pro"   # 必须与 transcribe 不同
thinking = "default"

[sampling]
page_risk = "max"
budget = "20%"           # 硬上限；概率目标约 10%
low_max = 50
high_min = 125
low_prob = 0.01
medium_prob = 0.09
high_prob = 0.60
min_pages = 10           # 绝对下限：公式稀疏的书也至少抽这么多页
min_rate = 0.10          # 相对下限：至少抽候选页的 10%
seed = 0
```

### 两个 agent 的模型与思考强度

- `transcribe` 使用 `[models.transcribe]`
- `verify` 使用 `[models.verify]`

适配器会把 `model` + `thinking` 拼成 `provider/model#variant`，通过
`opencode run --model ...` 传给对应 agent，所以两个 agent 天然可以使用不同模型。

`thinking = "default"` 或 `"none"` 表示不附加 variant；其他值会变成 `#<value>`。
例如 `thinking = "high"` 会生成 `mimo/mimo-v2.6-pro#high`。

> 具体有哪些 variant 由模型和网关决定。当前 MiMo 网关只接受 `default`；
> 设置 `high` / `low` / `medium` / `max` 会得到 `Variant unavailable`。

风险分档来自实测标定：页风险 `≤50 / 51–125 / >125` 对应抽样概率 `1% / 9% / 60%`，
期望约 10% 页，`budget` 仅作硬封顶。

抽样有“下限 + 上限”：先按风险概率抽页；若不足
`max(min_pages, ceil(min_rate × 候选页数))`，从剩余页中随机补齐；候选页总数少于
该下限时全部抽取。这样公式稀疏的书也不会因为风险带为空而几乎不抽样。

## 成本与质量

```bash
pdfoenix -C books/my_book report --by model     # 按模型看 token / 成本
pdfoenix -C books/my_book report --by chapter   # 按章看 token / 成本
pdfoenix -C books/my_book experiment --by model --metric cost,error_rate
pdfoenix -C books/my_book watch                 # 实时看板
```

每次模型调用都会记入 `logs/pipeline.db`（模型、思考档、输入/视觉/输出 token、耗时、状态）。

> `cost_usd` 由 `presets/pricing.toml` 的单价计算；未配置单价的模型显示 0，但 token 数始终真实。

## 项目结构

```text
pdfoenix/          # 包源代码
├── core/          # 确定性内核（无 LLM）
├── transcribe/    # Transcriber 协议 + OpenCode 适配
├── verify/        # 抽样、页内容恢复、全局审计
├── assets/        # 图裁图、复核页渲染
├── presets/       # epub.css / opencode.jsonc / 任务书 / 单价 / 默认配置
├── cli.py         # 命令编排
├── report.py      # 成本/实验报告
├── calibrate.py   # 风险分布
├── inspect.py     # EPUB 验收
└── watch.py       # 看板
tests/             # 单元与集成测试
start.sh           # 环境配置脚本
```

`books/` 是本地数据目录（PDF、书稿、页图、日志、构建产物），已在 `.gitignore` 中忽略，不进入仓库。

## 实时看板

`pdfoenix watch` 是**只读**的，不会启动或推进流水线。它读取
`state/progress.json` 和 `logs/pipeline.db`，所以要和写这些文件的命令并行运行。

```bash
# 终端 1：跑流水线
pdfoenix -C books/an_introduction transcribe

# 终端 2：同时看看板
pdfoenix -C books/an_introduction watch

# 只看一帧
pdfoenix -C books/an_introduction watch --once

# 调整刷新间隔（秒）
pdfoenix -C books/an_introduction watch --interval 2
```

`pdfoenix transcribe && pdfoenix watch` 也能跑，但 `watch` 会在 transcribe
**结束后**才启动，看不到转写过程；想看实时进度，请用两个终端，或：

```bash
pdfoenix -C books/an_introduction transcribe &
pdfoenix -C books/an_introduction watch
```

按 `Ctrl-C` 退出看板。未安装 `rich` 时自动回退为纯文本帧。

## 开发

```bash
uv pip install -e ".[dev]"
pytest
ruff check pdfoenix tests
pylint --disable=all --enable=duplicate-code pdfoenix
```

约定：注释与 docstring 用英文；每个模块配套测试；禁止 `\tag{}`、裸 `■`、无锚点标题。

## 设计文档

页锚点、对象模型、复核抽样、成本账本与配置 schema 的完整设计见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## License

（待定）
