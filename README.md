# Codex 记忆框架

统一来源：私有仓库 `york6668/codex-framework`。发布分支为 `york-worktree/Codex框架共享`。本仓使用新的 Git 历史，现有 GitLab 配置仓和记忆仓继续保留。

本仓共享用户偏好、9 个 York Skills、必要引用、记忆使用方法和可选配置片段。历史记忆、任务记录、业务代码/数据、会话、认证、工具授权规则、插件包和运行缓存不在同步范围内。

## 在另一台电脑安装

需要 Git、Python 3.9+ 和本机自己的 GitHub 私有仓库访问权限。支持 macOS 与 Linux；Windows 可在 WSL 中使用。不要复制其他设备的登录凭据。

```sh
git clone --branch 'york-worktree/Codex框架共享' \
  https://github.com/york6668/codex-framework.git "$HOME/work/codex-framework"
python3 "$HOME/work/codex-framework/scripts/codex_framework.py" install
"$HOME/.local/bin/codex-framework" status
```

安装将偏好与说明放到 `${CODEX_HOME:-$HOME/.codex}`，将 York Skills 放到 `$HOME/.agents/skills`，并创建上述命令入口。源码仓库需保留，入口直接指向其中的脚本；不修改 shell 配置。若 `$HOME/.local/bin` 未加入 PATH，使用下方完整命令路径。

首次安装遇到已有不同内容会标记 `conflict` 并保留双方文件。比较对应的本机文件和仓库文件，将审核后的共享正文放到双方相应文件，再次 `apply` 记录共同版本；选择继续保留差异时，该文件仍显示冲突且不会被覆盖。不要整目录覆盖。其他 Skill 和设备说明保持原样。

## 按需同步

```sh
"$HOME/.local/bin/codex-framework" status       # 只读比较
"$HOME/.local/bin/codex-framework" pull --apply # 获取并应用更新
"$HOME/.local/bin/codex-framework" export       # 导出本机框架修改供审核
```

`pull` 不带 `--apply` 时仅快进仓库并预览设备差异；`apply` 使用当前本地检出内容，不访问网络。Git 自身保留无关修改，分叉或重叠修改会让快进失败；网络或 Git 失败时不应用设备文件。命令不会切换分支、强制推送、暂存或提交。

每台设备保存自己最后已应用的框架基线。只有上游改变的文件才更新；设备独有修改显示为 `keep-local`。双方修改同一文件时保留双方并返回退出码 2，其余可独立更新的文件继续处理。人工解决后再次 `apply` 记录共同版本。Git、文件或输入错误返回 1，无冲突返回 0。

`export` 不包含设备的 `config.toml`、历史记忆或新建的无关 Skill，也不会将未发布修改当成已发布的共同基线。本框架仓库已获用户授权，由 Codex 核对修改、完成必要验证后自主提交与推送，无需逐次交用户审核；其他设备再主动同步。同步命令没有后台任务或自动推送行为。

记忆配置片段仅供参考，安装不改写 `config.toml`、不自动开启原生记忆；见 [.codex/memory-framework.md](.codex/memory-framework.md)。设备可选资源说明放在 Codex home 的 `workstation/local.md`，不随本仓分发。

## Codex 云端

1. 在「设置 → Codex 云端 → 环境」创建环境，只选择 `york6668/codex-framework`，访问范围为 `Only me`。GitHub 连接仅授权该仓库。
2. 使用发布分支，在仓库目录运行 `sh scripts/cloud-install.sh`，作为环境的 Install script。无需安装业务项目、数据库、JDK 或 Maven。
3. 查看 `"$HOME/.local/bin/codex-framework" status`，处理实际冲突后发布环境。根 `AGENTS.md` 指向同一份偏好，Skills 既能从仓库发现，也能安装到环境的标准位置。
4. 发布后用新任务核验中文偏好、记忆边界及至少一个 York Skill；已有任务保留自己的状态。更新框架后，在环境编辑流程中拉取并安装，再重新发布，供新任务使用。

云端加载共享规则和方法，不获得任何一台设备的历史记忆。原生记忆功能取决于客户端与账号能力，安装脚本不声称开启了云端原生记忆。

如果平台注入的 `CODEX_HOME` 是只读目录，在环境的 Install script 中仅为安装和状态命令指定可写用户目录；不更改平台的环境变量或配置：

```sh
# 先进入环境实际检出的 codex-framework 仓库目录
CODEX_HOME="$HOME/.codex" sh scripts/cloud-install.sh
CODEX_HOME="$HOME/.codex" "$HOME/.local/bin/codex-framework" status
```

这时规则由仓库根 `AGENTS.md` 读取同一份 `.codex/AGENTS.md`；Skills 位于仓库 `.agents/skills`，也安装到 `$HOME/.agents/skills`。`$HOME/.codex` 中的副本和同步基线不能证明平台会加载它作为全局偏好，仍需通过发布后的新任务验证。云端检出分支可能由平台命名为 `work`；同步命令不会为此自动切换分支，环境更新使用编辑、仓库刷新、安装和重新发布流程。

官方说明：[Cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environments)、[Memories](https://learn.chatgpt.com/docs/customization/memories)。

## 开发验证

```sh
python3 -m unittest discover -s tests -v
```

测试使用独立临时用户目录和本地 Git 远端，不访问业务服务或修改真实 Codex home。可用 `--home /absolute/test-home` 和 `--codex-home /absolute/codex-home` 指定测试目标；显式 `--home` 会忽略环境中的 `CODEX_HOME`，避免误触真实目录。
