# pdfoenix 架构文档

> pdfoenix（PDF Phoenix）= 通用「扫描版 PDF → 现代 EPUB」流水线。
> 本文档是**实施蓝图**：所有设计决策的权威记录，后续代码实现以本文档为准。
> 状态：v0.2 实施完成（2026-10-22）：内核、模型、资产、复核、预设、CLI、看板与首轮标定均已落地。
> v0.3 重构完成（2026-10-24）：命令面分组统一（流水线 / 报告 / 实验），
> `expt.py` 拆出 `combo.py`（模型组合）与 `pipeline.py`（共享编排），死代码与废配置清除。
> v0.4.0（2026-10-29）：数学门禁（`core/mathlint.py`）接入 transcribe/check；
> `init` 刷新现有书 preset；发布工程补齐（AGPL-3.0、CI、示例书、协作文件）。

---

## 1. 定位与目标

把**扫描版 PDF**（尤其数学密集的古旧书）重写为**现代排版的 EPUB**：

- 不追求一比一复刻；排版采用**私有预设**（对标现代出版商），可大幅降低成本
- **文字的绝对准确**是唯一硬指标；公式忠实、编号保留
- 全文流程围绕**成本-质量平衡**设计：确定性的事绝不用 LLM，LLM 调用全部记账
- 交付形态：**包 + init 脚手架**（形态 C）——核心逻辑在包里，每本书一个数据目录

命名：包名/CLI = `pdfoenix`；每本书配置文件 = `config.toml`。

## 2. 设计原则

1. **确定性优先**：scout/render/merge/check/build 等全是纯脚本；LLM 只在「看图转写」和「看图复核」两处出现。
2. **解耦**：LLM 调用收口进 `Transcriber` 接口（可插拔）；算法数值（风险分权重、抽样概率、chunk 大小、模型档位）全是配置项，**换数值不改主逻辑**。
3. **文件即状态**：所有中间产物、注册表、日志落盘，任意中断可续跑；编排器是控制流，不掌握内存态。
4. **成本账本化**：每次 LLM 调用记 token（vision/text 分开）、模型、思考档、实验标签；质量侧（纠错）结构化分级，与成本侧可 JOIN——一切为了跑成本-质量实验。
5. **对抗式复核**：复核永远使用与转写**不同的模型**；LLM 绝不自我复核。

## 3. 分层架构

```
pdfoenix/                       # 源代码区（包）
├── cli.py                      # 编排器：子命令、控制流、断点续跑（见 §5）
├── scaffold.py                 # init 脚手架（目录/配置/预设/空注册表）
├── combo.py                    # 模型组合：provider/combo 配置、active combo、
│                               #   working/opencode.jsonc 生成（组合在 config.toml 手写）
├── pipeline.py                 # 共享编排原语：渲染补缺、转写窗口、复核页图、
│                               #   进度单元、纠错落账（CLI 与 expt 共用）
├── report.py                   # 成本汇总 + 成本-质量对比 + 风险分布报告
├── expt.py                     # 实验：批次/格子账本（experiments.db）、跑批、指标
├── display.py                  # 表格渲染（纯文本 / Markdown，唯一实现）
├── inspect.py                  # EPUB 结构验收检查
├── core/                       # 确定性内核（无 LLM）
│   ├── book.py                 #   书上下文：路径、章/批次/节枚举、章页范围、自然排序
│   ├── config.py               #   配置：默认值 + config.toml 深合并 + 校验 + 差异写回
│   ├── state.py                #   state/ JSON 注册表原子读写 + schema 校验
│   ├── pages.py                #   页选择/批次规划/页锚点与命名约定（唯一来源）
│   ├── telemetry.py            #   SQLite 成本账本
│   ├── risk.py                 #   复杂度风险分（纯字符串分析）
│   ├── mathlint.py             #   数学门禁：安全方言归一化 + texmath 校验
│   ├── scout.py                #   侦察：章/节/图/表/公式结构 → state/scout.json
│   ├── render.py               #   页渲染（整页/区域）→ working/pages/
│   ├── merge.py                #   合并：页锚点→state、切节路由、公式/图/表注册
│   ├── check.py                #   自动校验（编号/脚注/链接/残留/注册表/对象/数学）
│   ├── build.py                #   拼装（目录/表内联）→ pandoc → EPUB
│   └── postprocess.py          #   EPUB 后处理（脚注章末化、nav 移出 spine）
├── transcribe/                 # 模型层（唯二含 LLM 的地方之一）
│   ├── protocol.py             #   Transcriber / Verifier 协议 + 输出契约（见 §8）
│   └── opencode.py             #   OpenCodeTranscriber（本地 CLI，见 §8）
├── verify/                     # 复核层（抽样/审计；LLM 调用经 Transcriber.verify）
│   ├── sampling.py             #   分布驱动抽样策略（可插拔）
│   ├── content.py              #   从批次恢复“页→转写文本”供复核
│   └── audit.py                #   纠错→全局审计
├── assets/                     # 对象资产层
│   └── crop.py                 #   图裁图；抽样页按需 300 dpi 重渲染
├── presets/                    # 预设层（版本化，见 §13）
│   ├── epub.css                #   默认样式（基于现有 styles/epub.css）
│   ├── opencode.jsonc          #   agent 骨架模板（内部；生成 working/opencode.jsonc 用）
│   ├── transcribe.md           #   转写任务书模板
│   ├── verify.md               #   复核任务书模板
│   ├── pricing.toml            #   模型单价表
│   └── config.schema.toml      #   全量配置参考（与 DEFAULT_CONFIG 由测试保持同步）
└── watch.py                    # 实时看板（rich TUI，只读，见 §11）
```

