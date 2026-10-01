# Codex 记忆框架

统一来源：私有仓库 `york6668/codex-framework`。发布分支为 `york-worktree/Codex框架共享`。本仓使用新的 Git 历史，现有 GitLab 配置仓和记忆仓继续保留。

本仓共享用户偏好、9 个 York Skills、必要引用、记忆使用方法和可选配置片段。历史记忆、任务记录、业务代码/数据、会话、认证、工具授权规则、插件包和运行缓存不在同步范围内。

环境准备时安装完整共享内容，任务启动时读取全局和适用仓库规范；Skills 正文及引用按需读取，后续沿用。需求变化涉及长期规范时修正本仓并按需应用，普通业务需求只修改所属仓库。当前会话的新要求立即生效；不为后续对话增加实时同步、每轮拉取、重复安装或常驻任务协调。

## 在另一台电脑安装

需要 Git、Python 3.9+ 和可用的 GitHub 私有仓库访问权限；配置与认证按 [共享资源入口](.codex/workstation/README.md) 发现。支持 macOS 与 Linux；Windows 可在 WSL 中使用。不要复制其他设备的登录凭据。

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

1. 在「设置 → Codex 云端 → 环境」维护私有环境。`codex-framework` 使用本仓的发布分支；`market-roles` 保留业务仓的 `main`，同时接入本框架发布分支作为共享规范来源。GitHub 连接只授予环境所需仓库的访问权限，业务仓不维护共享规范副本。
2. 在框架实际检出目录运行下方安装和状态命令，纳入环境的 Install script；规则明确安装到 `$HOME/.codex`，9 个 Skills 及引用完整安装到 `$HOME/.agents/skills`。业务依赖继续由业务仓准备，框架环境本身无需业务服务或依赖。
3. Start skill 在任务开始时明确读取 `$HOME/.codex/AGENTS.md` 和当前仓库适用说明，并按需要使用 `$HOME/.agents/skills` 下的 York Skills；已读取的规范后续沿用。启动不拉取框架、不重复安装或执行无关全量测试，worktree 按实际需要选择。业务仓自己的隔离和禁止真实发送边界继续生效。
4. 准备验证后保存并发布环境，用新任务核验规则读取和至少一个适用 Skill。规范变更后再更新框架源码、安装并重新发布；已有任务保留自己的状态，确需采用修订时读取相关部分。仓库刷新不会重新执行安装；保存配置草稿、发布环境和新任务验证分别报告，接口未支持的步骤不能称为完成。

配置与认证遵循 [共享资源入口](.codex/workstation/README.md)：先使用已有云端配置，没有适用配置时再检查本地是否可用。环境准备时引用入口中已确认的配置键，核对是否已请求并提供给任务；各业务仓共用这一入口，不新增变量或重复填写凭据。具体名称、类型和适用范围只在入口维护，凭据值继续由原有配置渠道管理。

云端加载共享规则和方法，不获得任何一台设备的历史记忆。原生记忆功能取决于客户端与账号能力，安装脚本不声称开启了云端原生记忆。

云端安装和状态检查沿用同一显式目标；`cloud-install.sh` 透传同步工具已有参数，不改写平台运行时 `CODEX_HOME`：

```sh
# 先进入环境实际检出的 codex-framework 仓库目录
sh scripts/cloud-install.sh --codex-home "$HOME/.codex"
"$HOME/.local/bin/codex-framework" --codex-home "$HOME/.codex" status
```

安装位置与平台运行时 `CODEX_HOME` 不同时，由 Start skill 指向实际共享规则位置；不能只凭文件已复制就断言平台已将它作为全局偏好加载。框架仓根 `AGENTS.md` 也指向同源正文，工作站入口兼容显式安装位置、当前 Codex home 和框架源码。云端检出分支可能由平台命名为 `work`；同步命令不会自动切换分支，环境更新使用现有编辑、仓库刷新、安装和发布能力。

本地与云端仅在需要远端执行、配置核验或环境更新时通过原生任务接口协作。读取已有任务帮助定位；用户授权后发送具体请求，再回读执行结果，不将任务间通信作为规范加载前置。历史记忆继续各自保留。

官方说明：[Cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environments)、[Memories](https://learn.chatgpt.com/docs/customization/memories)。

## 开发验证

```sh
python3 -m unittest discover -s tests -v
```

测试使用独立临时用户目录和本地 Git 远端，不访问业务服务或修改真实 Codex home。可用 `--home /absolute/test-home` 和 `--codex-home /absolute/codex-home` 指定测试目标；显式 `--home` 会忽略环境中的 `CODEX_HOME`，避免误触真实目录。
