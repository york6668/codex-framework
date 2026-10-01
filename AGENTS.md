# Codex 共享框架

本仓的完整用户偏好在 [.codex/AGENTS.md](.codex/AGENTS.md)，开始工作时读取该文件。专业方法位于 `.agents/skills/york-*`，按当前问题选用。

本仓仅保存框架。各设备的历史记忆、会话、业务文件、认证和运行状态不进入仓库。记忆的使用与配置见 [.codex/memory-framework.md](.codex/memory-framework.md)。

修改同步工具时运行 `python3 -m unittest discover -s tests -v`。文件安装和 Git 交付是不同操作；工具不会自动提交或推送。

用户已授权本框架仓库由 Codex 自主维护、提交和推送，无需逐次交用户审核。发布到本仓的 `york-worktree/Codex框架共享` 分支，保持仓库私有；这项授权仅适用于本框架仓库，不改变业务仓库的审核与交付规则。同步命令本身仍不自动提交或推送，由 Codex 完成本次修改与必要验证后执行 Git 交付。