## 4. 目录布局

### 4.1 仓库（源代码区 + 图书区）

```
BooksProject/
├── ARCHITECTURE.md             # 本文档
├── pdfoenix/                   # ★ 源代码区（包，见 §3）
├── books/                      # ★ 图书区（纯数据，非代码）
│   └── an_introduction/        # 现有这本书 = 第一个测试对象/回归 fixture
│       └── legacy/             #   旧人工流水线产物归档（不参与构建）
├── tests/                      # 内核单元测试
└── pyproject.toml
```

### 4.2 一本书的目录（`pdfoenix init` 生成）

```
<book>/
├── config.toml                 # 该书配置（只写与默认值不同的项，见 §12）
├── presets/                    # init 复制的预设（opencode/任务书/单价/css）
├── known_dangling.txt          # check 的合法悬空锚点白名单
├── styles/epub.css             # 构建样式（从 presets/epub.css 复制）
├── source/                     # 原始素材：*.pdf、cover.*
├── manuscript/                 # ★ 书稿源（唯一构建输入内容）
│   └── chNN/
│       ├── 00_chapter.md       #   章标题页
│       ├── secX-Y.md           #   节（最小写作单元；公式 LaTeX inline 于此）
│       └── tabX-Y.md           #   表的 Markdown（正文以占位引用，merge 时 include）
├── figures/                    # 图：figX-Y.png（裁图或重绘；文件即真相）
├── working/                    # 过程文件（可再生）
│   ├── pages/                  #   整页渲染 150 dpi（唯一一份；公式/表页参考图也在这）
│   ├── verify/                 #   复核抽样页的 300 dpi 重渲染（临时）
│   ├── batches/chNN/           #   转写批次 pXXX-YYY.md + TASK.md
│   └── opencode.jsonc          #   生成的适配器配置（生成物，勿手改）
├── state/                      # ★ 元数据/注册表/决策（纯文本，见 §6/§7）
│   ├── scout.json
│   ├── formulas.json  figures.json  tables.json
│   ├── corrections.json
│   └── progress.json
├── expt/                       # 实验区（可再生）
│   ├── experiments.db          #   实验账本（batch/cell/metrics）
│   ├── report.md               #   report quality 生成的对比报告
│   └── batch_N/combo_M/        #   格子产物：batches/ progress.json logs/run.log
├── logs/
│   └── pipeline.db             #   ★ 成本账本（SQLite，见 §10）
└── build/                      # 构建产物：book.md、*.epub
```

