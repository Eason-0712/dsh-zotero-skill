---
name: zotero
description: 面向科研文献全流程的 Zotero 技能：找文献、追前沿、筛核心论文、管理文献（打标签/归集/查重）、批量阅读、分类整理、提炼脉络、发现空白，并支持导出 BibTeX 与给 LaTeX/Markdown 草稿插入引用。用户提到 Zotero、找文献、查文献、前沿、综述、批量读论文、文献整理或研究空白时使用。
---

# Zotero · DeepSeek Harness

独立目录 skill，不依赖 Codex、OpenAI 工具或 MCP 服务。使用 Harness 当前提供的文件读取、编辑和命令执行能力运行 Python 脚本。若执行工具不可用，说明缺失能力，不要虚构执行结果。

## 定位与运行

以 Harness 加载本 skill 返回的资源目录为 `<skill-dir>`，不是当前工作目录。入口为 `scripts/zotero.py`，Python 3.10+，仅依赖标准库。

- Windows：先检查 `py -3 --version`；不可用时检查 `python --version`。不要假定存在 `python3`。
- macOS / Linux：通常使用 `python3`。
- 路径包含空格时加引号；PowerShell 里带引号的路径用 `&` 调用。

下文 `<python>` 表示已验证可用的解释器命令。先运行 `status --json`：默认连 `http://127.0.0.1:23119`，需要 Zotero 在运行且已开启「允许此计算机上的其他应用程序与 Zotero 通信」。连不上不等于文献库为空，按 [references/deepseek-harness.md](references/deepseek-harness.md) 排查；必要时让用户正常关闭 Zotero 后执行 `enable --restart`（备份 prefs.js，不强制结束进程）。

## 读写边界

- 读操作（检索、统计、导出、生成阅读包）随时可用。
- 写操作（打标签、归集、查重清理、分类写入）必须先 `authorize`：Zotero 会弹窗，让用户点「Always Allow」（持久）而不是「Allow」（一次性）。凭据保存在 `~/.zotero-skill/local-key.json`。
- 写命令一律支持 `--dry-run`；改库前先把 `--dry-run` 结果给用户看，得到确认后再加 `--yes`。
- 不要直接读写 `zotero.sqlite`，也不要绕过本地 API 改文件。

## 八项能力

### 1. 找文献 `find`

从 OpenAlex 检索，按期刊影响、年化引用、相关度、新鲜度打分，并与本地库去重。

```text
<python> "<skill-dir>/scripts/zotero.py" find "knowledge distillation motor imagery" --days 730 --limit 15
<python> "<skill-dir>/scripts/zotero.py" find "bin picking 6d pose" --days 365 --oa-only --json --out find.json
```

- 分数是启发式排序信号（期刊 2 年平均被引 + 引用速度 + 相关度 + 新鲜度），不是 JCR 影响因子。
- `--json` 输出可交给 `screen` 或 `import`；`--oa-only` 只留开放获取。

### 2. 追前沿 `frontier`

把常读方向存成 watch，之后每次只拉「上次运行以来」的新论文。

```text
<python> "<skill-dir>/scripts/zotero.py" frontier add eeg-kd --query "knowledge distillation motor imagery" --query "transformer EEG decoding" --days 60
<python> "<skill-dir>/scripts/zotero.py" frontier run eeg-kd --since-last --max 10
<python> "<skill-dir>/scripts/zotero.py" frontier list
<python> "<skill-dir>/scripts/zotero.py" frontier remove eeg-kd
```

多次 `frontier run` 不会重复给同一篇（seen 记在 `~/.zotero-skill/state.json`）。需要直接入库时加 `--import --yes`。

### 3. 筛核心论文 `screen`

把候选（`find`/`frontier` 的 JSON）或库内条目分成 核心 / 重要 / 参考 三档。

```text
<python> "<skill-dir>/scripts/zotero.py" screen --input find.json --top 12
<python> "<skill-dir>/scripts/zotero.py" screen --library --tag "project:MI-EEG" --top 10
<python> "<skill-dir>/scripts/zotero.py" screen --query "grasp pose estimation" --top 10 --out shortlist.md
```

- 有引用数据时按分档阈值判定；库内条目没有引用数据，改用相对排名，输出里会注明。
- 报告是给人看的短名单，不要把它当成事实性结论。

### 4. 管理文献 `authorize` / `tag` / `collect` / `dupes`

```text
<python> "<skill-dir>/scripts/zotero.py" authorize
<python> "<skill-dir>/scripts/zotero.py" tag --tag "project:MI-EEG" --add "reviewed:2026-10" --dry-run
<python> "<skill-dir>/scripts/zotero.py" tag --keys ABC12345 --replace "topic:kd,topic:eeg"
<python> "<skill-dir>/scripts/zotero.py" collect 核心-EEG --tag "project:MI-EEG" --dry-run
<python> "<skill-dir>/scripts/zotero.py" dupes --all --json
<python> "<skill-dir>/scripts/zotero.py" dupes --all --trash --yes
```

