# pdfoenix

把**扫描版 PDF**（尤其是数学密集的古旧书）重建为现代排版的 **EPUB**。

文字必须准确；公式用 LaTeX 并保留原书编号；图、表、公式可复核；模型调用有成本账本。
设计细节见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 环境结论

**使用 `uv` + 项目内 `.venv`，不使用 conda。**

`uv` 负责安装 Python 3.12、创建 `.venv`、安装依赖；所有东西都在项目目录里，
不污染系统 Python。conda 对这个项目没有额外价值，反而更重。

系统外部只需要：

- Node.js 20+（用于 OpenCode）
- 一个模型网关的 API Key
- PDF 需要带文字层；纯图片扫描件请先 OCR

`pandoc` 和 `opencode` 缺失时，`start.sh` 会尝试自动安装。

## 安装环境（只需一次）

```bash
git clone https://github.com/<你的用户名>/pdfoenix.git
cd pdfoenix
./start.sh
source .venv/bin/activate
```

`start.sh` **只负责环境配置，不会运行任何书稿流程**：

1. 安装 `uv`（如果还没有）；
2. 用 `uv` 创建 `.venv` 并安装 Python 3.12；
3. 安装 pdfoenix 依赖；
4. 检查/安装 `pandoc` 和 `opencode`。

看到 `Environment ready.` 后，环境就绪。

## 开始做书

第一次先只跑一章：

```bash
pdfoenix init books/my_book \
  --pdf /path/to/book.pdf \
  --title "Book Title" \
  --author "Author" \
  --page-offset 18

pdfoenix -C books/my_book scout
pdfoenix -C books/my_book transcribe --chapter ch01
pdfoenix -C books/my_book merge --chapter ch01
pdfoenix -C books/my_book verify --chapter ch01 --full
pdfoenix -C books/my_book build
```

确认这一章没问题后，再跑完整本书：

```bash
pdfoenix -C books/my_book transcribe
pdfoenix -C books/my_book merge
pdfoenix -C books/my_book assets
pdfoenix -C books/my_book verify
pdfoenix -C books/my_book check
pdfoenix -C books/my_book build
pdfoenix -C books/my_book inspect
```

产物在：

```text
books/my_book/build/book.epub
```

中断后重新执行同一条命令即可续跑：已完成的转写批次和复核页会跳过。

## 模型配置（只需一次）

API Key 放进环境变量，不要提交到 Git：

```bash
export MIMO_API_KEY="你的密钥"
```

把它写进 `~/.zshrc` 或 `~/.bashrc`，然后重新打开终端。

网关配置放在 `~/.config/opencode/opencode.jsonc`：

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "providers": {
    "mimo": {
      "name": "MiMo",
      "canonical": "xiaomi",
      "settings": {
        "baseURL": "https://your-gateway.example.com/v1",
        "apiKey": "{env:MIMO_API_KEY}"
      },
      "models": {
        "mimo-v2.6-flash": { "name": "MiMo v2.6 Flash" },
        "mimo-v2.6-pro": { "name": "MiMo v2.6 Pro" }
      }
    }
  }
}
```

书的模型选择在 `books/my_book/config.toml`：

```toml
[models.transcribe]
model = "mimo/mimo-v2.6-flash"
thinking = "default"

[models.verify]
model = "mimo/mimo-v2.6-pro"   # 必须与 transcribe 不同
thinking = "default"
```

## 常用命令

```bash
pdfoenix -C books/my_book report --by model     # 成本/token
pdfoenix -C books/my_book watch                 # 实时看板
pdfoenix -C books/my_book check                 # 校验
pdfoenix -C books/my_book build                 # 生成 EPUB
```

`cost_usd` 默认可能是 0：在 `books/my_book/presets/pricing.toml` 里填入服务商单价即可。
token 数始终会真实记录。

## 仓库与推送

只推送代码和测试：

```text
pdfoenix/  tests/  pyproject.toml  start.sh  README.md  ARCHITECTURE.md  .gitignore
```

`books/` 已在 `.gitignore` 中忽略，包含 PDF、书稿、页图、日志和构建产物，不会推送。
如果旧仓库里已经跟踪了 `books/`，执行一次：

```bash
git rm -r --cached books
```

这不会删除本地文件。

## 开发

```bash
uv pip install -e ".[dev]"
pytest
ruff check pdfoenix tests
```