## 5. 流水线阶段（CLI 子命令）

命令面分三组，四条统一规则（作用域 `--chapter/--pages`、幂等与 `--force`、
输出 `--out`/位置参数、退出码 0/1/2）详见 README「命令手册」。

```
# 流水线（按执行顺序，每个命令独立可跑、幂等）
pdfoenix init <book> --pdf a.pdf [--combo ID ...]   # 脚手架 + config + 预设
pdfoenix transcribe [--chapter|--pages]   # 分页批转写（自动侦察/渲染，写盘校验）
pdfoenix merge [--chapter] [--no-assets|--recrop]  # 合并 + 锚点/链接 + 裁图
pdfoenix verify [--chapter|--pages|--full] [--audit off|dry|apply]
pdfoenix check [--all]                    # manuscript 校验（含数学门禁）；--all 连批次/图/进度
pdfoenix build [--out book.epub]          # pandoc → EPUB + 后处理（不做内容校验）
pdfoenix inspect [EPUB]                   # EPUB 验收抽查

# 状态（只读）
pdfoenix watch [--interval SEC] [--once]  # 实时看板

# 报告（只读）
pdfoenix report cost [--by model|chapter] # 成本汇总
pdfoenix report quality [--all]           # 成本-质量对比 + expt/report.md
pdfoenix report risk                      # 公式/页风险分布（标定用）

# 实验（只有 run 花钱）
pdfoenix expt show [--batch N] [--combo ID ...] [--all]
pdfoenix expt run  [--batch N] [--combo ID ...] [--all] [-m 备注] [--force]
```

**编排器（cli.py）**：普通确定性 Python 主程序——读 `state/progress.json` 判断「做到哪了」，只做没做的单元；调确定性脚本直接调，LLM 那两处经 `Transcriber` 接口**阻塞调用** opencode；每一步结果落盘。真相与顺序在编排器 + 文件里，opencode 只是被按批召唤的执行体，适配器内部负责并行 fan-out。
共享的单元操作（渲染补缺、转写窗口、复核页图、进度、纠错落账）在 `pipeline.py`，
CLI 与实验跑批共用，两个编排器因此不会漂移。

**数学门禁**：`core/mathlint.py` 是唯一实现。转写批次与整章流用它做早筛；
`check` 用它做书稿终检。安全的 MathML 方言替换（`\Bigm` → `\Big`、双反斜杠）
由 `check` 原子写回源文件；其余问题（缺参数、缺 `$`、花括号/环境不平衡、
texmath 无法渲染）都是 `check` 的 error。`build` 不调用它，只负责生成 EPUB。
`init` 对已存在的书目录刷新文本 preset（任务书/单价/配置参考），不改 CSS、
配置、书稿与状态。

## 6. 对象模型：图 / 表 / 公式

三类「浮动物体」统一为一套对象模型。核心区分：**内容（进 build）与参考图（QA）分离**。

| 对象 | 内容（进 build）                                                    | 参考图（QA）                                      |
|------|---------------------------------------------------------------------|---------------------------------------------------|
| 图   | 物理裁图 `figures/figX-Y.png`（裁图**就是**内容）                   | 无（图本身即图）                                  |
| 表   | Markdown `manuscript/chNN/tabX-Y.md`（正文占位引用，merge include） | `state/tables.json` 路径引用 → `working/pages/`   |
| 公式 | LaTeX（manuscript 内 **inline**，保持正文可读）                     | `state/formulas.json` 双向索引 → `working/pages/` |

**图**：
- 默认全部**裁图**（300 dpi，含题注、避页眉页码），文件即真相。
- 重绘是**可选升级**：用户要重做某张 → 跑重绘脚本**覆盖同名文件** → build 无感知、零 manuscript 改动。
- 转录时生成的结构化图描述（Caption/Content）存 `state/figures.json`，未来重绘无需再翻原书。
- 实拍图不可重绘，永远裁图；示意图默认裁图、可升级重绘。

