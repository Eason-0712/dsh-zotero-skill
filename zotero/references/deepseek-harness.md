# DeepSeek Harness 运行适配

## Skill 发现

将整个 `zotero` 目录直接放在以下任一位置，避免额外套一层目录：

- 用户级：`$DSH_HOME/skills/zotero`，未设置时通常为 `~/.dsh/skills/zotero`。
- 项目级：`<projectRoot>/.dsh/skills/zotero`。项目根通常为最近的含 `.git` 的祖先目录，无 Git 时使用会话工作目录。
- 如果 Harness 配置覆盖了 `dshHome`、禁用了默认目录，或使用自定义技能目录，按实际配置安装。

官方说明与实现（适配核对日期：2026-10-28）：

- https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/subsystems/skills.md
- https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/skill/skill-filesystem/src/index.ts

Harness 使用目录中的 `SKILL.md` 发现和加载说明，再按资源路径运行 Python；无需 `.codex-plugin/plugin.json` 或 `agents/openai.yaml`。默认允许模型和用户调用，不设置 Codex 工具白名单。

安装后让 Harness “加载 zotero 技能，运行 status --json”。若未发现，核对实际 skill 根目录、文件权限和 skill provider 配置，再新建会话或重启 Harness。Skill 只提供流程和脚本，不会自动授予命令执行、网络或文件访问权限。

## 本机连接排查

- `api_running=true`：可继续检索。
- Connector 可用但 API 返回 403/404：检查 Zotero 本地 API 开关及当前版本支持情况。
- 连接拒绝或超时：检查 Zotero 是否已打开、端口、运行环境和防火墙。不要无限重试。
- 代理：脚本对 `localhost`、`127.0.0.1`、`::1` 直连，不将本机请求发送到 HTTP 代理；非回环地址仍使用系统代理配置。
- 权限拒绝：使用 Harness 所提供的授权方式申请本机连接或文件访问，不使用 Codex 专用审批工具名。
- `local_api_enabled_pref=null`：仅表示没有找到显式配置，不代表 API 一定禁用；以实际 API 响应为准。

## 可选环境变量

```text
ZOTERO_EXECUTABLE      Zotero 程序完整路径；支持安装在其他磁盘。
ZOTERO_PROFILE_DIR     含 prefs.js 的配置目录；多配置环境中显式指定。
ZOTERO_LOCAL_BASE_URL  API 地址，默认 http://127.0.0.1:23119。
```

Windows 会自动查询 PATH、注册表 App Paths 和常见安装位置。`restart` 命令保留兼容名称，但只启动或激活程序，不强制关闭程序；需要重启时先由用户正常关闭。`enable` / `disable` 会备份并修改所选配置的 prefs.js，应在 Zotero 已关闭且用户授权时使用。

如果 Harness 运行在 WSL、Docker 或远端服务器，`127.0.0.1` 指向那个运行环境，未必是安装 Zotero 的桌面。优先在 Zotero 所在系统运行脚本；其他方式需要用户配置可信隧道/转发，随后设置 `ZOTERO_LOCAL_BASE_URL`。该变量不会自动建立转发或认证；不要将 Zotero 接口直接暴露到公网。远端 API 模式下，`enable` 等配置命令仍修改脚本所在机器，不操作远端配置。

## 验证边界

此包已用 Python 3.13 验证 CLI、模拟 API 的检索/导出/引用/导入门槛及安装流程。未在用户的 DeepSeek Harness 实例进行端到端验证，也没有验证对真实文献库的写入。实际权限与可用能力取决于安装环境。
