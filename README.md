# Codex 共享框架

**核心框架、长期规范、Skills、引用及可复用源码由对应 GitHub 项目持有、维护和发布。Mac mini 是具体的数据存储与执行节点，按发布版本取得框架并执行任务。**

Mac mini 上的全局文档和 Skills 是部署副本，供 Codex 加载使用；本地检出可以用于开发修订，长期变更应回到所属 GitHub 项目。运行数据、历史记录、设备配置和凭据不随框架入库。

| 归属 | 职责 |
|---|---|
| [york6668/codex-framework](https://github.com/york6668/codex-framework) | 共享框架的权威来源，维护用户偏好、9 个 York Skills、引用、记忆使用方法、配置模板、工作站环境与数据生命周期规范及分发工具；发布分支为 `york-worktree/Codex框架共享`。 |
| [york6668/btc-core-strategy](https://github.com/york6668/btc-core-strategy) | BTC 专属方法、核心 Skill 与闭环源码的权威来源；本仓只保留发现入口。 |
| Mac mini | 保存实际数据、历史记录、设备配置和运行状态，按核心框架策略实现环境发现、按需同步、数据保留与清理，提供任务所需工具与执行资源，加载所属 GitHub 项目的部署副本。其他执行节点遵循同一来源关系。 |
| Codex 云端执行环境 | 按需使用的另一执行环境，从同一 GitHub 项目安装框架；其配置、任务和环境发布不等于 GitHub 框架发布。 |

本文的“GitHub 项目”指源码与规范来源，“Codex 云端执行环境”指任务运行位置。Mac mini 直接通过当前可用的仓库连接或 Git 访问 GitHub，读取规范不要求中转到另一个 Codex 任务。本仓为私有仓库，使用独立 Git 历史；旧 GitLab 配置仓与记忆仓仅保留历史用途，现行共享框架以本 GitHub 项目为准。

历史记忆、任务记录、业务代码/数据、会话、认证、工具授权规则、插件包和运行缓存不在本框架同步范围内。业务源码与契约由各业务仓库维护；实际机器配置与数据留在执行节点。

## 任务如何使用框架

1. 冷启动（新建、恢复或清空任务）检查 GitHub 发布版本，补齐缺少的部署内容，对变化做兼容更新，再读取全局和适用仓库规范；Skills 正文及引用按当前需求选用。
2. 按已读取的框架和 Skills 完成用户需求。核心框架定义工作站的职责、策略设置和预期结果；具体数据单元识别项目环境、多环境时取得明确选择、发现服务并保存本地信息，按任务需要同步和核验数据，执行保留、过期与清理策略。方法见 [工作站入口](.codex/workstation/README.md)，工具与实际配置由节点实现。涉及多个节点或局部访问受阻时，按 [多机任务分工](.agents/skills/york-workstation/references/environment-sync.md#按依赖拆分多机任务) 明确各步骤及交接，继续已有授权内不依赖缺口的工作。
3. 发现长期规范或方法缺口时，在所属 GitHub 项目的检出中修订正文及引用，按该项目授权发布；发布后刷新受影响的部署副本，并重新读取当前任务需要的修订。普通业务需求在所属业务仓库处理。
4. 来源暂时不可用时沿用最后有效版本，保留数据和未发布修改，继续不依赖刷新的工作；普通追加消息和上下文压缩不重复联网刷新。

当前会话的新要求立即生效，不等待发布；可复用的长期变更归回所属项目。发布、部署和当前任务读取分别核验，不能以文件已写入证明任务已采用。具体执行见 [同步方法](.agents/skills/york-codex-policy-maintenance/references/framework-sync.md)。

## 在另一台电脑安装

需要 Git、Python 3.9+ 和可用的 GitHub 私有仓库访问权限；配置与认证按 [共享资源入口](.codex/workstation/README.md) 发现。支持 macOS 与 Linux；Windows 可在 WSL 中使用。不要复制其他设备的登录凭据。

```sh
git clone --branch 'york-worktree/Codex框架共享' \
  https://github.com/york6668/codex-framework.git "$HOME/work/codex-framework"
python3 "$HOME/work/codex-framework/scripts/codex_framework.py" install
"$HOME/.local/bin/codex-framework" status
```

安装将偏好与说明放到 `${CODEX_HOME:-$HOME/.codex}`，将 York Skills 放到 `$HOME/.agents/skills`，并创建上述命令入口。源码仓库需保留，入口直接指向其中的脚本；不修改 shell 配置。若 `$HOME/.local/bin` 未加入 PATH，使用下方完整命令路径。

首次安装遇到已有不同内容会标记 `conflict` 并保留双方文件。比较节点副本与 GitHub 发布内容：共享规范修订归回本仓，BTC 专属方法归回 BTC 项目，设备路径与资源线索留在设备说明。保留有效偏好和 BTC 例外，按所属项目授权发布后再刷新；尚未发布的差异明确标为未发布修改，不覆盖、不伪造共同基线，也不将其当成另一套权威规范。不要整目录覆盖。其他 Skill 和设备说明保持原样。

## 任务启动与变更刷新

完成首次兼容迁移后运行 `"$HOME/.local/bin/codex-framework" install-hook`，将 SessionStart 提示合并到实际 Codex home 的 `hooks.json`，保留其他 Hook。首次启用及定义变化通过 Codex 原生 `/hooks` 审阅并信任；安装器不会修改信任记录或启用绕过选项。Hook 只提示当前任务执行检查，不直接联网或运行其他模型。

先复用当前任务可用的 Codex 仓库能力或 GitHub 原生连接，当前入口不可用时回退到本机已有 Git 配置；两边失败标记待用户处理并保留现用规范。不同入口访问的是同一 GitHub 项目，Codex 云端执行环境不是必经中转。具体命令、快照格式、冲突处理与当前任务重读动作见 [同步方法](.agents/skills/york-codex-policy-maintenance/references/framework-sync.md)。普通追加消息和上下文压缩不联网；后续仅明确已发布的规范变化、当前任务完成长期规范发布或用户要求时刷新，不注册每轮 Hook、后台轮询或跨任务通知。

`sync-status` 返回当前已观察与已应用版本；`refresh` 统一接收原生连接内容或本机 Git 内容。默认 `status` / `apply` 使用最后取得的发布内容缓存，避免维护检出较旧时意外退回旧规范；维护本地源码时显式使用 `--repo <路径>`，这只是开发验证，不代表已经发布。同步器程序、测试和 Codex 云端安装脚本在维护仓库更新，不当成共享正文自动执行。

## 手动维护与同步

```sh
"$HOME/.local/bin/codex-framework" status       # 只读比较
"$HOME/.local/bin/codex-framework" pull --apply # 明确手动使用本机 Git 获取并应用
"$HOME/.local/bin/codex-framework" export       # 将部署副本中的未发布修订带回维护检出
```

`pull` 不带 `--apply` 时仅快进仓库并预览设备差异；默认 `apply` 使用最后取得的发布缓存，无缓存时使用维护检出，显式 `--repo <路径> apply` 才指定本地源码，均不访问网络。应用前区分发布内容与开发中的未发布修改。Git 自身保留无关修改，分叉或重叠修改会让快进失败；网络或 Git 失败时不应用设备文件。命令不会切换分支、强制推送、暂存或提交。

每台设备保存自己最后已应用的框架基线。只有上游改变的文件才更新；部署副本中的单方修改显示为 `keep-local`，属于受保护的未发布修订，不能长期作为独立规范分支。框架修订按所属项目授权回写 GitHub；设备配置移到不参与分发的节点说明。双方修改同一文件时保留双方并返回退出码 2，其余可独立更新的文件继续处理。解决内容归属与冲突后刷新相应发布版本，不把未发布内容记为已发布。Git、文件或输入错误返回 1，无冲突返回 0。

`export` 不包含设备的 `config.toml`、历史记忆或新建的无关 Skill，也不会将未发布修改当成已发布的共同基线。本框架仓库已获用户授权，由 Codex 核对修改、完成必要验证后自主提交与推送，无需逐次交用户审核；其他设备再主动同步。同步命令没有后台任务或自动推送行为。

记忆配置片段仅供参考，安装不改写 `config.toml`、不自动开启原生记忆；见 [.codex/memory-framework.md](.codex/memory-framework.md)。设备可选资源说明放在 Codex home 的 `workstation/local.md`，不随本仓分发。

## Codex 云端执行环境

以下是可选执行环境的部署方式。GitHub 仍持有共享框架；Mac mini 直接访问 GitHub 时无需先更新或启动这些环境。

1. 在「设置 → Codex 云端 → 环境」维护私有环境。`codex-framework` 使用本仓的发布分支；`market-roles` 保留业务仓的 `main`，同时接入本框架发布分支作为共享规范来源。GitHub 连接只授予环境所需仓库的访问权限，业务仓不维护共享规范副本。
2. 在框架实际检出目录运行下方安装和状态命令，纳入环境的 Install script；规则明确安装到 `$HOME/.codex`，9 个 Skills 及引用完整安装到 `$HOME/.agents/skills`。业务仓提供依赖契约和配置线索，执行节点按工作站规范准备任务所需能力；共享框架安装本身不启动业务服务或同步业务数据。
3. Start skill 在任务开始时明确读取 `$HOME/.codex/AGENTS.md` 和当前仓库适用说明，并按需要使用 `$HOME/.agents/skills` 下的 York Skills；已读取的规范后续沿用。Codex 云端执行环境按实际安装与启动能力检查和采用 GitHub 发布版本；本机 SessionStart 的版本检查见上节。版本不变且部署完整时不重复安装或执行无关全量测试，worktree 按实际需要选择。业务仓自己的隔离和禁止真实发送边界继续生效。
4. 准备验证后保存并发布环境，用新任务核验规则读取和至少一个适用 Skill。长期规范先发布到 GitHub，再更新受影响环境的源码与安装内容；需要重新发布 Codex 环境时另行核验该结果。已有任务保留自己的状态，采用修订时读取相关部分。仓库刷新不会重新执行安装；GitHub 发布、保存环境配置草稿、发布环境和新任务验证分别报告，接口未支持的步骤不能称为完成。

配置与认证遵循 [共享资源入口](.codex/workstation/README.md)：先使用已有 Codex 云端配置或原生连接，当前入口不可用时再检查本机已有配置。环境准备时引用入口中已确认的配置键，核对是否已请求并提供给任务；各业务仓共用这一入口，不新增变量或重复填写凭据。具体名称、类型和适用范围只在入口维护，凭据值继续由原有配置渠道管理。

Codex 云端执行环境加载共享规则和方法，不获得任何一台设备的历史记忆。原生记忆功能取决于客户端与账号能力，安装脚本不声称开启了该环境的原生记忆。

Codex 云端安装和状态检查沿用同一显式目标；`cloud-install.sh` 透传同步工具已有参数，不改写平台运行时 `CODEX_HOME`：

```sh
# 先进入环境实际检出的 codex-framework 仓库目录
sh scripts/cloud-install.sh --codex-home "$HOME/.codex"
"$HOME/.local/bin/codex-framework" --codex-home "$HOME/.codex" status
```

安装位置与平台运行时 `CODEX_HOME` 不同时，由 Start skill 指向实际共享规则位置；不能只凭文件已复制就断言平台已将它作为全局偏好加载。框架仓根 `AGENTS.md` 也指向同源正文，工作站入口兼容显式安装位置、当前 Codex home 和框架源码。云端检出分支可能由平台命名为 `work`；同步命令不会自动切换分支，环境更新使用现有编辑、仓库刷新、安装和发布能力。

同一主会话可按需要通过已授权的原生任务接口，协调固定在不同 Mac mini 或 Codex 云端执行环境的子任务。读取已有任务帮助定位；向运行中的任务发送消息可能改变其当前执行，先按 [任务对齐](.agents/skills/york-task-alignment/SKILL.md) 核对本次目标、必要依赖和拟影响的对象与优先级，不能把接口可用当成打断或扩大该任务范围的授权。确需交接时发送有界请求并回读结果；执行机、输入版本和输出交接见 [多机任务分工](.agents/skills/york-workstation/references/environment-sync.md#按依赖拆分多机任务)。这不要求迁移既有任务、业务状态或调度，也不将任务间通信作为规范加载前置。历史记忆继续各自保留。

官方说明：[Cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environments)、[Memories](https://learn.chatgpt.com/docs/customization/memories)。

## 开发验证

`python3 scripts/codex_framework.py check < /tmp/current-action.json` 提供无持久台账的项目/任务范围、原生副作用去重、依赖、预算、节点能力、失败恢复和缺陷闭环检查。它处理当前任务从原生工具与节点取得的短暂引用元数据，不读写同步状态；用法与证据边界见[执行与修复的即时检查](.agents/skills/york-task-alignment/references/execution-check.md)。检查结果不能代替平台实际权限或原件核验。

```sh
python3 -m unittest discover -s tests -v
```

测试使用独立临时用户目录和本地 Git 远端，不访问业务服务或修改真实 Codex home。可用 `--home /absolute/test-home` 和 `--codex-home /absolute/codex-home` 指定测试目标；显式 `--home` 会忽略环境中的 `CODEX_HOME`，避免误触真实目录。
