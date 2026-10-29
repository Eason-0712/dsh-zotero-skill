---
name: zotero
description: 操作本机 Zotero 文献库：检索论文、导出 BibTeX、为 LaTeX 或 Markdown 草稿添加引用、按需读取附件全文、导入 BibTeX/RIS，以及按主题每日自动收录新论文。用户提到 Zotero、文献库、references.bib、从文献库添加引用或每日文献推送时使用。
---

# Zotero · DeepSeek Harness

这是独立目录 skill，不依赖 Codex、OpenAI 工具或 MCP 服务。使用 Harness 当前提供的文件读取、编辑和命令执行能力运行 Python 脚本。若执行工具不可用，说明缺失能力，不要虚构执行结果。

## 定位与运行

以 Harness 加载本 skill 返回的资源目录为 `<skill-dir>`，不是当前工作目录，也不需要插件根目录。入口为 `scripts/zotero.py`。使用 Python 3.10+，仅依赖标准库。

- Windows：先检查 `py -3 --version`；不可用时检查 `python --version`，或使用用户提供的 Python 可执行文件。不要假定存在 `python3`；WindowsApps 的别名不等于可用解释器。
- macOS / Linux：通常使用 `python3`。
- 路径包含空格时加引号；PowerShell 运行带引号的可执行文件时使用 `&`。

下文 `<python>` 表示已经验证可用的解释器命令。脚本可从任意目录运行：

```text
<python> "<skill-dir>/scripts/zotero.py" status --json
<python> "<skill-dir>/scripts/zotero.py" search "transformer" --with-bibtex-keys --json
<python> "<skill-dir>/scripts/zotero.py" export-bibtex --item-key PXW99EKT --out references.bib
```

## 工作流程

1. 首先运行 `status --json`。默认连接 `http://127.0.0.1:23119`，不需要 Zotero 云端密钥。
2. 如果 API 不可用，按 [references/deepseek-harness.md](references/deepseek-harness.md) 排查。优先让用户在 Zotero 的设置 → 高级 → 其他中启用“允许此计算机上的其他应用程序与 Zotero 通信”（实际名称依版本略有差异）。不要把连接失败等同于文献库为空。
3. 若用户授权自动修改本机设置，先让用户正常关闭 Zotero，再执行 `enable --restart`；它备份 prefs.js、设置本地 API 偏好并启动程序，不强制结束进程。不要在 Zotero 运行时编辑 prefs.js。远端/容器部署不能直接修改另一台机器的 Zotero 配置。
4. 使用 `inventory`、`collections`、`tags`、`search` 检索本机数据。结果中报告题名、作者、年份、Zotero item key，以及可用的 BibTeX key。不要将 item key 当作引用键。
5. 添加引用前先读用户指定的草稿，按具体论断匹配文献。缺少草稿时检索可以继续，但应索取路径或正文，不能假装已编辑。不要只根据标题断言论文支持某个具体结论。
6. `cite --query` 多结果时会拒绝；先检索选定正确条目，再传入 `--item-key`。在目标位置设置唯一标记，然后用 `--marker` 插入。不要默认在文末追加一个脱离上下文的引用。
7. 操作后核对草稿和 .bib，报告改动路径、引用键、备份路径。`sync-bib` 和 `export-bibtex --out` 会覆盖输出文件，避免无意覆盖用户维护的参考文献文件。

## 草稿引用

先通过文件编辑工具在用户授权的正确位置加入唯一标记，如 `<cite:transformer>`：

```text
<python> "<skill-dir>/scripts/zotero.py" cite --item-key PXW99EKT --tex paper.tex --bib references.bib --marker "<cite:transformer>"
<python> "<skill-dir>/scripts/zotero.py" cite --item-key PXW99EKT --markdown draft.md --bib references.bib --marker "<cite:transformer>"
```

LaTeX 输出 `\cite{key}`，Markdown 输出 Pandoc 格式 `[@key]`。脚本会备份已有草稿和 .bib；它不负责配置文档的文献渲染器。Word 的 Zotero 动态引用域不在此脚本能力内，不要用替换 .docx 内部文本冒充已插入 Zotero 引用。

## 其他能力

```text
<python> "<skill-dir>/scripts/zotero.py" collections --json
<python> "<skill-dir>/scripts/zotero.py" citations --style apa --json
<python> "<skill-dir>/scripts/zotero.py" sync-bib --out references.bib
```

仅当用户要求附件、PDF 路径或全文内容时运行：

```text
<python> "<skill-dir>/scripts/zotero.py" children PXW99EKT --json
<python> "<skill-dir>/scripts/zotero.py" fulltext 2JAZS9U8 --out fulltext.txt
<python> "<skill-dir>/scripts/zotero.py" file-url 2JAZS9U8
```

导入是文献库写操作。明确记录来源、内容和目标文献库/集合；用户已明确授权该导入时直接继续，否则先询问。`--yes` 是脚本的执行开关，不是用户授权的替代品。

```text
<python> "<skill-dir>/scripts/zotero.py" selected-target --json
<python> "<skill-dir>/scripts/zotero.py" import-bibtex --file new.bib --yes
<python> "<skill-dir>/scripts/zotero.py" import-ris --file new.ris --yes
```

## 每日自动收录

`daily` 从 OpenAlex 抓取最近 N 天、按主题检索的论文，按期刊 2 年平均被引、年化引用速度、相关度和新鲜度打分，去重后把排名靠前的论文通过 Connector 导入当前选中的 Zotero 分类。OpenAlex 免费、无需 API Key，但需要网络。

```text
<python> "<skill-dir>/scripts/zotero.py" daily-config --enable --topics "transformer;large language model" --max 10 --mailto "you@example.com"
<python> "<skill-dir>/scripts/zotero.py" daily --dry-run
<python> "<skill-dir>/scripts/zotero.py" daily
<python> "<skill-dir>/scripts/zotero.py" daily-schedule --time 08:00
```

- 每次最多 30 篇（`--max`），同一期刊默认最多 3 篇（`--max-per-venue`），回溯天数默认 7（`--days`）。
- 是否启用由用户决定：`daily-config --enable` / `--disable`；未启用时 `daily` 只返回提示，`--force` 可临时运行。
- 导入前先在 Zotero 中选中目标分类；导入是写库操作，先让用户确认主题、数量和目标分类。
- 定时任务用 Windows 任务计划程序，只在该用户登录时运行；Zotero 未启动时脚本会尝试启动并等待。
- `daily --dry-run` 只排序不写库，适合先给用户看候选名单。
- 配置与去重状态在 `~/.zotero-skill/`（`config.json`、`state.json`），可用 `ZOTERO_SKILL_CONFIG_DIR` 改位置。
- 指标是 OpenAlex 的期刊 2 年平均被引与引用速度，不是 JCR 影响因子；不要把分数说成“影响因子”。
- v1 只导入题录与摘要，不下载 PDF；附件仍需用户按需获取。

需要路由细节时再读 [references/local-api-routes.md](references/local-api-routes.md)。本地 API 只读，导入使用 Connector；不要直接写 Zotero SQLite 数据库。