**表**：
- **全部重构为 Markdown**（准确率高且简单），不裁图进书。
- 参考图不单独裁：页已渲染，`state/tables.json` 存路径引用即可（同公式做法）。
- 表注符号固定套路：标题带符号 `**Table X.Y**$^{\dagger}$...` + 表下斜体注行，**不进脚注**。

**公式**：
- 定义：**独立成行的公式（`$$...$$`）才是公式对象**，编号可有可无；多行 aligned/矩阵算一个对象。行内 `$...$` 属于正文，不是公式对象。
- LaTeX inline 在正文（可读性 = 复核面），每个公式是**可寻址对象**：ID + 编号 + LaTeX + 风险分 + 所在页 + 所属节，登记在 `state/formulas.json`。
- **保留原书编号**；题注照录；页码剔除；脚注保留统一收章末；目录自动生成；索引丢弃。

## 7. 页锚点机制（「页」概念的唯一来源）

合并成 md 后页概念会不可逆丢失，所以必须在**转写阶段**显式锚定：

1. 转写时模型逐页处理，每页内容前插入 `<!-- page:N -->`（N = PDF 页号）。
2. merge 时解析锚点：每个公式/对象记录「→ 页号」进 `state/`；**锚点注释从 md strip 掉**。
3. 此后「公式 ↔ 页 ↔ 页图」全走 `state` 双向索引，**永不重新识图**。

`state/formulas.json` schema（双向，加载后两个方向均 O(1)）：

```json
{
  "formula_to_page":  { "eq3-2-1": "working/pages/pdfpage_123.png", "...": "..." },
  "page_to_formulas": { "working/pages/pdfpage_123.png": ["eq3-2-1", "eq3-2-2"] },
  "formulas": {
    "eq3-2-1": { "number": "(1)", "latex": "\\hat{\\boldsymbol{\\beta}} ...",
                 "risk": 12, "section": "3.2" }
  }
}
```

## 8. 模型层：Transcriber 协议 + opencode 适配

**多态接口**（预留扩展空间：用户可自实现并插拔，不碰核心代码）：

class Protocol:
pass

```python
class Protocol:
    pass

class Transcriber(Protocol):
    def transcribe(self, images: list[Path], task: str) -> str: ...   # 页图 → Markdown
    def verify(self, images: list[Path], content: str) -> list[Correction]: ...  # → 纠错清单

class Correction:
    unit_id: str; formula_id: str | None
    severity: str        # "introduced" | "faithful" | "style"
    pattern: str         # "X→Y"（喂全局审计）
    source: str          # "hallucination" | "ocr" | "bold" | "typeset" | ...
```

**OpenCodeTranscriber（官方唯一实现）**：
- 用已安装的 `opencode` CLI（`npm install -g @opencode/cli`；项目配置指定模型/agent/思考档）。
- 单次（one-shot）headless 调用：一个 prompt 进、一个结果出，无多轮对话；批量 fan-out 在适配器内部并行。
- `transcribe` 默认批大小 4–6 页/次；`verify` 默认 chunk_size ≈ 5 页/次（见 §9）。
- 调用可失败（额度/超时）：编排器按单元重试，状态落盘。
- 模型/思考档由 active combo 决定（`combo.py` 的 `pipeline_config` 从
  `[[expt.combo]]` 生成适配器的 `[models]`），`config.toml` 是用户唯一配置文件，
  `working/opencode.jsonc` 为生成物。

## 9. 复核（verify）：一阶段页抽样 + 对抗式 + 全局审计

**一阶段**：文本复核与公式复核**坍缩为一**——抽样单元 = 页，抽中即整页全核（prose + 行内数学 + 全部公式）。图已加载，全核不额外花 vision token；成本唯一旋钮 = 「抽多少页」。

**复杂度风险分**（纯字符串分析，零模型；权重为可标定默认）：

```
risk = 2×(_ ^ 计数) + 3×(\frac \sqrt) + 3×(\hat \bar \tilde)
     + 2×(\boldsymbol \mathscr) + 5×(matrix/array 环境) + 2×(带上下限的 \sum \prod \int)
```

