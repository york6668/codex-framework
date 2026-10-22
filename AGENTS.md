# Codex 共享框架

核心框架、长期规范、Skills、引用及可复用源码由对应 GitHub 项目持有、维护和发布；Mac mini 是数据存储与执行节点，其全局文档和 Skills 是部署副本。本仓 `york6668/codex-framework` 持有共享框架，BTC 专属方法归 `york6668/btc-core-strategy`。本地检出用于开发修订，长期变更回到所属 GitHub 项目发布，再刷新部署副本与任务上下文；未发布修改受保护并明确标识，不形成另一套框架权威。完整职责与任务流程见 [README](README.md)。

本仓的完整用户偏好在 [.codex/AGENTS.md](.codex/AGENTS.md)，开始工作时读取该文件。专业方法位于 `.agents/skills/york-*`，按当前问题选用。

本仓仅保存框架。各节点的历史记忆、会话、业务数据、实际设备配置、认证和运行状态不进入仓库。GitHub 是项目来源，Codex 云端执行环境是可选运行位置；访问 GitHub 不要求先启动另一个 Codex 任务。记忆的使用与配置见 [.codex/memory-framework.md](.codex/memory-framework.md)。

修改同步工具时运行 `python3 -m unittest discover -s tests -v`。文件安装和 Git 交付是不同操作；工具不会自动提交或推送。

用户已授权本框架仓库由 Codex 自主维护、提交和推送，无需逐次交用户审核。发布到本仓的 `york-worktree/Codex框架共享` 分支。用户已确认本仓改为公开仓库；发布仍只包含框架源码、方法和必要合成测试，不纳入凭据、个人或业务资料、节点运行数据及取证原件，不自行改变仓库可见性。这项授权仅适用于本框架仓库，不改变业务仓库的审核与交付规则。同步命令本身仍不自动提交或推送，由 Codex 完成本次修改与必要验证后执行 Git 交付。
