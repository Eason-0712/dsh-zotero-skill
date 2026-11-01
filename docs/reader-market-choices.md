# 文献阅读工具调研与轻量化取舍

核对日期：2026-11-01。范围为 11 个代表性产品/平台的公开官方功能说明，覆盖翻译阅读、文献管理、证据提取和关联发现；不是对全部在售产品的穷尽调查，也不是付费版本的速度或质量实测。只吸收交互与工作流思路，没有复制其代码、界面或专有算法。

| 产品与官方资料 | 值得吸收的思路 | 在本插件的处理 |
| --- | --- | --- |
| [Scholaread](https://www.scholaread.com/help/guides/getting-started/scholaread-integrated-pdf-reading-academic-translation-literature-management-and-note-taking) | 逐段对照、目录导航、选段理解和学术术语 | 逐段双语 Markdown、可回跳目录、聚焦问答和课题术语表；PDF 仍由现有可选导出流程负责。没有重造其移动客户端或专有重排算法。 |
| [SciSpace](https://scispace.com/chat-pdf) | 从具体段落出发解释和追问，答案回到文中 | 针对显式选定的阅读任务检索有限原文，由当前 DSH 模型回答，再检查引文和上下文版本。 |
| [ReadCube](https://about.readcube.com/help-center/readcube-ai-chat-with-pdf/) | 围绕论文上下文问答，并提示核对具体段落 | 复用同一套来源定位、问答和“不足以回答”状态，避免每个问题重读全部 PDF。 |
| [Scholarcy](https://www.scholarcy.com/) | 一致的结构化阅读卡、可调深度与可导出成果 | 第一版已有结构化精读分类与深度选项；本轮加强局部复用、纠错历史和对比表，保持 Markdown 可编辑。 |
| [Elicit](https://support.elicit.com/en/articles/14759154-systematic-reviews-in-elicit) | 研究问题驱动的自定义提取列，单元格有依据，缺失信息可检查 | 自定义论文证据矩阵；单元格必须区分原文、解释、缺失和不可比，并引用该论文已接受的证据。输出 Markdown/CSV 与证据 JSON。 |
| [scite](https://scite.ai/features) | 引用方式和证据质量需要区分，撤稿信息应显著呈现 | 参考/被引关系明确标注；OpenAlex 返回的撤稿标记会提示。没有接入 scite 数据，也不把普通引用自动判断为支持/反驳。 |
| [ResearchRabbit](https://www.researchrabbit.ai/features) | 从已知论文出发探索相关研究和线索 | 有限的一跳参考/被引候选，解释来自哪篇种子论文；保持列表输出，不建立新的图谱应用。 |
| [Litmaps](https://www.litmaps.com/features) | 通过参考和引用联系发现遗漏，持续关注新工作 | 新增有请求上限的一跳检索，与既有 frontier/daily 流程衔接；不新增账户、邮件后台或另一套订阅系统。 |
| [Connected Papers](https://www.connectedpapers.com/about) | 共被引/共同参考所体现的相关性，与直接引用有所区别 | 仅借鉴种子扩展思路。本插件提供的是直接引用关系，不宣称实现其相似度图或大规模图算法。 |
| [Paperpile](https://paperpile.com/features/pdf-annotator/) | 原文批注、颜色区分和开放的笔记/PDF 导出 | 写回 Zotero 原生高亮与中文评论，保留用户修改；Markdown 和标准 PDF 继续使用通用格式。 |
| [Zotero](https://www.zotero.org/support/pdf_reader) | 原生批注、笔记、回到原文和现成引用工作流 | 直接利用本地 API 和内置阅读器；笔记链接可精确指向已生成的原生批注。文献库、阅读器界面、引用管理与同步继续交给 Zotero。 |

## 这轮实施

1. 小范围证据问答：标准库 SQLite FTS5 检索；不可用时采用局部词项检索，不下载嵌入模型、不接入向量服务。中文问题可由宿主提供英文检索词，或复用已有中文分析定位原文。
2. 原生批注：默认预览，需要明确的写入选项才调用 Zotero 的既有鉴权 API；稳定标识避免重复创建，原有评论有变化时保留并报告差异。
3. 文献矩阵：用户自定义列，所有论文都须出现；缺失/不可比显式保留，不用空白掩盖筛选。单元格引用本行论文证据，CSV 对公式前缀作转义。
4. 学术翻译检查：课题术语表、数字/百分比等值正规化检查、未翻译长段检测。它们是确定性的复核线索，不是自动证明翻译正确；可显式使用严格接受模式。
5. 可修订复用：以当前修订号为前提进行纠错，保留不可变快照和理由；内容修正后，旧的问答、矩阵或综述上下文会失效。
6. 有界发现：每次至多 5 篇种子、每种子至多 30 条参考；标注截断、歧义、索引不足与撤稿状态。候选仍需全文判断，不自动纳入为可信证据。

## 体积与交互约束

新增部分只用 Python 标准库和上一版已提供的 PDF 依赖，没有新增第三方推理、向量、Web 或前端依赖。所有功能按请求运行，不启用后台守护进程。日常使用继续通过 DSH 对话与 Zotero 原界面，只有当前任务需要的选项才展示；CLI 和 JSON 协议是宿主执行接口，不要求普通使用者自己维护。

本轮 npm 打包预览：压缩包从约 83 KB 增至约 100 KB，增加约 17 KB；包内不包含论文、缓存、数据库或模型。首次按需下载的 Python/PDF/OCR/字体组件另算，这轮没有增加它们的第三方依赖清单。

大规模图谱可视化、云同步、多人账户、独立文献库、邮件服务和完整写作排版均不在本轮实现范围内。验证边界、Windows 联调与模型耗时目标继续见 [reading-acceptance.md](reading-acceptance.md)。