**分布驱动抽样**（策略可插拔；数值不影响主逻辑）：

```
1. 每条公式算 risk → 聚合到页（page_risk = max 或 sum，默认 max）
2. 用全书经验分布定抽样概率 p(page_risk)，单调递增
3. 独立伯努利抽页；不归一化，靠硬预算封顶
4. 下限保护：不足 max(min_pages, ceil(min_rate × 候选页数)) 时，从剩余页随机补齐；
   候选页少于下限则全抽（公式稀疏的书不会被跳过）
```

**首轮标定**（2026-10-22，1531 条公式；旧批次缺页锚点，页面分布为估算）：
公式风险中位数 28、P90 77；页风险中位数约 54、P90 约 124。采用
`低 ≤50 / 中 51–125 / 高 >125`，抽样概率 `1% / 9% / 60%`，在本数据上期望约 10%；
`budget = "20%"` 只是硬上限，不是目标。`min_pages = 10`、`min_rate = 0.10` 是抽样下限，
保证公式稀疏的书仍有最低复查覆盖率。权重或阈值变更后由标定流程重算。

> 风险分权重、p(risk) 形状、预算数值**实施后实测标定**（用现有 an_introduction 全书公式算出真实分布 + 一批全量复核实测「错误率 vs 风险分」再拟合）。标定不影响架构，只换配置。

**verify 调用粒度**：
- 单次调用 = 一个**页块**（N 页任意分组，默认 N≈5，`verify.chunk_size` 可配）。摊销 opencode 固定开销；受上下文窗口与长上下文精度衰减约束。
- **换模型**（对抗式）；复核页按需 300 dpi 重渲染到 `working/verify/`（本地零 token）。
- 输出为结构化 `Correction` 清单（带 severity/pattern），可直接计数、可 JOIN 成本账本。

**纠错 → 全局审计**（零成本常驻）：
1. 任一纠错记录 `pattern "X→Y"` 进 `state/corrections.json`。
2. 自动全书 grep X（及同形变体），列出全部命中位置。
3. 只对命中位置定点复核（便宜），批量修正——一个错揪出一整类系统性错误。

**全覆盖复核**：仅当预算极充裕且显式要求时开启的高保真模式（`verify --full`），非默认。

## 10. 日志与成本账本（成本-质量实验的基础设施）

`logs/pipeline.db`（SQLite 结构化账本，跨 run 累积、append-only）；同库的 `events`
表就是人类可读的运行轨迹（`watch` 与断点调试直接读它）。

**llm_calls**（每次调用一行；token 分开记，vision/text 单价不同；存 token 不存钱）：

```sql
CREATE TABLE llm_calls (
  call_id TEXT PRIMARY KEY, run_id TEXT,
  combo_id TEXT, batch_no INTEGER,
  stage TEXT,            -- transcribe | verify | ...
  role TEXT,             -- 转写 | 复核（对抗角色）
  model TEXT, provider TEXT, thinking_mode TEXT, prompt_version TEXT,
  unit_id TEXT, n_images INTEGER,
  input_text_tokens INTEGER, input_vision_tokens INTEGER, output_tokens INTEGER,
  latency_ms INTEGER, status TEXT, error TEXT, ts TEXT
);
```

**corrections**（每条纠错一行；结构化分级，与成本侧可 JOIN 出「每页成本 vs 每页错误率」）：

```sql
CREATE TABLE corrections (
  correction_id TEXT PRIMARY KEY, verify_call_id TEXT, unit_id TEXT,
  formula_id TEXT, severity TEXT, pattern TEXT, source TEXT, ts TEXT
);
```

**事件流**（同库追加，供 watch 与断点调试）：`run` 元信息、`unit:stage:start/ok/fail/retry`、`dispatch`/`agent_start`/`agent_done`（fan-out 与子代理状态）、确定性阶段结论（check 缺口/merge 接缝/build 警告）。

原则：不记密钥；LLM 完整 I/O 不落盘（只记 token/耗时/状态）；成本在 `report` 时由 `pricing.toml` 单价 × token 计算（价格变动不污染历史）。

