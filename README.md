# Zotero Skill for DeepSeek Harness

[![npm](https://img.shields.io/npm/v/dsh-zotero-skill.svg)](https://www.npmjs.com/package/dsh-zotero-skill)
[![npm downloads](https://img.shields.io/npm/dm/dsh-zotero-skill.svg)](https://www.npmjs.com/package/dsh-zotero-skill)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

让 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 把文献工作串成一条流水线：**找文献 → 追前沿 → 筛核心 → 管理归集 → 批量阅读 → 分类整理 → 提炼脉络 → 发现空白**，再加上导出 BibTeX 与给 LaTeX / Markdown 草稿插入引用。

基础文献管理由 Python 标准库实现，**不需要 MCP 服务或 Zotero 云端 API Key**。Zotero 操作默认连接本机 `127.0.0.1:23119`；论文发现访问 OpenAlex。全文阅读工作流的本地 PDF/OCR 组件首次按需下载，后续复用；分析与翻译使用 DSH 当前模型配置。

## 0.5：全文阅读与可复用的综述证据

在 DSH 中说「精读 Zotero 当前选中的论文」或「比较这个分类中的论文」。插件先展示本次输出、快速/深入模式、讲解深度、PDF 版式、优先内容和执行方式。可保存常用设置和多个研究课题档案；通用插件不预设研究领域。

- **译文与精读**：同一份已接受结果用于 Markdown、Zotero 中文笔记、标注 PDF 和双语 PDF。完整翻译覆盖摘要、正文和实质性附录。精读协议要求说明章节逻辑、方法与公式、实验和消融、贡献、局限及实践步骤。
- **原文依据**：每条分析带块 ID、页码和可匹配的原文片段，分别标记原文报告、分析解释和待验证建议。校验定位不等于自动证明科学结论正确。
- **可选版式**：左右、上下、原文页后跟译页；简洁/详细高亮和中文批注。保留原始页面，较长译文增加续页；识别到的栅格图、表格和公式复制到译文页。复杂矢量图以保留的原文页为准。
- **可恢复与复用**：按源文件指纹缓存 PDF 解析；分批接受译文和分析，显示缺少的块与分析项。完整导出会检查覆盖与源文件变化；显式部分导出只生成带未完成标记的 Markdown。新版导出不覆盖手工修改的旧文件。
- **综述与推荐**：复用单篇证据进行方法、实验协议、结果、局限和实现条件的比较，列出已有材料不足，自动检索带推荐理由的补充候选。候选仅有题录/摘要时明确标明；研究空白保持为待验证假设。
- **省用量默认**：当前会话顺序处理；用户可选 DSH 内置、继承当前模型配置的受限并行。没有子任务能力时回到顺序处理，不配置额外模型 API。

### 首次准备与当前选中条目

Node 20+ 是 DSH 插件的运行前提。包内启动器准备私有 Python 3.12、PDF、OCR 和可嵌入的 Noto 中文字体，不改变系统 Python：

```bash
node lib/reader-runner.js reader-config
node lib/reader-runner.js bridge --out dsh-reader.xpi
```

「当前选中论文」需要将生成的配套扩展在 Zotero 的 **工具 → 插件 → 从文件安装**中安装一次，然后运行已有 `authorize` 流程。扩展要求 Zotero 10 的本地 API 鉴权能力，继承 Zotero 的 API key/server-id 检查，不新增服务或凭据。分类、标签、关键词与明确 item key 无需此扩展。

首次组件下载需要网络，输出进度，后续缓存复用。uv 启动器与字体锁定下载地址和 SHA256；Python 组件安装到私有环境。Noto 字体许可证见 `zotero/references/Noto-OFL.txt`；新模块选用非 AGPL 的 PDF 依赖。Windows 为主要使用环境，macOS/Linux 同样提供运行时映射，仍需各平台实际验证。

### 阅读命令

```bash
# 实际使用由 DSH 根据用户选择运行，无需用户自己编写分析 JSON
node lib/reader-runner.js read prepare --selected --outputs notes translation annotated-pdf bilingual-pdf zotero-note --mode deep --layout side-by-side
node lib/reader-runner.js read prepare --collection COLLECTIONKEY --outputs notes --depth beginner
node lib/reader-runner.js read status read-JOBID
# 当前 DSH 模型读取任务、写出结构化结果后：
node lib/reader-runner.js read accept read-JOBID --input result.json
node lib/reader-runner.js read export read-JOBID --out reading
node lib/reader-runner.js read publish read-JOBID --yes
```

使用 `reader-config --input settings.json` 保存配置，`research-profile NAME --input profile.json` 保存课题档案，`read list` 列出可复用任务；`review prepare/render/search` 用于跨论文比较与自动补充检索。协议与 JSON 格式见 [reading-workflow.md](zotero/references/reading-workflow.md) 和 [review-workflow.md](zotero/references/review-workflow.md)。

**验证边界**：已提供离线 PDF、引用、覆盖、缓存、布局及接口契约测试；未在使用者的 Windows DSH/Zotero 实例完成端到端验证。10–15 页文字型论文首分钟有实质内容、3–5 分钟完成是待测性能目标，模型速度、所选输出、OCR 与首次下载会影响耗时。现有 `digest` 仍是元数据阅读包，不是本流程的模型精读结果。

## 特性

- **找文献**：从 OpenAlex 按主题检索，按期刊影响力、引用速度、相关度、新鲜度排序，并与本地库去重。
- **追前沿**：把常读方向存成 watch，每次只拉「上次运行以来」的新论文，不重复推送。
- **筛核心论文**：把候选或库内条目分成 核心 / 重要 / 参考 三档，输出可复制的短名单。
- **管理文献**：批量打标签、归集到分类、按 DOI / 标题查重并清理副本。
- **批量阅读**：一次为几十篇生成阅读包（元数据 + 摘要 + 章节大纲 + 笔记骨架），不必把原文全塞进上下文。
- **分类整理**：按你想要的维度（论文类型 / 方法 / 数据集 / 年份 / 期刊 / 主题 / 项目）在 Zotero 里直接建文件夹并归入条目，也可以只生成 `auto:method:*` / `auto:dataset:*` 标签计划，预览后写入。
- **提炼脉络**：统计年份、期刊、方法、数据集及共现，直接产出综述可用的表格。
- **发现空白**：按项目分组找「方法 × 数据集」从未交叉的组合、停更标签与标注缺失。
- **检索与引用**：按关键词搜索条目，返回题名、作者、年份、item key 与 BibTeX key；在草稿唯一标记处插入 `\cite{key}` 或 `[@key]` 并同步 `.bib`。
- **每日收录**：按主题每天从 OpenAlex 抓新论文，去重排序后自动导入指定分类，数量 / 开关 / 时间都由你决定。
- **不碰数据库**：只走 Zotero 官方本地 API，不直接读写 `zotero.sqlite`；改库前先 `--dry-run`，写入需要你在 Zotero 弹窗里点一次「Always Allow」。

## 安装

### 1. 准备

- Python 3.10+（Windows 上用 `py -3` 或 `python`，macOS / Linux 用 `python3`）
- 桌面版 Zotero（开发时验证于 Zotero 10.0.6）
- DeepSeek Harness

### 2. 装进 Harness

两种方式，任选其一。

**方式 A：作为 DSH 插件安装（推荐）**

包已发布到 npm，自带 `dsh.bundle` 清单，装成插件后 skill 自动出现在技能目录，不需要复制文件：

```bash
dsh plugin --profile <你的 profile> add dsh-zotero-skill
```

想跟最新代码，或 npm 不可达时，可直接从 GitHub 安装：

```bash
dsh plugin --profile <你的 profile> add github:Eason-0712/dsh-zotero-skill
```

也可以从本地检出安装（开发调试用）：

```bash
dsh plugin --profile <你的 profile> add "link:/path/to/dsh-zotero-skill"
```

卸载：`dsh plugin --profile <你的 profile> remove dsh-zotero-skill`。

**方式 B：作为独立 skill 目录安装**

```text
# 仓库根目录下执行
python install.py
```

安装器默认复制到 `$DSH_HOME/skills/zotero`（未设置时 `~/.dsh/skills/zotero`），目标已存在时不会覆盖。也可以手动把整个 `zotero/` 目录复制过去，最终结构必须是 `skills/zotero/SKILL.md`。

项目级（只在某个仓库生效）：

```bash
python install.py --skills-dir .dsh/skills
```

想装到别的盘或自定义目录，用 `--skills-dir` 指定，再让 Harness 扫描该目录即可。

### 3. 打开 Zotero 本地 API

Zotero → 设置 → 高级 → 其他 → 勾选「允许此计算机上的其他应用程序与 Zotero 通信」。

也可以让脚本改配置（**先完全退出 Zotero**）：

```bash
python zotero/scripts/zotero.py enable --restart
```

它会备份 `prefs.js` 后写入 `extensions.zotero.httpServer.localAPI.enabled`，再启动 Zotero；不会强杀进程。

### 4. 验证

```bash
python zotero/scripts/zotero.py status --json
```

`api_running: true`、`api_status: 200` 即连通。

## 使用示例

在 Harness 里直接说：

> 帮我找一下 knowledge distillation 用在运动想象 EEG 上的新论文，按重要性排个序。

> 我常看的方向有这几个，做成 watch，以后只给我新出现的。

> 从库里 project:MI-EEG 这 18 篇里挑出最核心的几篇，列个短名单。

> 把这批论文生成阅读包，我看完再写综述。

> 按方法和数据集给整个库打上标签，先给我看计划。

> 这个方向现在有哪些明显的空白？

> 读一下 `/path/to/draft.md`，把相关论断后面补上引用，并更新同目录的 references.bib。

Skill 会自己决定调用哪些子命令，并在写库或写文件前先给预览。

## 每日自动收录

每天早上自动把新论文收进 Zotero，数量、主题、开关都由你决定。

### 开启

```bash
# 设定主题与数量（最多 30 篇），并在 Zotero 里先选中一个目标分类
python zotero/scripts/zotero.py daily-config --enable \
  --topics "transformer;large language model" --max 10 --mailto "you@example.com"

# 先看候选，不写库
python zotero/scripts/zotero.py daily --dry-run

# 真正导入
python zotero/scripts/zotero.py daily
```

关闭：`daily-config --disable`。关闭状态下 `daily` 只返回提示，加 `--force` 可临时跑一次。

### 定时

```bash
# 每天 08:00 自动运行（Windows 任务计划程序）
python zotero/scripts/zotero.py daily-schedule --time 08:00

# 删除定时任务
python zotero/scripts/zotero.py daily-schedule --remove
```

任务只在该 Windows 用户登录时触发；Zotero 没开着时脚本会尝试启动它并等待接口就绪。

### 排序依据

候选论文先按主题从 [OpenAlex](https://openalex.org)（免费、无需 API Key）检索最近 N 天，再按加权分数排序：

| 维度 | 默认权重 | 含义 |
| --- | --- | --- |
| 期刊影响力 | 0.45 | 期刊近两年平均被引（OpenAlex `2yr_mean_citedness`） |
| 引用速度 | 0.30 | 年化被引次数，避免老论文靠累计引用刷分 |
| 主题相关度 | 0.15 | OpenAlex 搜索相关度 |
| 新鲜度 | 0.10 | 越新越高，7 天窗口内自然衰减 |

同一期刊默认最多 3 篇（`--max-per-venue`），避免一天全是同一个刊物。已导入过的 DOI 会记录在 `~/.zotero-skill/state.json`，不会重复推送。

### 配置项

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `enabled` | `false` | 总开关 |
| `max_items` | `10` | 每次导入上限，最大 30 |
| `days_back` | `7` | 回溯天数，1–30 |
| `topics` | `[]` | 研究主题，分号分隔 |
| `max_per_venue` | `3` | 同一期刊上限 |
| `include_preprints` | `false` | 是否纳入预印本 |
| `min_venue_score` | `0` | 期刊 2 年被引下限，过滤水刊 |
| `weights` | 见上表 | 各维度权重 |
| `mailto` | `""` | OpenAlex 礼貌池联系邮箱 |
| `schedule_time` | `08:00` | 定时任务时间 |

配置和状态都在 `~/.zotero-skill/`，可用 `ZOTERO_SKILL_CONFIG_DIR` 改位置。**注意**：这里用的是 OpenAlex 的期刊被引指标，不是 JCR 影响因子；两者不等价，别把分数当 IF 用。

## 命令

| 命令 | 作用 |
| --- | --- |
| `status` | 检查本地 API、Connector、版本 |
| `authorize` | 取得本地 API 写权限（Zotero 弹窗点「Always Allow」） |
| `find <query>` | OpenAlex 检索并按分数排序，`--oa-only` / `--json` / `--out` |
| `frontier add\|run\|list\|remove` | 保存研究方向，只拉上次之后的新论文 |
| `screen` | 把候选或库内条目分成 核心 / 重要 / 参考 |
| `tag` | 批量加 / 删 / 替换标签（`--dry-run`） |
| `collect <name>` | 归集到分类，不存在则新建 |
| `classify` | 建文件夹：`方向 / 维度 / 取值`，方向取 `project:` 标签并可 `--rename` 改名；也可生成标签计划；`--apply` 写入 |
| `dupes` | 按 DOI 或标题查重，`--trash` 清理副本 |
| `digest` | 批量生成阅读包（每篇一个 Markdown + index.md） |
| `landscape` | 年份 / 期刊 / 方法 / 数据集统计与共现 |
| `gaps` | 方法 × 数据集空组合、停更标签、标注缺失 |
| `inventory` | 列出条目 |
| `collections` / `tags` / `groups` | 列出集合、标签、分组 |
| `search <query>` | 关键词检索，`--with-bibtex-keys` 附带引用键 |
| `export-bibtex` | 导出 BibTeX（`--item-key` 单篇，`--out` 写文件） |
| `sync-bib` | 导出整库到 `references.bib` |
| `citations` | 按样式渲染引用文本（如 `--style apa`） |
| `cite` | 在草稿标记处插入引用并同步 `.bib` |
| `children` / `fulltext` / `file-url` | 附件、全文、文件路径 |
| `selected-target` | 查看 Connector 当前选中的库/集合 |
| `import-bibtex` / `import-ris` | 经 Connector 导入（需 `--yes`） |
| `daily` | 按主题抓取并按分数导入新论文（`--dry-run` 预览） |
| `daily-config` | 查看/修改每日收录配置 |
| `daily-schedule` | 创建/删除 Windows 每日定时任务 |
| `probe` | 探测各 API 路由可用性 |
| `enable` / `disable` / `restart` | 管理本地 API 开关与启动 |

所有命令加 `--json` 输出机器可读结果。批量选择统一用 `--keys`、`--query`、`--tag`、`--collection`、`--all`，`--limit` 限制条数。

## 目录结构

```text
.
├── package.json                     # dsh.bundle 清单，dsh plugin add 靠它识别
├── cordis.patch.yml                 # 插件挂载行
├── lib/index.js                     # 向 ctx.skills 注册内置 skill 的 provider
├── zotero/
│   ├── SKILL.md                     # skill 入口，Harness 读取它来发现技能
│   ├── references/
│   │   ├── deepseek-harness.md      # 安装、连接排查、环境变量
│   │   └── local-api-routes.md      # 本地 API 与 Connector 路由
│   └── scripts/                    # zotero.py 基础 CLI；paper_reader/ 全文工作流
├── test/provider.test.mjs           # provider 的 Node 测试
├── tests/test_zotero.py             # 模拟 API 的 Python 单元测试
├── tests/test_literature.py         # 术语抽取、分类计划、脉络、查重、空组合
├── install.py                       # 复制式安装器（方式 B）
├── LICENSE
└── NOTICE.md
```

装成插件后，`lib/index.js` 在 `apply()` 里注册一个 rank 600 的 provider，直接读包内的 `zotero/SKILL.md`。相比复制到 `~/.dsh/skills`，升级只需 `dsh plugin` 更新，也不会有两份副本不同步的问题。

## 设计取舍

- **读写都走官方 API**：检索、导出、全文走 `/api/` 的 GET（免认证）；打标签、归集、查重清理走同一套本地 API 的 PATCH/POST，需要本地 API key 与 `Zotero-Server-ID`，脚本用 `authorize` 一次性取得并缓存，绝不直接改 `zotero.sqlite`。
- **先预览再写入**：所有写命令都支持 `--dry-run`，Skill 要求先把计划给用户看，确认后才加 `--yes`。
- **规则可解释**：方法 / 数据集识别是正则词表，分歧能被逐条检查；空组合与停更标签都是可复算的统计，而不是模型编的结论。
- **写文件前备份**：`cite` 会先校验草稿和标记，再备份草稿与 `.bib`；`sync-bib` / `export-bibtex --out` 会覆盖目标文件，所以 Skill 要求先确认路径。
- **本机请求绕过代理**：`localhost`、`127.0.0.1`、`::1` 直连，避免系统代理吞掉本机请求；非回环地址才走代理配置。
- **UTF-8**：Windows 控制台下的中文 JSON 与路径不会乱码；`--input` 也能读 PowerShell 写出的 UTF-16 文件。
- **不强制结束 Zotero**：需要改配置时要求先正常退出，避免 `prefs.js` 被覆盖。

## 测试

```bash
python -m unittest discover -s tests -v   # Python 侧
node --test test/*.test.mjs               # 插件 provider
```

测试用临时文件和模拟 API，不读写真实文献库。GitHub Actions 会在 Ubuntu / Windows 与 Python 3.10 / 3.13 上跑 Python 测试，并在 Node 20 / 22 上跑 provider 测试。

## 已知限制

- Word 里的 Zotero 动态引用域不在能力范围内；脚本不会用替换文本的方式伪装成已插入引用。
- 远端 / WSL / 容器里 `127.0.0.1` 指向的是运行环境本身，不在同一台机器上时无法直接连到桌面 Zotero。
- `enable` / `disable` 只改本机 `prefs.js`，对远端 Zotero 无效。
- 本地 API 的能力随 Zotero 版本变化，遇到 403 / 404 先检查开关和版本。
- 写入需要你在 Zotero 弹窗里授权一次（选「Always Allow」才是持久的）；选「Allow」的 key 只能写一次。
- 方法 / 数据集识别基于固定正则词表，冷门术语可能漏标；`gaps` 会把这些条目列成「标注缺失」供人工补。
- `landscape` / `gaps` 的结论来自题录与摘要的统计，不是全文精读；写进论文前需要回头看原文。
- 每日收录依赖 OpenAlex 的网络访问；离线或被限流时会跳过本次抓取，不会写坏文献库。
- 收录只导入题录与摘要，不下载 PDF；需要全文时再按需获取。
- 期刊指标来自 OpenAlex，与 JCR 影响因子不是同一套数据，仅用于相对排序。
- 定时任务只在该 Windows 用户登录时运行；机器关机或睡眠时当次会跳过。

## 来源与许可

本项目改编自 OpenAI 的 Zotero 插件 0.1.2（MIT）：保留了其 `zotero.py` 与本地 API 路由说明，并针对独立运行、DeepSeek Harness、Windows 和本机代理环境做了修改。

- 原始版权与许可全文见 [LICENSE](LICENSE)
- 来源与改动范围见 [NOTICE.md](NOTICE.md)
- 本项目不是 OpenAI、DeepSeek 或 Zotero 官方发布的适配版本

## English

A [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) skill that runs the whole literature loop against your local Zotero library: find papers on OpenAlex, track research fronts with saved watches, screen candidates into core / important / reference tiers, manage items (batch tags, collections, DOI and title de-duplication), generate batch reading packs, classify items into Zotero folders by type / method / dataset / year / venue / topic / project, chart the landscape of years / venues / methods / datasets, and surface candidate research gaps. It also exports BibTeX, inserts citations into LaTeX/Markdown drafts, and can run a ranked daily digest of new papers.

Install with `dsh plugin --profile <name> add dsh-zotero-skill` (or copy `zotero/` to `~/.dsh/skills/zotero`, or run `python install.py`), enable Zotero's local API, and run `zotero.py authorize` once if you want write operations. Adapted from OpenAI's Zotero plugin 0.1.2 under the MIT license.
