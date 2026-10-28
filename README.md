# Zotero Skill for DeepSeek Harness

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

让 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 直接操作你本机的 Zotero 文献库：检索论文、导出 BibTeX、给 LaTeX / Markdown 草稿插入引用，按需读取附件全文。

纯 Python 标准库实现，**不需要 pip、MCP 服务或 Zotero 云端 API Key**；默认只与本机 `127.0.0.1:23119` 通信。

## 特性

- **检索**：按关键词搜索顶层条目，返回题名、作者、年份、item key 与 BibTeX key。
- **浏览**：列出集合、标签、分组，或导出整个文献库清单。
- **引用**：读取你指定的 `.tex` / `.md` 草稿，在唯一标记处插入 `\cite{key}` 或 Pandoc `[@key]`，并同步 `references.bib`。
- **安全写入**：修改草稿或 `.bib` 前自动备份；`.bib` 按 key 去重，不重复追加。
- **按需读取**：只有你明确要求时才读取附件路径或 PDF 全文，避免把本地路径和正文无谓地送进上下文。
- **导入**：通过 Zotero Connector 导入 BibTeX / RIS，属于写库操作，脚本会用 `--yes` 显式确认。
- **不碰数据库**：只走 Zotero 官方本地 API，不直接读写 `zotero.sqlite`。

## 安装

### 1. 准备

- Python 3.10+（Windows 上用 `py -3` 或 `python`，macOS / Linux 用 `python3`）
- 桌面版 Zotero（开发时验证于 Zotero 10.0.6）
- DeepSeek Harness

### 2. 放进 skill 目录

用户级（所有工作区可用）：

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

> 检索我文献库中 transformer 相关的论文，给出作者、年份和 BibTeX 键。

> 读一下 `/path/to/draft.md`，把相关论断后面补上引用，并更新同目录的 references.bib。

> 把这篇论文的附件全文读出来，总结方法部分。

Skill 会自己决定调用哪些子命令，并在写文件前确认标记和备份。

## 命令

| 命令 | 作用 |
| --- | --- |
| `status` | 检查本地 API、Connector、版本 |
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
| `probe` | 探测各 API 路由可用性 |
| `enable` / `disable` / `restart` | 管理本地 API 开关与启动 |

所有命令加 `--json` 输出机器可读结果。

## 目录结构

```text
.
├── zotero/
│   ├── SKILL.md                     # skill 入口，Harness 读取它来发现技能
│   ├── references/
│   │   ├── deepseek-harness.md      # 安装、连接排查、环境变量
│   │   └── local-api-routes.md      # 本地 API 与 Connector 路由
│   └── scripts/zotero.py            # 全部功能，仅标准库
├── tests/test_zotero.py             # 模拟 API 的单元测试
├── install.py                       # 安装器
├── LICENSE
└── NOTICE.md
```

## 设计取舍

- **只读本地 API**：检索、导出、全文都走 `/api/`；该接口本身只读，写操作交给 Connector。
- **写文件前备份**：`cite` 会先校验草稿和标记，再备份草稿与 `.bib`；`sync-bib` / `export-bibtex --out` 会覆盖目标文件，所以 Skill 要求先确认路径。
- **本机请求绕过代理**：`localhost`、`127.0.0.1`、`::1` 直连，避免系统代理吞掉本机请求；非回环地址才走代理配置。
- **UTF-8**：Windows 控制台下的中文 JSON 与路径不会乱码。
- **不强制结束 Zotero**：需要改配置时要求先正常退出，避免 `prefs.js` 被覆盖。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试用临时文件和模拟 API，不读写真实文献库。GitHub Actions 会在 Ubuntu / Windows 与 Python 3.10 / 3.13 上运行同一套测试。

## 已知限制

- Word 里的 Zotero 动态引用域不在能力范围内；脚本不会用替换文本的方式伪装成已插入引用。
- 远端 / WSL / 容器里 `127.0.0.1` 指向的是运行环境本身，不在同一台机器上时无法直接连到桌面 Zotero。
- `enable` / `disable` 只改本机 `prefs.js`，对远端 Zotero 无效。
- 本地 API 的能力随 Zotero 版本变化，遇到 403 / 404 先检查开关和版本。

## 来源与许可

本项目改编自 OpenAI 的 Zotero 插件 0.1.2（MIT）：保留了其 `zotero.py` 与本地 API 路由说明，并针对独立运行、DeepSeek Harness、Windows 和本机代理环境做了修改。

- 原始版权与许可全文见 [LICENSE](LICENSE)
- 来源与改动范围见 [NOTICE.md](NOTICE.md)
- 本项目不是 OpenAI、DeepSeek 或 Zotero 官方发布的适配版本

## English

A [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) skill for the local Zotero library: search items, export BibTeX, add citations to LaTeX/Markdown drafts, and read attachment full text on demand. Standard library only — no pip, no MCP, no Zotero API key.

Install by copying `zotero/` to `~/.dsh/skills/zotero` (or run `python install.py`), enable Zotero's local API, then ask your agent to search your library. Adapted from OpenAI's Zotero plugin 0.1.2 under the MIT license.