```toml
# presets/pricing.toml（单位：美元 / 1M token）
[pricing."deepseek-v4-pro"]
input_text = 1.0
input_vision = 3.0
output = 5.0
```

## 11. 可视化：`pdfoenix watch`

只读实时看板（tail `pipeline.db` + `state/progress.json` + 磁盘上的批次文件），
与编排器零耦合。每章一行渲染真实进度（批次完成数、在跑/失败、已复核页、
公式/图、纠错、花费），底部是全书汇总：

```
pdfoenix · My Book
────────────────────────────────────────────────────────────────────────
chapter  stage       transcribe        verify   objects         corr  cost
-------  ----------  ----------------  -------  --------------  ----  -----
ch01     verify      5/5               2 pages  104 eq · 8 fig  1     $0.05
ch02     transcribe  0/5 1 run 1 fail  -        -               1     $0.00
────────────────────────────────────────────────────────────────────────
$0.05 · 2 calls · 104k tokens · 2 agents active · 2 pages verified · 2 corrections · 1 failure
```

stage 取自进度本身：`pending → transcribe → merge → merged → verify`；
「在跑/失败」由 `dispatch`/`agent_start`/`agent_done`/`agent_fail` 事件与
`progress.units` 的失败标记驱动。

## 12. config.toml schema

`config.toml` **只写与默认值不同的项**（`core/config.py` 的 `diff_config` 保证写回也只写差异）；
全量键与默认值的参考是 `presets/config.schema.toml`，由测试与 `DEFAULT_CONFIG` 严格同步。

```toml
[book]
title = "An Introduction to Multivariate Statistical Analysis"
author = "T. W. Anderson"
pdf = "source/….pdf"
page_offset = 18                # PDF 页 = 印刷页 + offset

[pipeline]
batch_pages = 5                 # 转写批大小
render_dpi = 150
verify_dpi = 300
verify_chunk_size = 5           # 一次 verify 会话查几页

[transcriber]
timeout = 3600                  # 单次模型调用超时（秒）

[sampling]
strategy = "risk_distribution"  # 可插拔
risk_weights = { sub_sup=2, frac=3, hat=3, bold=2, matrix=5, bigop=2 }
page_risk = "max"               # max | sum
budget = "20%"                  # 硬上限（页比例或页数），非目标抽样率
low_max = 50                    # 页风险 ≤ low_max 为低风险
high_min = 125                  # 页风险 > high_min 为高风险
low_prob = 0.01                 # 各档每页抽样概率（不归一化）
medium_prob = 0.09
high_prob = 0.60
min_pages = 10                  # 绝对下限（公式稀疏的书也至少抽这么多页）
min_rate = 0.10                 # 相对下限（至少抽候选页的 10%）
seed = 0                        # 确定性抽样

[review]
global_audit = true             # verify 后跑全局审计（dry 模式）

[expt]                          # 模型组合 + 实验跑批（见 §8/§5）
pages = "220-224,507-511"
max_workers = 2
attempts = 4
active = 1
[[expt.provider]]  name / base_url / api_key_env   # 网关连接，key 只走环境变量
[[expt.combo]]     id / transcribe / verify        # 不可变的模型对，手写添加
```

适配器所需的 `[models]` 与 `working/opencode.jsonc` 均为**生成物**（由 `combo.py`
从组合与 provider 生成），不进 `config.toml`——用户只改一个文件。

## 13. 预设（presets/，版本化）

| 预设                 | 内容                                                                                                                                         |
|----------------------|----------------------------------------------------------------------------------------------------------------------------------------------|
| `epub.css`           | 现代出版商默认样式（以现有 styles/epub.css 为基准）：90% 字号、1.35 行距、链接色、章末 Notes、悬挂缩进参考文献、figure/table/blockquote 样式 |
| `opencode.jsonc`     | agent 骨架模板（内部）：生成 `working/opencode.jsonc` 用，用户无需编辑                                                                       |
| `transcribe.md`      | 转写任务书：页锚点 `<!-- page:N -->`、图/表占位格式、公式 LaTeX 规则、记号角色制、残缺三分法、脚注/证毕/引用规范                             |
| `verify.md`          | 复核任务书：只核清单、输出结构化 Correction                                                                                                  |
| `pricing.toml`       | 模型单价表                                                                                                                                   |
| `config.schema.toml` | 全量配置参考（与 `DEFAULT_CONFIG` 由测试保持同步）                                                                                           |

