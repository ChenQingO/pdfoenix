# pdfoenix

**简体中文** · [English](README.en.md)

这个项目的目的是将**扫描版 PDF**重建为现代排版的 **EPUB** 格式（如果你看过那种来自上个世纪的出版物，
你一定会同意这个项目是十分必要的），这也正是 PDF Phoenix（pdfoenix）所代表的含义。

设计这个项目时，我想我们无需原封不动地复刻书中的设计和排版，既然是再版，不妨放开手脚，所以在转录时，
项目将使用现代排版的规范。但书籍的内容不会遭到扭曲，我们会尽最大的努力确保文字、公式、图、表的准确。
设计细节见 [ARCHITECTURE.md](ARCHITECTURE.md)。

[特性](#特性) · [安装](#安装) · [快速开始](#快速开始) · [命令手册](#命令手册) · [配置](#配置) · [常见问题](#常见问题)

![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python&logoColor=white)
![uv](https://img.shields.io/badge/uv-package%20manager-8A2BE2)
![pandoc](https://img.shields.io/badge/pandoc-EPUB-7a4f1d)
![status](https://img.shields.io/badge/status-alpha-orange)

---

## 它是什么

`pdfoenix` 是一条把**扫描版 PDF**（尤其是数学密集的旧书）重建为**现代排版 EPUB** 的命令行流水线。
一条命令一条命令地跑，也可以交给脚本整本跑完；中断了随时接着跑，每一分钱花在哪都有账可查。

它不追求逐像素复刻原书，而是追求：

- **文字绝对准确** —— 唯一的硬指标
- **公式忠实** —— LaTeX 转写，保留原书编号
- **对象可寻址** —— 图、表、公式都能定位、复核、重做
- **成本可记账** —— 每次模型调用都入账，方便做成本-质量实验

> 项目目前处于 **alpha**：核心流水线已经能完整出书，格式规范与边角情况还在打磨中。
> 欢迎提 issue。

## 特性

| 特性           | 说明                                                                         |
|----------------|------------------------------------------------------------------------------|
| **确定性优先** | 渲染、侦察、合并、校验、构建全是纯脚本，LLM 只负责「看图转写」和「看图复核」 |
| **文件即状态** | 中间产物、注册表、日志全部落盘，中断后可续跑                                 |
| **页锚点**     | 转写时写入 `<!-- page:N -->`，建立公式/图/表与页图的双向索引                 |
| **对抗式复核** | 转写与复核使用不同模型，按风险抽页，抽中即整页全核                           |
| **可插拔**     | `Transcriber` 协议、抽样策略、风险权重、阈值与概率均为配置项                 |

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

`start.sh` 只负责环境配置，不运行书稿流程：安装 `uv`、创建 `.venv`（Python 3.12）、
安装依赖，并在缺失时自动安装 `pandoc` 与 `opencode`。

默认配置来自 MiMo、DeepSeek 的实验结果，力图在尽可能低的成本下取得尽可能高的质量。
这是一个权衡结果，而不是质量最优解。如果你想要更好的质量，可以换用更强的模型或其它 agent。
本项目配置项较多；如果你决定更换模型，可以委托智能体修改配置并协助运行。

### 配置模型（只改 config.toml）

模型、网关、组合**全部写在每本书的 `config.toml` 里**，不需要碰任何其它文件。
`init` 生成的默认配置已带官方 MiMo 与 DeepSeek 网关和一个开箱即用的组合，
你只需要把 key 放进环境变量：

```bash
export MIMO_API_KEY="your-key"        # 默认组合的转写端
export DEEPSEEK_API_KEY="your-key"    # 默认组合的复核端
```

换网关就改 `[[expt.provider]]` 的 `base_url`；key 只走环境变量（`api_key_env`），
永不落盘。包内的 `presets/opencode.jsonc` 只是 agent 骨架模板，
`working/opencode.jsonc` 会在运行时自动生成——那是生成物，无需编辑。

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

## 命令手册

### 统一规则（先读这个）

所有命令共享四条规则，读完这四条，`--help` 里的参数基本就自明了。

**1. 选书：`-C / --book`**

`-C`（等价于 `--book`）指定**要操作的书目录**，默认是当前目录 `.`：

```bash
pdfoenix -C books/my_book check        # 从仓库任意位置操作任意一本书
cd books/my_book && pdfoenix check     # 等价写法
```

**2. 作用域：`--chapter` / `--pages`**

作用域参数只做一件事：**把工作限制在指定范围内，范围外的产物一个都不碰**。

| 参数        | 含义                          | 支持的命令                       |
|-------------|-------------------------------|----------------------------------|
| `--chapter ch09` | 只处理这一章             | `transcribe`、`merge`、`verify`  |
| `--pages 19-30,40` | 只处理这些页           | `render`、`transcribe`、`verify` |

`merge` 只有 `--chapter`：它的工作单元是「一章 → 若干节文件」，按页切会破坏注册表一致性。
`verify` 里 `--pages` 会绕过抽样（直接复核指定页），`--chapter` 则把抽样/全核限制在一章内。

**3. 幂等与 `--force`**

所有阶段都是**幂等**的：跳过已记录为完成的工作单元，中断后重跑同一命令即可续跑。
`--force` 只有一个含义——**重做已完成的单元**（连带刷新该单元的输入缓存，如页图）：

| 命令        | `--force` 重做什么                       |
|-------------|------------------------------------------|
| `init`      | 覆盖已存在的脚手架文件                   |
| `render`    | 重渲染已有页图                           |
| `transcribe`| 重跑已有批次                             |
| `verify`    | 重核已完成的页（含 300 dpi 页图重渲染）  |
| `assets`    | 覆盖已有裁图                             |
| `expt run`  | 重跑已完成的实验格子                     |

**4. 退出码与读写边界**

| 退出码 | 含义                                        | 典型命令                     |
|--------|---------------------------------------------|------------------------------|
| 0      | 成功                                        | 全部                         |
| 1      | 跑完了，但发现问题（校验未通过/实验有失败）| `check`、`inspect`、`expt run` |
| 2      | 用法错误或运行时错误                        | 全部                         |

只读命令（**从不改文件、从不花钱**）：`watch`、`report cost|quality|risk`、`check`、`inspect`、`expt show`。
花钱的只有两个模型调用点：`transcribe`、`verify`（以及 `expt run`）。

> 所有命令均可用 `pdfoenix <command> --help` 查看完整参数。

### 流水线命令（按执行顺序）

#### `pdfoenix init [TARGET] [--pdf FILE] [--title T] [--author A] [--page-offset N] [--force]`

脚手架：目录结构、`config.toml`（只写与默认值不同的项）、预设、空注册表。
`TARGET` 是书目录（省略时用 `-C`）；`--page-offset` 满足 `PDF 页 = 印刷页 + offset`。
模型组合在 `config.toml` 里手写（见[配置](#配置)中的「组合」一节）。

#### `pdfoenix scout`

扫描 PDF 文字层 → `state/scout.json`（章/节/图/表/公式结构）。
其它命令需要章结构时会自动补跑，一般不必手动执行。

#### `pdfoenix render [--pages SPEC] [--force]`

按 `pipeline.render_dpi`（默认 150）渲染页图 → `working/pages/`。
可选：`transcribe` 会自动补渲染缺失页，只有想提前批量渲染时才需要它。

#### `pdfoenix transcribe [--chapter chNN] [--pages SPEC] [--force]`

分批转写 → `working/batches/chNN/pXXX-YYY.md`。批大小由 `pipeline.batch_pages` 决定；
每次调用的 token/成本入账 `logs/pipeline.db`；进度写入 `state/progress.json`（供 `watch` 读取）。

#### `pdfoenix merge [--chapter chNN]`

合并批次、切节、注册对象 → `manuscript/chNN/`，同步更新
`state/{formulas,figures,tables}.json`。合并时解析页锚点并将其从正文剥离；
重复合并会先清掉该章旧贡献，不会产生重复注册。

#### `pdfoenix assets [--force] [--no-auto]`

裁图 → `figures/figX-Y.png`。裁剪框来源优先级：注册表里人工标注的 `crop` > 自动检测
（OCR 题注 + 墨迹投影）；低置信度只告警不硬裁。`--no-auto` 关闭自动检测。

#### `pdfoenix verify [--chapter chNN] [--pages SPEC] [--full] [--force] [--audit off|dry|apply]`

抽样复核：按风险分布抽页（见[配置](#配置)中的「抽样」），另一个模型整页全核，纠错入账
`state/corrections.json` 与成本账本。

- `--full` 跳过抽样，复核候选页全部；`--pages` 复核指定页；
- `--audit` 控制「全局审计」（把纠错 pattern 全书 grep 出同类错误）：
  `dry` 只列出命中（默认），`apply` 批量改写，`off` 跳过；
  默认值也可由配置 `review.global_audit` 关掉。

#### `pdfoenix check`

本地规则校验：公式编号连续性、脚注配平、交叉链接、注册表一致性、残留
（`\tag{}`、裸 `■`、`\mathcal`）、标题锚点。有错误时退出码 1。
合法的悬空锚点写进 `known_dangling.txt`（每行一个 id）。

#### `pdfoenix build [--out book.epub]`

拼装 Markdown（目录、表内联）→ pandoc → EPUB → 后处理（脚注章末化、nav 移出 spine）。
`--out` 指定 `build/` 下的输出文件名。`source/cover.*` 自动成为封面。

#### `pdfoenix inspect [EPUB]`

EPUB 结构验收（mimetype 位置/压缩、container.xml、OPF manifest/spine、MathML、脚注回链）。
省略路径时取 `build/` 下最新的 `.epub`。有问题时退出码 1。

### 状态与报告

#### `pdfoenix watch [--interval SEC] [--once]`

只读实时看板（详见[实时看板](#实时看板)）。

#### `pdfoenix report cost [--by model|chapter]`

按模型或按章汇总 token 与成本（`cost_usd` 由 `presets/pricing.toml` 单价计算，
未配置单价的模型显示 0，但 token 数始终真实）。

#### `pdfoenix report quality [--all]`

实验的**成本-质量对比**：每个 (batch, combo) 格子的覆盖率、纠错/100 复核页、成本，
并写入 `expt/report.md`。`--all` 包含已从批次移除的组合（数据保留，默认隐藏）。

#### `pdfoenix report risk`

公式/页风险分布报告（用于标定抽样阈值与概率；只读，不改任何配置）。

### 实验

一个**批次（batch）** = 一轮组合对照；一个**格子（cell）** = 批次里一个组合的完整运行
（产物在 `expt/batch_N/combo_M/`）。实验用独立账本 `expt/experiments.db`，不污染生产账本。

#### `pdfoenix expt show [--batch N] [--combo ID ...] [--all]`

只读展示：

| 写法                            | 显示                       |
|---------------------------------|----------------------------|
| `expt show`                     | 所有批次总览               |
| `expt show --batch 2`           | 批次 2 的格子明细          |
| `expt show --combo 2 4`         | 这些组合跨批次的行         |
| `expt show --batch 2 --combo 4` | 预览：批次 2 换成组合 4 会怎样（不落配置） |

#### `pdfoenix expt run [--batch N] [--combo ID ...] [--all] [-m 备注] [--force]`

跑一轮实验（**花钱**）。不带 `--batch` 时创建新批次；带 `--batch` 时续跑该批次未完成的格子，
若同时给出 `--combo` 则替换该批次的组合集并补跑缺口（旧数据保留）。已完成的格子默认跳过，
`--force` 才重做。

```bash
pdfoenix -C books/my_book report cost --by model      # 按模型看成本
pdfoenix -C books/my_book expt show                   # 批次 × 组合总览（只读）
pdfoenix -C books/my_book expt run --combo 2 4 -m "换网关后复测"
pdfoenix -C books/my_book expt run --batch 1          # 续跑 batch 1
pdfoenix -C books/my_book report quality              # 对比表 + expt/report.md
```

#### 上手示例：挑一个划算的组合

如果你想自己测一测成本和质量的平衡，就用 `expt`。场景：默认组合在你的书上表现不错，
但你想知道更便宜的组合行不行——组合是「转写模型 × 复核模型」，两个都花钱，值得先测再换。

**第 1 步：在 `config.toml` 里声明想试的组合**（手写即可，没有向导）：

```toml
[[expt.combo]]
id = 2
transcribe = "mimo/mimo-v2.6-pro-ultraspeed"   # 更快、更贵的转写
verify = "deepseek/deepseek-flash#high"

[[expt.combo]]
id = 3
transcribe = "deepseek/deepseek-flash#high"    # 换 deepseek 来转写
verify = "mimo/mimo-v2.6-pro"
```

用到的网关都已声明过；只有新网关才需要再补一段 `[[expt.provider]]`。

**第 2 步：指定基准页**（所有组合跑同一批页，结果才可比）：

```toml
[expt]
pages = "220-224,507-511"   # 挑几页有代表性的：公式密的 + 稀的
```

**第 3 步：跑一轮实验**（这一步花钱；20 页 × 3 个组合，可参考下方价格表估算）：

```bash
pdfoenix -C books/my_book expt run --combo 1 2 3 -m "第一次定价复测"
```

这会创建 batch 1，为每个组合跑一个格子：转写基准页 → 该组合自己的裁判复核 →
记录成本与纠错率。中断了重跑同一命令自动续跑；`--force` 才重做。

**第 4 步：看结果**：

```bash
pdfoenix -C books/my_book expt show --batch 1    # 每个组合的纠错/100页与成本
pdfoenix -C books/my_book report quality         # 跨批次对比 + expt/report.md
```

**第 5 步：选定胜者**。比如组合 2 又便宜又好，把它设为生产用组合：

```toml
[expt]
active = 2
```

之后普通的 `transcribe` / `verify` 就用它。历史批次永远可回看——组合不可变，
账本里每笔调用都记着当时的组合。

## 配置

每本书只有一个 `config.toml`，**只写与默认值不同的项**；缺省项由内置默认值补齐。
全部键与默认值的参考在 `presets/config.schema.toml`（`init` 会复制一份到书里，
始终与内置默认值同步）。`init` 写入的也只是差异，文件不会被膨胀成全量快照。

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

[transcriber]
timeout = 3600            # 单次模型调用的超时（秒）

[expt]
pages = "220-224,507-511"     # 全部组合共用的基准页
active = 1                    # 驱动普通 transcribe/verify 的组合
max_workers = 2               # 每个组合并发的窗口数
attempts = 4                  # 每个窗口的重试次数

[[expt.provider]]             # 网关连接；key 永不落盘，请 export
name = "mimo"
base_url = "https://api.xiaomimimo.com/v1"
api_key_env = "MIMO_API_KEY"

[[expt.provider]]
name = "deepseek"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"

# 默认组合：基准实验的性价比拐点（已随默认配置提供，无需手写）
[[expt.combo]]
id = 1                        # 组合不可变；要改就新建 id
transcribe = "mimo/mimo-v2.6-pro"
verify = "deepseek/deepseek-flash#high"   # 必须与 transcribe 不同

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

[review]
global_audit = true      # verify 后跑全局审计（dry 模式）
```

### 组合（combo）：两个 agent 的模型对

**组合（combo）** 是两个计费 agent 的模型对：`transcribe` 写 Markdown，`verify`（裁判）复核。
组合写在 `config.toml` 且**不可变**——要改就新建 id，历史实验记录因此永远可解释。
**添加组合就是手写配置**（没有向导，也没有预设模型库——配模型的人知道自己要什么）：

```toml
# config.toml
[[expt.provider]]         # 新网关才需要；已声明过的直接跳过
name = "deepseek"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"

[[expt.combo]]
id = 2                    # 新 id，不要改旧的
transcribe = "deepseek/deepseek-flash#high"
verify = "mimo/mimo-v2.6-pro"   # 必须与 transcribe 不同
```

规则只有三条：

- 组合写法为 `provider/model#thinking`；`verify` 必须与 `transcribe` 不同
  （裁判不复核自己的产出）；
- `[[expt.provider]]` 声明网关连接：`base_url` + key 的环境变量名（**key 永不落盘**，export 即可）；
- `active` 指向驱动普通 `transcribe` / `verify` 的组合。

适配器把组合拼成 `opencode run --model provider/model#variant` 传给对应 agent；
`working/opencode.jsonc` 运行时自动生成（内含禁用工具的 agent 骨架），无需编辑。

`thinking = "default"`（或留空）表示不附加 variant；其他值会变成 `#<value>` 并作为显式档位
传给网关。具体有哪些档位由模型和网关决定：

- **MiMo** 网关只接受 `default`；设置 `#high` / `#low` / `#max` 会得到 `Variant unavailable`；
- **DeepSeek** 的 `#low` / `#high` / `#max` 对应 `reasoning_effort`（默认 `high`），
  `#none` 关闭思考（质量显著下降，见下表）；
- `deepseek-flash`（现为 V4.1 Flash）支持图片输入，可以当转写或复核；
  `deepseek-v4-pro` 是纯文本模型，不能看图（会拒绝执行）。

### 组合参考价（美元 / 10 页）

实测基准：数理统计扫描书，20 页 × 4 个难度窗（密集公式/中等/稀疏/散文），
全量转写 + 全量复核、含重试损耗，折算为每 10 页口径：

| 组合（transcribe × verify）                              | 纠错/100页 | 美元/10页 | 定位       |
|----------------------------------------------------------|-----------:|----------:|------------|
| mimo-v2.6-pro-ultraspeed × mimo-v2.6-pro                 |   **55**   |   0.68    | 质量最佳   |
| **mimo-v2.6-pro × deepseek-flash#high**（默认组合）      |   50–65    |   0.13    | 性价比拐点 |
| deepseek-flash#high × mimo-v2.6-pro                      |     85     |   0.12    | 成本优先   |
| mimo-v2.6-flash × mimo-v2.6-pro                          |    190     |   0.11    | 不推荐     |
| deepseek-flash#none × mimo-v2.6-pro                      |    250     |   0.13    | 不推荐     |

- 默认组合（第 2 行，性价比拐点）已随默认配置提供：`init` 后
  `export MIMO_API_KEY` 与 `export DEEPSEEK_API_KEY` 即可直接开工；
  追求极致质量就按第 1 行新建组合；
- 思考档位显著影响质量：deepseek-flash `#none` → `#high`，纠错率 250 → 85，
  成本仅 +$0.02/10页——思考值得开；
- `deepseek-v4-pro` 不支持图片输入（会拒绝执行），任何思考档位都不可用；
- 价格随公式密度浮动；上表为**全量复核**口径。生产环境按约 10% 风险抽样复核时，
  全书成本 ≈ 转写 + 10% × 复核，可比上表再降 3–4 成。

### 抽样

风险分档来自实测标定：页风险 `≤50 / 51–125 / >125` 对应抽样概率 `1% / 9% / 60%`，
期望约 10% 页，`budget` 仅作硬封顶。风险分是纯字符串分析（上下标/分式/帽子/粗体/矩阵/大运算符
的加权计数），权重在 `[sampling.risk_weights]`，可用 `report risk` 查看实测分布后重标定。

抽样有「下限 + 上限」：先按风险概率抽页；若不足
`max(min_pages, ceil(min_rate × 候选页数))`，从剩余页中随机补齐；候选页总数少于
该下限时全部抽取。这样公式稀疏的书也不会因为风险带为空而几乎不抽样。

## 成本与质量

每次模型调用都会记入 `logs/pipeline.db`（生产）或 `expt/experiments.db`（实验）：
模型、思考档、组合、批次、输入/视觉/输出 token、耗时、状态。随时可以回答
「这本书花了多少、花在哪」：

```bash
pdfoenix -C books/my_book report cost --by model     # 按模型看 token / 成本
pdfoenix -C books/my_book report cost --by chapter   # 按章看 token / 成本
pdfoenix -C books/my_book report quality             # 成本-质量对比 + expt/report.md
pdfoenix -C books/my_book report risk                # 风险分布（标定用）
```

实验质量由组合自己的 `verify` 裁判判定：纠错/100 复核页，并列覆盖率与结构缺陷
（错误页锚点、工具标记泄漏）。

> `cost_usd` 由 `presets/pricing.toml` 的单价计算；未配置单价的模型显示 0，但 token 数始终真实。

## 常见问题

**PDF 没有文字层怎么办？**
`scout` 依赖文字层侦察结构。纯图片扫描件先 OCR 一遍：

```bash
ocrmypdf input.pdf output.pdf
```

**跑到一半中断了怎么办？**
直接重跑同一条命令。所有阶段都会跳过已完成的部分接着做；想从头重做某一步，
给那条命令加 `--force`。

**`check` 报「悬空锚点」，但那些章节我还没录？**
预期行为：交叉引用指向尚未转录的章节。把这些锚点 id 写进书目录的
`known_dangling.txt`（每行一个），`check` 就会放行。

**报 `Variant unavailable`？**
你给模型指定了网关不支持的思考档位（如 MiMo 上的 `#high`）。改成 `thinking = "default"`
或换用支持该档位的网关；详见[组合](#组合combo两个-agent-的模型对)。

**整本书大概要花多少钱？**
见[组合参考价](#组合参考价美元--10-页)。生产口径下全书成本 ≈ 转写 + 约 10% 复核。
想知道已花多少，随时 `report cost`。

**想换更强或更便宜的模型？**
不要改已有组合（它不可变，历史实验才可比）；在 `config.toml` 里新建一个 id，先用
`expt` 在你自己的书上测一轮再切换——完整流程见[上手示例](#上手示例挑一个划算的组合)：

```toml
[[expt.combo]]
id = 2
transcribe = "mimo/mimo-v2.6-pro-ultraspeed"
verify = "mimo/mimo-v2.6-pro"
```

**API key 安全吗？**
key 只走环境变量（`[[expt.provider]].api_key_env`），不会写进任何文件；
`books/` 整个目录也在 `.gitignore` 里。

## 实时看板

`pdfoenix watch` 是**只读**的，不会启动或推进流水线。它读取
`state/progress.json` 和 `logs/pipeline.db`，所以要和写这些文件的命令并行运行：

```bash
# 终端 1：跑流水线
pdfoenix -C books/my_book transcribe

# 终端 2：同时看看板
pdfoenix -C books/my_book watch

# 只看一帧
pdfoenix -C books/my_book watch --once

# 调整刷新间隔（秒）
pdfoenix -C books/my_book watch --interval 2
```

想看实时进度请用两个终端；`pdfoenix transcribe && pdfoenix watch` 也能跑，
但 `watch` 会在 transcribe **结束后**才启动。按 `Ctrl-C` 退出看板。
未安装 `rich` 时自动回退为纯文本帧。

一帧长这样（转写进行中）：

```text
pdfoenix · My Book
────────────────────────────────────────────────────────────────────────
chapter  stage       transcribe        verify   objects         corr  cost
-------  ----------  ----------------  -------  --------------  ----  -----
ch01     verify      5/5               2 pages  104 eq · 8 fig  1     $0.05
ch02     transcribe  0/5 1 run 1 fail  -        -               1     $0.00
────────────────────────────────────────────────────────────────────────
$0.05 · 2 calls · 104k tokens · 2 agents active · 2 pages verified · 2 corrections · 1 failure
```

每章一行：批次进度（完成/总数，`run` = 在跑，`fail` = 失败）、已复核页数、
合并后的公式/图、纠错数与花费；底部是全书汇总。看板本身只读，
所有数字都来自流水线已落盘的状态文件。

## 项目结构

```text
pdfoenix/          # 包源代码
├── core/          # 确定性内核（无 LLM）：book/config/state/pages/telemetry/
│                  #   risk/scout/render/merge/check/build/postprocess
├── combo.py       # 模型组合：provider/combo 配置、active combo、opencode 配置生成
├── pipeline.py    # 共享编排原语：渲染补缺、转写窗口、复核页图、进度、纠错落账
├── transcribe/    # Transcriber 协议 + OpenCode 适配
├── verify/        # 抽样、页内容恢复、全局审计
├── assets/        # 图裁图、复核页渲染
├── expt.py        # 实验：批次/格子账本、跑批、指标、对比报表
├── report.py      # 成本/质量/风险报表
├── display.py     # 表格渲染（纯文本 / Markdown）
├── inspect.py     # EPUB 验收
├── watch.py       # 看板
├── scaffold.py    # init 脚手架
├── cli.py         # 命令编排
└── presets/       # epub.css / opencode.jsonc（agent 骨架模板）/ 任务书 / 单价 / 配置参考
tests/             # 单元与集成测试
start.sh           # 环境配置脚本
```

`books/` 是本地数据目录（PDF、书稿、页图、日志、构建产物），已在 `.gitignore` 中忽略，不进入仓库。

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