- 选择参数统一：`--keys`、`--query`、`--tag`、`--collection`、`--all`（`--limit` 限制条数）。
- `collect` 找不到同名分类会新建；`--parent` 可建成子分类。
- `dupes` 按 DOI（大小写不敏感）与归一化标题聚类，保留标签/摘要更全的一条；`--trash` 只把多余副本放进回收站。

### 5. 批量阅读 `digest`

为一批条目生成阅读包：每篇一个 Markdown（元数据 + 摘要 + 章节大纲 + 笔记骨架），加一个 `index.md`。

```text
<python> "<skill-dir>/scripts/zotero.py" digest --tag "project:BIN-PICKING" --out reading/bin-picking
<python> "<skill-dir>/scripts/zotero.py" digest --tag "project:BIN-PICKING" --fulltext --out reading/bin-picking
```

- 章节大纲来自 Zotero 已索引的全文；没有 PDF 时只有摘要，不要假装读到了正文。
- 生成后按需读这些文件再写综述，不要一次把几十篇原文塞进上下文。

### 6. 分类整理 `classify`

从题名+摘要识别方法与数据集，生成标签/集合计划，可写入。

```text
<python> "<skill-dir>/scripts/zotero.py" classify --all
<python> "<skill-dir>/scripts/zotero.py" classify --tag "project:MI-EEG" --apply --yes
<python> "<skill-dir>/scripts/zotero.py" classify --all --by-venue --apply --yes
```

- 默认生成 `auto:method:*`、`auto:dataset:*`；`--tag-prefix` 可改前缀。
- 写入前务必先跑一次不带 `--apply` 的版本给用户看。

### 7. 提炼脉络 `landscape`

统计年份、期刊、方法、数据集与共现，输出可直接放进综述的表格。

```text
<python> "<skill-dir>/scripts/zotero.py" landscape --all --out landscape.md
<python> "<skill-dir>/scripts/zotero.py" landscape --tag "project:MI-EEG" --json
```

### 8. 发现空白 `gaps`

按 `project:`/`topic:` 分组，找「方法 × 数据集」从未交叉的组合、停更标签和标注缺失。

```text
<python> "<skill-dir>/scripts/zotero.py" gaps --all --out gaps.md
<python> "<skill-dir>/scripts/zotero.py" gaps --tag "project:MI-EEG" --min-freq 2
```

- 输出是规则推导的候选空白，必须回到原文核实后再写进论文或汇报。

## 每日自动收录

`daily` 按主题从 OpenAlex 抓取最近 N 天的论文，打分去重后用 Connector 导入当前选中的分类。

```text
<python> "<skill-dir>/scripts/zotero.py" daily-config --enable --topics "transformer;large language model" --max 10 --mailto "you@example.com"
<python> "<skill-dir>/scripts/zotero.py" daily --dry-run
<python> "<skill-dir>/scripts/zotero.py" daily-schedule --time 08:00
```

每次最多 30 篇，同刊默认最多 3 篇。配置与状态在 `~/.zotero-skill/`，可用 `ZOTERO_SKILL_CONFIG_DIR` 改位置。定时任务用 Windows 任务计划程序，只在用户登录时运行。

## 引用与导出

```text
<python> "<skill-dir>/scripts/zotero.py" search "transformer" --with-bibtex-keys --json
<python> "<skill-dir>/scripts/zotero.py" export-bibtex --item-key PXW99EKT --out references.bib
<python> "<skill-dir>/scripts/zotero.py" sync-bib --out references.bib
<python> "<skill-dir>/scripts/zotero.py" cite --item-key PXW99EKT --tex paper.tex --bib references.bib --marker "<cite:transformer>"
```

- 插入引用前先读草稿，按具体论断匹配文献；`cite --query` 命中多条时会拒绝，请先选定 `--item-key`。
- `sync-bib`、`export-bibtex --out` 会覆盖目标文件，先确认这是用户想要的路径。
- item key 不是引用键，报告时同时给出 BibTeX key。
- 附件与全文：`children <key> --json`、`fulltext <attachmentKey> --out fulltext.txt`、`file-url <attachmentKey>`。

## 导入

导入是写操作：先确认来源、内容与目标分类，用户同意后执行。

```text
<python> "<skill-dir>/scripts/zotero.py" selected-target --json
<python> "<skill-dir>/scripts/zotero.py" import-bibtex --file new.bib --yes
<python> "<skill-dir>/scripts/zotero.py" import-ris --file new.ris --yes
```

需要路由细节时读 [references/local-api-routes.md](references/local-api-routes.md)。本地 API 的 GET 免认证，写入需要本地 API key 与 `Zotero-Server-ID`，脚本已封装；不要直接改 Zotero 数据库。