## 14. 格式与构建规范（自现有项目移植并泛化）

- 标题显式锚点 `{#sec-X-Y}`；交叉引用挂链接；未录入章节也挂（悬空白名单）。
- 公式编号 `\qquad (N)` 挂末行；**禁止 `\tag{}`**；长公式运算符处断行；保留原书编号。
- 脚注 `[^chXsY-N]`；证毕 `<span class="qed">∎</span>`；算子花体 `\mathscr{E/C/V}`。
- 记号**角色制**（向量/矩阵 `\boldsymbol`，标量/分量细体，算子直立，Wishart W 粗体）；原书印刷错误照录 + `<!-- uncertain -->` 注。
- 残字三分法、跨页段合并、批界接缝缝合——全进 `transcribe.md` 预设。
- pandoc 参数：`--toc --toc-depth=2 --math-method=mathml --split-level=1 --css=styles/epub.css -f markdown+footnotes`。
- postprocess：脚注章末化（剥 noteref、章内重编号、↩ 回链）、nav 移出 spine。
- 已踩陷阱（`\tag`、split-level、h1 page-break、MathML 不换行等）见现有 books/an_introduction/docs/PITFALLS.md，实施时并入包内文档。

## 15. 实施路线与状态

1. [x] **本架构文档**。
2. [x] **内核确定性层**（2026-10-22）：`book/config/state/telemetry/risk/render/scout/merge/check/build/postprocess` 全部实现，147 个单测全绿（`pytest`）。
   - 真实数据回归（`books/an_introduction`，复制到临时目录，不动已验证书稿）：ch03/04/05/06/07/09 的编号公式总数与现有书稿**逐一相等**；ch09 全量 113 条公式、Table 9.1 抽取、页映射 113/113。
   - 已知遗留差异（非内核 bug）：ch02 用旧格式 `**PROBLEMS**` 标题（新转录统一 `## Problems`）；ch08 现有 `sec8-9.md` 缺一段 9 条公式的证明（批次里有，新 merge 忠实收录，**反证现有书稿该处有漏**）。
3. [x] **模型层**（2026-10-22）：`transcribe/protocol.py`（Transcriber/Verifier + Correction）、
   `transcribe/opencode.py`（本地 CLI、页批/复核块并行 fan-out、每次调用落成本账本）+
   `presets/{opencode.jsonc,transcribe.md,verify.md}` 全部实现；真实 MiMo 复核冒烟通过。
   - OpenCode v2 以工作目录发现项目配置，故子进程在 `opencode.jsonc` 所在目录运行（图片传绝对路径）；
     agent 全部禁用工具，只用附件看图、stdout 返回文本。
   - 已知记账限制：`opencode session export` 只给 input 总数，不分 text/vision；当前全记 text、
     vision 记 0（`n_images` 已保留，将来可按图片数重定价）。
4. [x] **对象层**（2026-10-22）：`assets/crop.py` 实现图裁图与按需复核页渲染。
   - 裁图框来源：`state/figures.json` 的可选 `crop: [x0,y0,x1,y1]`（人工权威值），
     缺省时自动检测（OCR 题注 bbox + 行/列墨迹投影），结果写回 `crop` + `crop_source: "auto"`；
     低置信度只告警不硬裁，流程幂等，`--force` 才覆盖。
   - 输出 300 dpi、含图题、避开页眉页码；先写临时文件再 `os.replace`，中断不留半图。
   - `render_verify_pages()` 按 `verify_dpi` 渲染抽样页到 `working/verify/`，本地零 token。
   - 真实回归：ch8 的 fig8-1…8-4 自动裁图与旧手工框对齐，裁图目检通过（不写现有书稿）。
