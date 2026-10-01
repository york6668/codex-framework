# Codex 工作站

工作站承载当前设备上用户委托 Codex 管理、或在授权任务中创建的开发资源。框架只共享发现与维护方法，不复制资源、凭据、运行状态或业务数据。

| 资源 | 按当前设备发现 |
|---|---|
| 数据库与本地数据工具 | 从业务仓库、现有 Compose 标签和设备已有说明定位；`$HOME/docker/mi-local` 是可选线索。读取实际工具的 README，不假定资源已安装。 |
| Java 与 Maven | 仓库 Wrapper、`command -v mvn`、`java -version`、`JAVA_HOME`；macOS 可补充 `/usr/libexec/java_home -V`。依赖仓库读取当前 settings。 |
| 网络 | 操作系统的连接清单和当前路由；macOS 可用 `scutil --nc list`，Linux 使用现有网络管理工具。已有 VPN 不会随框架迁移。 |
| Git 工作区 | 当前仓库和 `git worktree list`；正常 IDEA 目录继续作为本地审核入口。云端使用该任务的检出目录。 |
| 新独立持久资源 | `${CODEX_HOME:-$HOME/.codex}/workstation/`；不为统一路径迁移现有资源。 |

需要设备特有说明时可使用同目录的 `local.md`；若该文件存在，按当前任务需要读取其中的发现线索。该文件不在框架同步范围内。实际资源信息仍以当前工具、进程、端口、标签和连接核验。

专业方法位于当前用户的 `$HOME/.agents/skills/york-workstation`，相关测试与数据方法按 Skill 入口引用读取。持久资源不会随框架更新或任务结束被删除、关闭或重新初始化。未知资源先辨识，服务管理权不等于其中业务数据的处置授权。
