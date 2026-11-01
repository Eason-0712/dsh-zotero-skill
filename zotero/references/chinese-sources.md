# 中文文献来源：知网（CNKI）与 Crossref

本技能支持三种文献来源，各有边界，选错会浪费很多时间。

| 来源 | 能做什么 | 不能做什么 | 命令 |
| --- | --- | --- | --- |
| OpenAlex | 主题检索、期刊影响力、引用速度、相关度打分、每日自动收录 | 中文期刊覆盖很薄，很多中文刊没有 `2yr_mean_citedness` | `find` / `frontier` / `daily` |
| Crossref | 按 DOI 精确元数据，覆盖多数中文核心期刊的英文登记信息 | 没有期刊指标、没有相关度分，也没有知网的被引数 | `find --source crossref` |
| 知网导出文件 | 你亲手挑选的中文文献，含卷期页、关键词、摘要 | 没有公开 API，技能不抓取、不登录、不代理下载 PDF | `cnki-search` + `cnki-import` |

## 为什么不做知网抓取

- 知网没有公开检索 API，只有面向机构的订阅界面；检索页有反爬、验证码与登录态。
- 抓取会违反其服务条款，且页面结构随时变化，稳定性无法保证。
- 因此技能只做两件事：**拼检索链接**（你点开、你自己挑）与**解析官方导出文件**（你导出、技能入库）。

## 操作步骤

1. 生成检索链接。默认只打印，`--open` 才打开浏览器：

   ```text
   <python> "<skill-dir>/scripts/zotero.py" cnki-search "运动想象 脑电解码" --field 主题 --json
   <python> "<skill-dir>/scripts/zotero.py" cnki-search "遮挡 抓取检测" --field 篇名 --open
   ```

2. 在知网页面勾选文献 →「导出与分析 / 导出文献」→ 选 **RefWorks**、**EndNote** 或 **NoteExpress** → 复制或下载 `.txt`。
3. 先预览，再入库：

   ```text
   <python> "<skill-dir>/scripts/zotero.py" cnki-import --file 知网导出.txt --dry-run
   <python> "<skill-dir>/scripts/zotero.py" cnki-import --file 知网导出.txt --yes
   ```

## 解析规则

- 自动识别两种标签格式：
  - RefWorks：`RT`（类型）、`A1`（作者，`;` 分隔）、`T1`/`T0`（篇名）、`JF`/`JO`/`T2`（刊名）、`YR`/`FD`（年）、`VO`、`IS`、`SP`/`EP`（起止页）、`DO`、`AB`、`K1`（关键词）。
  - EndNote / NoteExpress：`%0`、`%A`（可多行）、`%T`、`%J`、`%D`、`%V`、`%N`、`%P`、`%R`、`%X`、`%K`。
- 去重：同一文件内按 DOI 或归一化标题去重；库里已有的默认跳过（`--force` 才会再导入）。
- 写入字段：`title` / `author` / `journal` / `year` / `volume` / `number` / `pages` / `doi` / `keywords` / `language = {zh}` / `abstract`。
- BibTeX 键：中文姓氏与标题词会退化为 `cn` 与 `paper` 前缀，保证键是纯 ASCII，`bibtex`/`biber` 不会因为 UTF-8 键报错；重复时用序号后缀区分。

## 已知限制

- 知网导出的被引次数、影响因子不会进入 BibTeX，也不参与打分；需要量化指标时用 `find --source crossref` 或 OpenAlex。
- 中文文献的 PDF 通常要登录机构账号下载；技能只导入元数据，不代下载。
- 若导出文件里混入英文文献，同样会被解析并按同一套规则入库。
- `cnki-import` 依赖 Zotero Connector（`POST /connector/import`），Zotero 必须正在运行。