5. [x] **复核层**（2026-10-22）：`verify/sampling.py`（页风险聚合、可插拔策略、
   风险带抽样、硬预算）、`verify/audit.py`（`X→Y` 全书 grep、命中清单、可选批量替换）、
   `report.py`（按模型/章的成本汇总、实验成本-质量对比）。
   - 标定结论：公式风险中位数 28/P90 77；页风险分档 `≤50 / 51–125 / >125`，
     抽样概率 `1% / 9% / 60%`，本数据期望约 10%；`budget=20%` 仅作硬上限。
   - `sampling` 暴露 `SamplingStrategy` 协议，默认 `risk_distribution`，换策略不改主流程。
   - `audit` 默认 dry-run，仅返回命中位置；批量替换需显式 `dry_run=False`。
   - 抽样下限：`min_pages=10`、`min_rate=10%`；风险抽样不足时从剩余页随机补齐，
     候选页少于下限则全抽，避免公式稀疏的书被几乎跳过。
6. [x] **预设与脚手架**（2026-10-22）：`presets/` 全套（epub.css、opencode.jsonc、
   providers.toml、transcribe.md、verify.md、pricing.toml、config.schema.toml）；
   `scaffold.py` + `init`；`cli.py` 全部子命令 + `python -m pdfoenix`；
   `watch.py` 只读看板（rich 可用时表格，否则纯文本）。
7. [x] **标定阶段**（首轮，2026-10-22）：1531 条公式与页风险分布已测定，阈值/抽样概率/
   硬预算已写入默认配置；`report risk` 可随时重算分布。模型路由与 `chunk_size` 属于运行期
   调参，待真实全量转写/复核数据后按 `report` 微调，不阻塞架构。
8. [x] **整合重构**（v0.3，2026-10-24）：命令面分组统一（流水线 / `report cost|quality|risk` /
   `expt show|run`），四条统一规则（作用域、幂等与 `--force`、输出参数、退出码）成文并有
   参数面测试；`expt.py` 拆出 `combo.py`（模型组合）与 `pipeline.py`（共享编排）；
   页锚点/批次命名/章页范围/成本汇总等 21 处重复逻辑收口到单一实现；
   死代码（`MergeOverrides`、`calibrate` 命令、零调用函数）与废配置
   （`models.classify`、`transcriber.provider`、`logging.*`）清除，
   `config.toml` 改为只写差异；README 扩写为命令手册（中英同步）。
9. [x] **配置单一化**（v0.3.1，2026-10-24）：`config.toml` 成为用户唯一配置文件——
   opencode 配置并入（`working/opencode.jsonc` 由 `[[expt.provider]]` + 模板生成，
   `presets/opencode.jsonc` 降为内部骨架模板）；组合改为手写 `[[expt.combo]]`
   （向导与 `providers.toml` 模型库移除）；默认组合改为实测性价比拐点
   （`mimo/mimo-v2.6-pro × deepseek/deepseek-flash#high`），网关为官方地址；
   README 补「expt 实战：挑一个划算的组合」完整走查。

## 16. 开发约定

- **注释与 docstring 一律英文**且详尽：目标是让大模型与后来者无需上下文即可读懂每个模块的职责、边界与关键不变量。
- **Lint 零告警**：`ruff check pdfoenix tests`（规则集见 `pyproject.toml`，含 E/W/F/I/B/A/SIM/RET/ARG/UP/RUF）必须全过；对应 PyCharm 的 Problems 面板应无 weak warning（未使用导入/变量、遮蔽内建名或外层变量、行过长等）。
- **无重复代码块**：`pylint --disable=all --enable=duplicate-code pdfoenix` 应无告警（对应 PyCharm 的 "Duplicated code fragment"）。
- **测试先行（TDD）**：每个模块配套详尽单测；改注释/重构后必须 `pytest` 全绿。
- **真实数据回归**：对 `books/an_introduction` 的批量合并只在临时目录进行，绝不覆盖已验证书稿。
- **使用地道的美国英语**
