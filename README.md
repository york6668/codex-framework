# Codex 共享框架

## 产品版本

核心从 **0.1.0** 开始以产品版本号规划、选择安装目标和报告实际加载；此前未编号的发布保持未编号，不把历史提交追认为某个产品版本。版本号的唯一源码声明为 `scripts/codex_framework.py` 的 `RELEASE_VERSION`，使用无前导零的 `major.minor.patch`。后续按功能、兼容性和修复范围更新此声明；格式编号 `FORMAT`、输入契约编号和 Git hash 各自保留原用途。

从 `0.1.0` 起，正常入口 `codex-framework version` 显示实际运行的 `version`、源码声明及 runtime 的 `tool_version` / `active_version` / `document_version`。旧未编号程序不支持该子命令；先复用 Hook 或 `sync-status` 的实际运行结果，缺少 `tool_version` 时保留未编号或未知，不从新版正文推定旧命令能力。源码调用的实际运行版本为 null，不能从活动指针或请求字段补成已加载版本。`check` 的 `tool_version` 同样绑定实际物理模块；版本名不证明业务、模型或专家能力。

安装或更新先选择产品版本，例如 `install --release-version 0.1.0`，并继续以原机制核对发布分支、完整 `--revision` 和 `--expected-runtime`。版本名不匹配时不准备或切换包；Git hash 只绑定与验证这份版本内容，不充当主线名称。旧调用保持兼容，不新增所有调用方的必填字段、标签服务或第二发布台账。首次从未编号入口升级时，从已核对发布提交的源码入口执行带版本目标的安装；旧入口不具备新参数，不先试错。

**所有设计与任务发起均基于核心框架支持的 Codex 套餐模型编排；业务、项目及独立任务在其中保留各自职责，数据节点机器承担执行。核心长期规范、Skills、引用及可复用源码仍由所属 GitHub 项目维护和发布。**

Mac mini 上的全局文档和 Skills 是部署副本，供 Codex 加载使用；本地检出可以用于开发修订，长期变更应回到所属 GitHub 项目。运行数据、历史记录、设备配置和凭据不随框架入库。

| 归属 | 职责 |
|---|---|
| [york6668/codex-framework](https://github.com/york6668/codex-framework) | 共享框架的权威来源，维护用户偏好、10 个 York Skills、引用、记忆使用方法、配置模板、工作站环境与数据生命周期规范及分发工具；发布分支为 `york-worktree/Codex框架共享`。 |
| [york6668/btc-core-strategy](https://github.com/york6668/btc-core-strategy) | BTC 专属方法、核心 Skill 与闭环源码的权威来源；本仓只保留发现入口。 |
| Mac mini 等数据节点机器 | 保存实际数据、历史、配置和运行状态，提供编排所需工具与执行资源；由适用核心方法支持的模型编排落实当前要求并核验效果。机器不是独立的设计、任务发起或选择采用主体；不默认全部节点升级全部规范。 |
| Codex 云端执行环境 | 按需使用的另一执行环境，从同一 GitHub 项目安装框架；其配置、任务和环境发布不等于 GitHub 框架发布。 |

本文的“GitHub 项目”指源码与规范来源，“Codex 云端执行环境”指任务运行位置。Mac mini 直接通过当前可用的仓库连接或 Git 访问 GitHub，读取规范不要求中转到另一个 Codex 任务。本仓已由用户确认为公开仓库，使用独立 Git 历史；旧 GitLab 配置仓与记忆仓仅保留历史用途，现行共享框架以本 GitHub 项目为准。

历史记忆、任务记录、业务代码/数据、会话、认证、工具授权规则、插件包和运行缓存不在本框架同步范围内。公司业务源码、契约与长期测试源码由公司 GitLab 维护，本地检出负责实现与测试，不迁入共享框架；运行材料、实际机器配置与数据留在执行节点。获准文档、页面运行资源和效果图按项目交付范围导出。

## 任务如何使用框架

1. 冷启动（新建、恢复或清空任务）检查 GitHub 发布版本，补齐缺少的部署内容，对变化做兼容更新，再读取全局和适用仓库规范；Skills 正文及引用按当前需求选用。
2. 从用户要看到的结果出发，首次复杂需求按 [需求到交付](.agents/skills/york-task-alignment/SKILL.md) 自主选择方法和必要切片；代码设计与验证按 [通用工程](.agents/skills/york-engineering/SKILL.md)，明确小任务直接完成。纠偏时先手动复测本地正常入口的可验证行为，明确通过后再进入真实任务持续验证；长期效果单列后续验收。核心框架定义工作站的职责、策略设置和预期结果；具体数据单元识别项目环境、多环境时取得明确选择、发现服务并保存本地信息，按任务需要同步和核验数据，执行保留、过期与清理策略。方法见 [工作站入口](.codex/workstation/README.md)，工具与实际配置由节点实现。涉及多个节点或局部访问受阻时，按 [多机任务分工](.agents/skills/york-workstation/references/environment-sync.md#按依赖拆分多机任务) 明确各步骤及交接，继续已有授权内不依赖缺口的工作。
3. 发现长期规范或方法缺口时，在所属 GitHub 项目的检出中修订正文及引用，按该项目授权发布；发布后刷新受影响的部署副本，并重新读取当前任务需要的修订。普通业务需求在所属业务仓库处理。
4. 来源暂时不可用时沿用最后有效版本，保留数据和未发布修改，继续不依赖刷新的工作；普通追加消息和上下文压缩不重复联网刷新。

当前有效要求由适用核心框架支持的模型编排承接并落实，不等待发布；长期方法修订仍归所属项目。职责说明不是所有工作必经的层级、流程或新平台，当前只用Codex套餐内原生模型/任务和已获准正常工具/CLI，不设计独立模型API、Key、API计费、外部模型worker或fallback。按当前任务的适用要求分别核送达、执行与效果；版本定位来源，文件分发不代执行，缺失应修正原链路，不留节点待选择项。具体分发见 [同步方法](.agents/skills/york-codex-policy-maintenance/references/framework-sync.md)。

## 方法职责与当前能力

| 层次 / 入口 | 负责的结果与边界 |
|---|---|
| `.codex/AGENTS.md` | 稳定偏好、授权与拒绝、来源/数据/额度保护及方法发现指针。 |
| [需求到交付](.agents/skills/york-task-alignment/SKILL.md) | 首次复杂工作、纵向切片、实际依赖、授权交接、结果回收及父目标接续；简单任务直接完成。模型执行方法，非自动调度程序。 |
| [业务需求承接与项目复测](.agents/skills/york-engineering/references/business-workflow.md) | 业务系统持有需求、规范/执行要求、验收方法、交付标准和实际效果，承接核心能力并核项目落实；本方法只供工程复测建议。本地项目自持源码/tests/工程原件，反馈对应需求结果。明确要求前后台远端效果时，完整说明、两端效果和统一入口仍适用。 |
| [通用工程](.agents/skills/york-engineering/SKILL.md) | 任意语言的需求理解、职责/接口设计、缺陷反馈、验证及两轴审查；[Java](.agents/skills/york-java-test/SKILL.md) 负责 JDK/Maven/JUnit 等执行适配。 |
| [跨仓契约](.agents/skills/york-cross-repo-change/SKILL.md)、[资料](.agents/skills/york-provider-docs/SKILL.md) | 生产消费边界、语义与 mapping；不反向定义用户新目标。 |
| [日志](.agents/skills/york-log-query/SKILL.md)、[本地数据](.agents/skills/york-local-data/SKILL.md)、[工作站](.agents/skills/york-workstation/SKILL.md) | 按疑点取证、隔离数据及节点资源方法；真实系统操作仍按原授权。 |
| [交付](.agents/skills/york-safe-delivery/SKILL.md)、[框架维护](.agents/skills/york-codex-policy-maintenance/SKILL.md) | 工作区/Git 交付及共同规则/分发；正式独立验收不由实施自签。 |
| `check` → `execution_check` → `source_evidence` | 对给定输入关系和来源形状做确定性检查；不自主发现遗漏任务、不认证原件，不证明语义规划或真实完成。 |
| `install/refresh` → 分发正文；`SessionStart` → 版本核对/更新正文/节点摘要 | 白名单及三方更新保护未发布修改；Hook 不在空闲时唤醒模型。`install` 将明确 Git 版本部署为受管理运行包，稳定 loader 固定物理目录后导入；`sync-status.runtime` 核对实际工具、正文和输入契约。普通正文 `refresh` 不获得代码执行权限。 |

通用验证正文由工程方法持有，Java 与交付入口引用；生命周期正文由任务方法持有，跨仓只补契约共存顺序与组合边界。规则索引见 [正文归属](.agents/skills/york-codex-policy-maintenance/references/policy-owners.yaml)。方法存在及可安装只证明静态/分发能力；真实模型发现、规划和执行须由正常获授权交互独立判断。

运行版本链实现与隔离回归见下节；独立验收、发布、真实安装及采用各自取证。原生来源适配与统一有界读取、结果后的主动协调及真实采用仍按后续工作包推进，不移动 BTC 额度权威或节点实际配置。

## 助手协调与持久文件

助手侧的共识整理、任务追踪和进度核收使用 [助手协调方法](.agents/skills/york-task-alignment/references/assistant-coordination.md)，数据权威仍在用户资源和原系统工作站。`coordination` 是 0.2.0 源码新增的显式文件适配能力；[契约与模板](.agents/skills/york-task-alignment/references/coordination-file-contract.md) 说明 `read/view/commit`、CAS、完整事件追加及部分回写恢复。它在创建旧 Task Store 前分流，不登记全部会话，不联网、发送、执行或改变调度。源码声明与候选、已发布版本、节点加载及实际采用分别取证，离线通过不表示平台强制采用。

## 只读展开已有义务引用

```sh
python3 scripts/obligation_projection.py /path/to/existing.json \
  --frontier '#/integration_coordination/current_actionable_and_limited_frontier'
```

这是维护仓库中的独立纯读入口。默认同时读取 `first_ready_unassigned`、`assigned_inflight_do_not_duplicate`、`current_real_backlog_action`、`current_authorization_propagation_rootcause` 四族各自的 `_ref` 与 `_refs`；另选字段族时重复 `--family <不含后缀的名称>`。每条来源位置都保留，解析到同一实际 JSON pointer 的引用只展开一次。

支持本文件 JSON pointer/fragment 及唯一精确 `workstreams[id=...]` 选择；普通字段名优先，重复 ID、错误转义、空/坏引用分别报具体位置。输出保留所引对象的完整 `value` 和文档 hash，逐条区分存储状态、原目标、回合、发送/接收、结果和验收字段；不自动查询原生状态或推断可执行。新回合完成不能代替旧义务验收。拒绝或错误仅留在该行，其他合法引用继续展开；退出 0 表示引用解析无误，2 表示部分引用有误，1 表示文档/入口输入无效，均不是业务验收结果。

原任务在真实通知或既有小时机会核收当前需求成果并实际推进本端合法后继；通知可用时及时处理，小时继续集中核收、发现缺口和推进，不只轮询旧示例/索引。可选复用纯读引用入口，但引用去重不等于事件去重、远端恰好一次或后继已执行。父和总表不作每步前置；跨设备不靠父直接读/发会话，各端只在自身范围消费获准共享内容。此脚本不是队列/调度，未进当前运行包不冒已采用；不沿引用打开别的文件、发消息或改原文档。

## 在另一台电脑安装

需要 Git、Python 3.9+ 和对应 GitHub 仓库的必要访问权限；配置与认证按 [共享资源入口](.codex/workstation/README.md) 发现。支持 macOS 与 Linux；Windows 可在 WSL 中使用。不要复制其他设备的登录凭据。

```sh
git clone --branch 'york-worktree/Codex框架共享' \
  https://github.com/york6668/codex-framework.git "$HOME/work/codex-framework"
python3 "$HOME/work/codex-framework/scripts/codex_framework.py" install
"$HOME/.local/bin/codex-framework" status
```

安装将偏好与说明放到 `${CODEX_HOME:-$HOME/.codex}`，将 York Skills 放到 `$HOME/.agents/skills`，并创建上述命令入口。源码只承担明确的安装/更新引导，正常入口不再指向检出脚本。安装从已核对的发布分支头 Git 对象取代码与正文，未提交差异保留并单列；不修改 shell 配置。更新需要可用的获准源码位置，日常运行不依赖检出仍存在。若 `$HOME/.local/bin` 未加入 PATH，使用下方完整命令路径。

首次安装遇到已有不同内容会标记 `conflict` 并保留双方文件。比较节点副本与 GitHub 发布内容：共享规范修订归回本仓，BTC 专属方法归回 BTC 项目，设备路径与资源线索留在设备说明。保留有效偏好和 BTC 例外，按所属项目授权发布后再刷新；尚未发布的差异明确标为未发布修改，不覆盖、不伪造共同基线，也不将其当成另一套权威规范。不要整目录覆盖。其他 Skill 和设备说明保持原样。

## 固定版本运行与恢复

`install` 部署六个原有 CLI 模块及 `framework_runtime`、`task_runtime`、`task_worker`、`task_coordination`、`task_results`、`delivery_relations`，共十二个模块；从旧受管入口升级时，先按原活动指针、已批准包指纹及完整包校验，静态还原原入口模板并逐字匹配，随后由标准安装事务更新；未知或已改入口保持阻塞；`acceptance_capture` 是独立工具，不在运行闭包。每个完整提交在 Codex home 的 `framework-sync/runtime/versions/<commit>/` 保存模块、正文快照和 hash/模式/输入契约 manifest。`active.json` 指向一个已校验版本，`approved.json` 保存已校验版本的 manifest hash。正常 `$HOME/.local/bin/codex-framework` 是无仓库导入的稳定 loader，每次先固定物理目录、核对整个包，再导入；旧进程继续使用原目录。

```sh
# 已核对且存在于本地 Git 的发布分支头；首次 expected-runtime 使用 none。
python3 /path/to/codex-framework/scripts/codex_framework.py install \
  --revision <完整发布SHA> --expected-runtime <先前active_revision或none>
"$HOME/.local/bin/codex-framework" sync-status
# 明确恢复上次未完成的安装；不覆盖中断后新增的本地内容。
"$HOME/.local/bin/codex-framework" runtime-recover
# 回退到仍保留且先前校验过的完整版本；不从任意目录执行旧代码。
"$HOME/.local/bin/codex-framework" runtime-rollback \
  --revision <保留版本SHA> --expected-runtime <当前active_revision>
```

`--home`、`--codex-home` 和现有 `CODEX_HOME` 选择语义保留，显式 `--home` 不采用环境中的 `CODEX_HOME`。共享的稳定入口属于安装时选定的 Codex home；操作其他目标继续显式传参数，不改平台变量或搬运行数据。来源仓库换位置时，更新显式传 `--repo <新检出>`；正常检查、状态及已安装 Hook 不要求原检出可访问。

运行安装先验证获准来源、完整模块闭包、代码模式/编译、正文快照及契约，再用临时 home 烟测，之后才更新。现有正文白名单仍只接受数据；`refresh --snapshot` 和 `pull --apply` 沿用正文更新语义，不隐式执行快照脚本。工具更新明确使用 `install`。正文到了 Q、工具还在 P 时，`sync-status` 分别报告 `document_status`、实际 `runtime.tool_revision`、活动版本、契约及 override，不冒充整体采用完成。

更新与恢复沿用原同步锁。新调用遇到写锁会返回未就绪，SessionStart 则输出有界未知提示且退出 0；不会为 Hook 等待更新。跨文档、状态、入口和指针的写入前先持久化 `transaction.json`，中断后报告 `update-incomplete`。指针、包 manifest、恢复记录和错误诊断读取均先校验对象及字段形状；坏状态由 CLI 报结构化未就绪，Hook 输出有界未知并退出 0。恢复只还原仍等于本次写入值的内容；中断后的其他成果保留为具体冲突。首次安装若指针尚未切换，稳定 loader 仅允许从已准备包进入 `sync-status`/`runtime-recover`，不以开发源码作日常后备入口。

已识别的旧源码 launcher 会原位迁移；未知入口保持不动。已有本框架 SessionStart 命令在运行安装中迁到稳定入口，匹配同时核对有效 home/Codex home，保留原解释器、参数和属性；异目标、缺少明确目标或无法解析的命令不改写。显式 `install-hook` 也按同一身份判断，仅合并同一匹配器内执行参数和属性相同的重复项；缺少当前处理器时才添加默认项。配置 `--native-cli` 时，为实际原生读取和有界更新正文调整本框架处理器的超时及上下文额度，保留更大的已有额度；定义变化仍须重新原生审阅。首次启用仍用 `install-hook`。命令定义变化仍须原生 Hook 审阅，工具不修改信任库或安全设置，手动 Hook 合成验证不证明真实冷启动已信任或注入。

回退一起恢复相容的正文、工具和契约，保留已知远端版本与明确的 `rolled-back` 状态；本地有效修订继续保护。运行中、冻结复现或未决恢复可能引用的旧目录和失败准备内容不自动删除，退出引用后再按明确范围清理；它们不是另一套活动实现。失败入口诊断保留在运行目录的 `failures/`，不进入源码仓库。

## 任务启动与变更刷新

完成首次兼容迁移后，核对本机正常 Codex CLI，运行 `"$HOME/.local/bin/codex-framework" install-hook --native-cli <Codex绝对路径>`。处理器在启动、恢复及清空时直接读取发布版本、核对部署与运行版本，并复用现有同步器兼容刷新规则。它通过原生 `app-server` 的临时工具上下文读取已有 GitHub 连接，不启动模型回合、不保存聊天、不建立后台调度。无配置时发布结果为 UNKNOWN。全局已由原生指令链装入且未变化时不重复注入；本次更新正文有界进入当前上下文，超过范围仍明确未注入。程序检查完成、模型取得正文与实际采用分别验证。

首次启用及定义变化通过 Codex 原生 `/hooks` 审阅并信任；安装器不改信任记录或绕过选项。手动调用 handler 只证明脚本行为；还须从正常启动、恢复及清空观察 Hook、版本结果、原生规则上下文及实际需求行为。普通状态输入也须由入口完成版本检查，不能以模型未调用工具视为已检查；反之，入口已完成的检查不要求模型重复做。若当前平台不提供可观察证据，明确保留未验证，不以配置存在或未来任务代替。

先复用当前任务可用的 Codex 仓库能力或 GitHub 原生连接，当前入口不可用时回退到本机已有 Git 配置；两边失败标记待用户处理并保留现用规范。不同入口访问的是同一 GitHub 项目，Codex 云端执行环境不是必经中转。具体命令、快照格式、冲突处理与当前任务重读动作见 [同步方法](.agents/skills/york-codex-policy-maintenance/references/framework-sync.md)。普通追加消息和上下文压缩不联网；后续仅明确已发布的规范变化、当前任务完成长期规范发布或用户要求时刷新，不注册每轮 Hook、后台轮询或跨任务通知。

`sync-status` 返回当前已观察与已应用版本；`refresh` 统一接收原生连接内容或本机 Git 内容。默认 `status` / `apply` 使用最后取得的发布内容缓存，避免维护检出较旧时意外退回旧规范；维护本地源码时显式使用 `--repo <路径>`，这只是开发验证，不代表已经发布。同步器运行代码只由上述明确的版本化 `install` 更新，不当成普通共享正文快照自动执行；测试及云端 bootstrap 脚本留在维护仓库。

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

## 持久任务执行入口

核心持续工作由 Codex App 原生任务、用户控制的调度和原有结果记录承载，模型负责实际回合中的目标推进与验证，见[任务对齐](.agents/skills/york-task-alignment/SKILL.md)。下述 `task` 是按需使用的有限关系/验收适配，不是 App 的顶层任务库或调度层，不是每条聊天的必经入口；不以另起 Agent、Temporal、API Key 或独立计费作为前提。

`codex-framework task` 读取 JSON 请求，在节点 Codex home 的 `task-runtime/` 保存已登记任务的需求、验收、当前执行、结果和下一动作。记录不进 Git，也不替代业务总表；模型负责从已授权原输入准备登记和修订。安装不自动接入现有桌面或云端任务；Goal、Hook、消息回收和实际采用分别验证。

`register` 接收 `task_id` 和 `spec`。spec 包含原 `request_id`、需求正文、`acceptance_version`、准确 `workspace` 和 `steps`；每步声明 `id`、当前指令、`depends_on`、可证伪的 `checks`。检查支持精确文件 SHA256、精确结果文本、已获准且可重复的验证命令。验证先执行命令，再只读核文件；不能把真实变更、不可恢复动作或秘密处理藏在验证命令里。每步 `max_runs` 上限1–8；`review_required`、`load_required` 按实际验收使用。已有授权覆盖的步骤不另加审批条件。

调用方以原入口输入字节、修订和作用域负责登记，控制器不从另一会话的最新消息猜需求。`amend` 带当前 `expected_revision` 显式替换需求及验收，保护原失败、独立请求和未决生产者。文案、版本、步骤改名或临时移除不能清除已知同对象限制。workspace 只是相对路径锚点；文件按规范绝对目标和限制形成时记录的既有 device/inode 比较，覆盖硬链接、改名及文件系统实际认定的大小写/Unicode 别名。原路径被替换也不撤销其路径限制；相同内容或通用指令文案不证明不同对象/分支相同。符号链接和越界相对路径继续拒绝。任意工具业务语义仍由准确原输入与登记方负责。

限制在状态投影、预约派发、发送提示前、原生会话开始、结果回收、验证和验收重新核对。旧 `ready` 不是下次执行许可；调用间已完成的对象变化必须进入当前判断。提示前发现变化则停止发送任务提示；提示后才发现则停止该步骤并保留可能已发生的效果，不重发原动作、不假装撤销。当前限制、执行回执和未知效果分别保留，后续复核不覆盖先前未决事实。只有实际依赖受影响；其余获准步骤在剩余预算内继续。

这是协调器约束，不是 OS 文件隔离。任务锁只保护状态 CAS，不能阻止外部进程在核对后修改文件，也不能拦截工作方的所有嵌套工具、终端或直发。默认路径适用于原权限足够、由执行方配合遵守约束的任务；不能因几次 `stat` 一致就声称对象不可变。对确需保证对象在任意并发写入下稳定、原子快照或受保护写入的步骤，模型应保留该要求，可登记 `stable_objects_required: true`。当前原生 CLI 适配没有已核实的此类 OS 隔离能力，这一步返回具体能力缺口，不启动工作方或验证命令；不接受 `isolated` 自报放行，独立步骤继续。原平台权限、逐次审批和明确拒绝始终有效。

`run` 带当前版本，每次最多1–4个原生回合，默认3个。先持久化执行意图与对象观察，再沿现有账户/配置运行 `codex exec/resume --json`；进程和原生会话身份在原入口登记。每个自然返回后必经当前验收，模型 final、exit0、`turn.completed` 不能直接完成需求。验证器缺失、读取失败、超时、无进展及原生失败各留准确分支诊断，不自动重放结果不明的命令。文件核验在实际打开的 FD 上先 `fstat` 判对象，再流式读取，并复核该对象和目标路径；这只绑定控制器的读取，不隔离原生工作方的写入或其他进程。验收记录是有界观察，不是原子多文件快照或持续不变承诺；观察后外部对象变化须重新查证，不能盲目恢复旧文件。

验证 I/O 在专用、短命的 POSIX 子作业中执行，父进程有独立期限；子作业关闭继承的任务锁和原生管道，避免一个挂起的读操作继续占住协调状态。元数据探测默认2秒；文件检查默认5秒、256 MiB，可在该 file check 显式给 `timeout`（1–60秒）和 `max_bytes`（最多1 TiB）。命令沿原 `timeout`，输出流式哈希，默认总输出8 MiB，可给 `max_output_bytes`（最多1 GiB）；不收集完整输出到内存。目标以非阻塞、no-follow 方式取得实际FD，再核普通文件类型与身份，FIFO不等待写端。父进程的期限覆盖打开、类型核实和读取，不依赖子作业先返回；一次错误停止本步后续验证并继续独立步骤。同次调用不反复探测已经超时的同一元数据目标，但成功快照不跨决策缓存。超时、超量或子管道未关闭记录局部失败和可能副作用，不自动重放命令。正常结束先自然回收；必要清理仅针对本作业登记的进程组，清理被拒不换信号入口，未回收或内核不响应保留未知。此处不保证终止任意内核状态，也不支持验证命令脱离作业组、提权或留下后台服务。

`resume` 只需原 `task_id`，从持久事实恢复。记录中的控制或工作 PID 仍存在时不抢先验收；代际不能确认保留核对缺口。结果不明先读原生原会话/回合，或在无未确认原生生产者时只读核准确文件效果，不重复模型动作。验证命令开始而回执丢失仍不可重放。原生结果跨修订尚未确认时保持该原执行未决，不能用 `source_ref` 标签或文件已出现代替原生结果。只支持当前入口的进程恢复，不宣称 OS/桌面重启或空闲自动唤醒。

`block`、`approval`、`delivery` 保存原渠道观察；发送 accepted 仅属传输，助手 ACK、委托包装或技术 user-shaped 续办消息不能成为人类决定。当前 CLI 没有可认证新的人类批准入口，`approved` 字段不能扩权。`review` 核原报告字节、当前 `task_input_sha256`、`candidate_tree`、`reviewer_thread` 和 `verdict`；原协调仍核真实独立来源，报告标签不认证身份。`loaded` 核实际 active、完整包 manifest 与 Git tree；`accept` 再核当前证据、原报告、加载身份及对象约束。只发送成功或相近版本不足。

工作方默认沿用现有配置和账户，不新建 API、凭据、付费方式，不代工具批准。只有本任务据实际官方能力选定时才显式登记 `native_model`、`native_reasoning_effort`；不静默换模型或重试不支持名称。成功退出0；`run/resume` 未验收退出2并返回下一动作或具体等待；输入、冲突或必需验证失败退出1。`status` 返回当前约束投影及验收观察，不能把源码、安装或一次回合结束当作整体实际闭环。

任务更新按逐任务锁 → 共享 effects 锁的顺序执行，持锁期间不取得其他任务锁；锁争用、坏索引、条目容量和编码大小在保存任务前检查。预约仍先发布保守占用，再保存任务，之后才允许派发。普通更新先保存任务再写索引，两文件不宣称原子提交：写入阶段失败时退出1，返回 `status:"task_partial_commit"`、`phase`、`task_commit_state`（`committed/not_committed/unknown`）、可证实的 `committed_revision` 和 `index_sync_pending:true`。该修订由当前任务字节读回确认，不保证断电后的多文件持久性；读回失败保留 unknown，不按失败自动重放原动作。`work` 的 `executed`/`intake_errors` 同样保留 `commit` 详情。任务保存前且明确未替换文件的普通失败仍返回原错误。

只在当前范围已获准时，可用 `{"operation":"sync-effects","task_id":"<原任务>","expected_revision":<当前修订>}` 显式恢复索引。先从原任务核对修订及实际效果；此入口不增加任务或输入修订、不重跑验证/核收/执行，不迁移数据，不解除平台拒绝。它按当前持久任务事实合并本任务占用，保留其他任务条目及所有索引拒绝；未决 continuation 仍保留 uncertain。只有持久的已解决 continuation、原执行验证或未发送证据与原占用资源一致时，才移除对应旧占用。预约写出后任务保存失败、未知来源条目或资源不匹配时，保留原占用并报错，由原责任沿原获准入口查证，不用新预约覆盖。重复同步不会重做领域效果；旧 CAS 拒绝。普通 `status` 始终只读，不隐式修复索引。旧 format1 记录及正常请求/成功输出格式继续可用。

未决 continuation 的资源由当前输入与已有 `superseded_input` 中完全相同的 operation/attempt/session/turn 对应输入共同派生。`amend` 可缩减、替换或清空当前声明，但原操作未解时，其历次文件目标、工作区及逻辑资源占用仍保留；不相关输入不加入。索引恢复也使用这些原记录，不新增格式或迁移状态。仅有 completed 终态仍不自动释放；原结果及匹配核收明确置 resolved 后，才按同一历次资源事实释放对应 uncertain，拒绝条目继续保留。无未决原操作的合法修订不创建历史占用。

### 主线与交付的只读关系

task 的 delivery-view 是可选纯读视图，在构造 Store 前返回给定关系，不登记副本、不写状态、不取远端文件或查询原生任务。它的输入合同只约束选用该 API 的调用，不把用户示例固化成三层架构、固定顺序或每条聊天必填格式。原任务围绕真实需求成果接续；核心方法、业务规范承接和本地工程各守职责，业务侧不管理工程版本/原件。各端用已有获准载体交换必要内容；小时任务只负责其实际范围，文件共享不等于远端唤醒。

请求给当前 `baseline={id,source_ref}`、`mainlines=[{id,goal,source_ref,delivery_ids}]`、`deliveries=[{id,goal,source_ref,task_ids,stages}]`、`tasks=[{id,thread_id,turn_id,source_ref}]`，可带已取得的 `native_sources` 和 `observations`。`stages` 按具体交付声明适用的 source/build/install/load/adoption/verification，不适用项不凭空补流程；每项为 `{version,inputs,checks?,independent?}`。version=null 显示尚无冻结目标；inputs 精确关联已声明的上游阶段版本。实施交付与验证交付分开返回，原生 completed 只表示选定回合结束。

每项 observation 给 `delivery_id,stage,case,task_id,baseline,version,inputs,result,source_ref,sha256,report`；report 是原结果 JSON 正文，其指纹与前八个关系字段须匹配，结果为 PASS/FAIL/NOT_RUN/UNKNOWN。新用例不能只改旧报告的 case 名称；相同原任务/回合/用例出现冲突来源不按第一份或摘要时间覆盖。历史范围仅在明确的 `reuse={baseline,source_ref,requirements,code,input,environment,method}` 五轴均为 unchanged、且目标版本/输入/用例仍匹配时复用；有改变就保持待复验。来源和适用性仍由原负责人按真实原件判断，JSON 标签和散列不认证作者、授权或领域结论。

每项交付的 `reported_observations` 保留已绑定原报告全文与来源，包括历史或未选中的用例，避免摘要丢掉原限定；`next_actions` 只指出已声明阶段的具体证据缺口，不提供执行、重试或解除拒绝许可。未采用本视图的原任务同样保留这些事实，不增加必经转换。

此入口只核当前显式关系及原报告字节，不自动读取文件或证明实际部署。source、build、install、load、adoption 的实际证据来自各原入口，当前加载版本的组合仍走适用独验。输出没有执行权限或后台运行保证，不可用它解除原拒绝、UNKNOWN 或真实必要依赖。

### 项目职责和多任务接续

既有任务的 `status` 和 `coordinate/work` 实际消费原 `assignment.owner/delivery_owner`，在 `delivery_responsibility` 中区分分段执行者与最终交付责任；原目标、来源、输入修订、验收版本、结果审阅及剩余义务随下一动作保留。结果审阅交原交付责任，普通失败交原实施者诊断，拒绝及 UNKNOWN 保留准确等待且不授权重试；独立工作仍可推进。该输出只作当前任务的有界判定，不发送消息、不创建执行机会、不认证负责人或原件；`end_to_end_complete:null` 明确不能从分段通过推定接收端已收到可用结果。没有登记原责任时保持未声明，不默认转给助手，也不为普通任务新增必填输入。App、计划或领域任务是否消费此入口、接收端实际效果各自验证。

既有单任务 spec 继续使用。需要多项目协同时，模型按项目来源补 `project={id,goal,responsibility,source_ref,obligations:[{id,acceptance,task_ids}]}` 与 `assignment={owner,role,delivery_owner,covers}`；role 为 implementation、investigation、verification、integration、final_regression 或 coordination。`request_identity={thread_id,turn_id,message_id}` 绑定本轮选定原生输入。它们不证明授权或来源原件真实；旧 PASS 不能替代当前任务输入。`verify-return` 读取 workspace 内给定 path/sha256 的报告，核对 request_id/task_id/project_id/input_revision/task_input_sha256 及适用原生身份，只回报关联状态，不签收项目。

持久任务的本地 `depends_on` 每条边也须有消费者依据。单边 `depends_on_basis` 使用 `{status,source_ref,source_sha256,reason}`，多边按前序步骤 ID 映射；仅声明 proven 或省略部分边都不能制造等待。缺失、hypothesis/unknown、假引用或原件不匹配时，该消费者出现在 `work_partition.unresolved`，不可派发或收取其效果；其他无依赖步骤保持可做。source_ref 是 workspace 内已取得的原契约/观察 JSON 路径，source_sha256 锁定字节；使用既有有界 FD 读取、准确拒绝及共享效果检查，普通文件/体积/内容变化失败明确待核，不搜索原生会话或秘密。原件为 `{producer,consumer,requires:{kind,reason}}`；两端准确包含 task_id、request_id、acceptance_version、workspace 及 step 的 id/instruction/checks/resources（未列 resources 为[]），不包含依赖依据自身以免循环指纹。required_result 说明消费者确需原生产者结果；shared_effect 还核实际声明资源确实不兼容，兼容读不支持硬边。原件来源身份、是否实际必要仍由模型按原任务解释；原文件和标签不授予权限。已证关系沿必要边逐级传播上游失效，缺证仍归后继的 unresolved；external 被动核验按原入口投影为 read，不能用文件接受目标的保守 write 标签证明不兼容共享效果。已证关系只暂停其消费者；需要查证时执行者在原授权内有界取证、明确撤掉假边后再运行，不能自动清掉原拒绝/UNKNOWN。跨任务 `requires=[{task_id,step_id,input_sha256,basis}]` 只阻断依赖准确结果的消费者；step_id=null 表示整个已验任务，否则只消费指定已验步骤。未知来源先查证，不抹去原动作拒绝。步骤 `resources=[{kind,identity,object,mode,operation,source_ref}]` 声明具体效果：file 的 identity 是 workspace 内相对路径，logical 使用已知非敏感资源身份和对象层次；mode 为 read/write/exclusive，操作名独立保存。兼容读取可共享，不同逻辑对象不因同属一库阻断；exclusive 仅覆盖声明对象及子对象。未显式覆盖的文件输出检查按可能写输出登记。原生工具仍执行其权限与确认，关系字段不授权读秘密或绕过拒绝。

`coordinate` 接收明确 `task_ids`，只读回收原记录、项目义务、依赖和占用；不枚举其他任务，不创建第二总台账。`work` 再给逐项 `expected_revisions`、max_turns1–4，在已有授权与当前关系下轮转执行可做项；错误留在原任务，其他独立任务继续。它不会自动取得消息、系统修改或模型用量授权，未调用不宣称后台推进。内部 `.effects` 元数据只用于原执行/未知效果/拒绝的占用，不保存业务待办，也不以换任务解除原限制。

已有外部任务成果可在登记时选 `execution_mode:"external"`，保留原执行方，使用 `verify-effects`（task_id、expected_revision、path、sha256）核验当前返回和已登记的实际文件；不再启动模型，也不伪造 native worker 或增加模型 runs。返回 JSON 须绑定 request_id、task_id、input_revision、task_input_sha256，已声明 project/request_identity 时一并完全相等；verdict 只能 PASS、FAIL、NOT_RUN。此入口只接受被动 file 检查，不接受任意命令并将其自称为读；已获准的验证由原任务沿原工具完成，再核验实际文件。PASS 仍须真实物理检查通过，历史拒绝或未知在途执行不能由该入口清除。`verify-return` 只核当前返回身份，不验收成果；原协调核原生来源，填写身份不能认证来源或授予权限。`work` 对此类任务只在 observations 提供该 task 的 `{path,sha256}` 时核验。未选此模式的既有任务继续沿原 run/resume；外部模式禁止模型 run/resume。

最终收口由 `finalization={components,integration,regression,regression_report:{path,sha256}}`，可同时给出 `tracking` 与 `tracking_file:{path,sha256}` 绑定现有任务。原后续职责直接衔接：分项独验后集成，实际加载同一明确发布集合，再独立跨线总回归，汇总随后按原责任更新；tracking 的同步状态单列，不作为功能交付的许可或硬前置。integration 与 final_regression 的 `artifact={manifest,sha256,versions}` 相同；versions 各项为 `{project_id,repo,ref,commit,tree}`，repo/manifest 相对该任务 workspace。manifest 内容为 `{versions,files:[{project_id,source,loaded,sha256}]}`，逐项核当前 Git ref、tree、source blob 和实际 loaded 文件，缺件不通过。它只验证声明的加载范围，业务部署完整性仍由项目契约和独验负责。最终汇总还读回每个组件的原独验报告及 Git 内容，不能仅凭 task input 相同承接 A 树的验收到 B 树。原报告的 `candidate_tree` 与采用 tree 相同时可复用完整受检树；合并树不同则原独验须明确 `reviewed_files:[{path,sha256,mode?}]` 的受检范围（最多64项），逐项核原树与采用树的普通 blob、字节与 Git mode 相同，并对应 manifest 中实际加载文件。不同组件的有效范围可进入同一组合树；改变受检内容须取得适用新独验，原独验未声明的范围不能由集成方自行缩小。文件/树关系检查不认证审阅者身份或证明领域验收完整。

最终报告须为当前回归任务身份、PASS、artifact_sha256、component_inputs 精确映射、`component_results` 精确映射和独立 reviewer_thread；`component_results` 各组件绑定 task_input_sha256、实际结果 result_sha256、原 review_sha256/candidate_tree、project_id、adopted_commit/adopted_tree 与经过字节核对的 reviewed_files。原件内容、结果或 review 被替换时，旧总回归报告不能只沿用旧输入哈希；若声明 tracking/tracking_file，则核其原清单当前文件哈希并单列 summary.synchronized 与具体缺口；两者可同时省略，不强制新增协调任务。功能依赖版本改变、任务遗漏、旧报告或功能阶段未验，overall_complete 保持 false；汇总未同步不把已验功能重新标成待修，也不清除其原任务上的拒绝。没有 finalization 时仅展示局部进展，不能把全部选中任务 PASS 当总体完结。

正常 `check` 的输出附 `tool_revision`，来自稳定 loader 固定的实际物理模块上下文；源码调用或未匹配上下文为 null。它不从 active 指针、请求字段或环境值补出执行版本，不增加输入必填。消费者不能用 active/document 代替本次实际 tool_revision，加载或入口失败仍保留具体缺口。

## Codex 云端执行环境

以下是可选执行环境的部署方式。GitHub 仍持有共享框架；Mac mini 直接访问 GitHub 时无需先更新或启动这些环境。

1. 在「设置 → Codex 云端 → 环境」维护私有环境。`codex-framework` 使用本仓的发布分支；`market-roles` 保留业务仓的 `main`，同时接入本框架发布分支作为共享规范来源。GitHub 连接只授予环境所需仓库的访问权限，业务仓不维护共享规范副本。
2. 在框架实际检出目录运行下方安装和状态命令，纳入环境的 Install script；规则明确安装到 `$HOME/.codex`，10 个 Skills 及引用完整安装到 `$HOME/.agents/skills`。业务仓提供依赖契约和配置线索，执行节点按工作站规范准备任务所需能力；共享框架安装本身不启动业务服务或同步业务数据。
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

`python3 scripts/codex_framework.py check < /tmp/current-action.json` 提供无持久台账的原任务接续、项目/任务范围、原生副作用去重、依赖、预算、节点能力、偏差反馈、失败恢复及后续纠正接续检查。反馈和缺陷关闭会核对临时输入中的 Codex `read_thread` 原生 item 或节点文件原件；缺少来源、输出截断、命令失败和局部烟测均不能当作目标完成。它不读写另一套任务状态或同步状态；用法与覆盖边界见[执行与修复的即时检查](.agents/skills/york-task-alignment/references/execution-check.md)。检查结果不能代替平台实际权限、来源真实性核对或运行效果解释。

```sh
python3 -m unittest discover -s tests -v
```

测试使用独立临时用户目录和本地 Git 远端，不访问业务服务或修改真实 Codex home。可用 `--home /absolute/test-home` 和 `--codex-home /absolute/codex-home` 指定测试目标；显式 `--home` 会忽略环境中的 `CODEX_HOME`，避免误触真实目录。

## 工作站异常与追溯

`python3 scripts/codex_framework.py workstation-check < /tmp/workstation-check.json` 对当前任务选定的节点状态做有界只读核对。它将已知故障、来源过期/缺失、身份或版本不符、资源清理后置条件不符分别呈现为异常或未知；仅 `normal` 退出 0。来源契约与限制见[工作站异常与可溯源性](.agents/skills/york-workstation/references/observability.md)。该入口不创建采集或调度，也不代替原生任务记录和模型对实际原件的核实。

已安装的 `SessionStart` Hook 还可在本地任务启动或恢复时读取节点来源清单，注入简短异常或未知摘要；摘要缺席时只读运行 `codex-framework workstation-entry` 直读固定清单，不递归扫描工作站。该入口限制来源目录、文件数量和合计字节，旧记录可报告故障却不能证明健康。它不会在任务空闲时唤醒模型；正常启动的自动注入须单独验收。

已有 mi-local 数据节点可采用 [节点观察适配器](adapters/mi-local/README.md)，将正常资源操作与真实协议探测接到上述固定入口。适配器源码和持续回归由本仓持有，连接配置及观察结果留在节点；按节点实际需要部署，不加入核心 CLI 的强制依赖。清单或采集缺失在所属工作站维护范围内修复、实际复测，不能只登记未知后停止推进。

## 有限结果的实际返回核对

对于已经发生返回对象错配的原合并/收件路径，`scripts/return_receipt.py` 的 `produce(expected, committed_readback, bindings, payload=payload)` 可在原成功合并后的同一次读回操作中生成完整有限回执；它不修改总表或发送消息。`expected` 固定当前原生委托与逐对象报告、结论、限制和下一步，`bindings` 核对原有总表中的确切回执与下一步投影及报告指纹。原报告各自的身份保持分开。

主协调在读取候选返回前固定同一完整 `expected`，把 `scripts/return_receipt_receiver.js` 的纯函数 `receiveFinal(contracts, observed)` 放入原收件执行单元：受支持的原生读取取得实际 final 后直接比较全文，再记录接受或拒收。该函数无需访问 Mac 文件、模块或网络；`observed` 由当前原生读取提供 thread_id/final_ref/completed/truncated/text，不能用子任务自述替代。合同须随原委托或可核验返回携带完整内容，路径和摘要不能代替原文；不要执行子任务返回的任意代码。固定审核的接收器源码可直接内联使用。

回执格式是 `finite-return-v2` 的完整规范 JSON，禁止额外正文；报告、限制和下一步均与预期完整相等才接受。多发送方独立请求逐项保留在 `pending_request_refs`，不默认取最新消息；缺失、截断或不明的原生返回保留具体未知。拒收只针对该返回，保护已成功合并成果并继续独立获准工作。全局总表 hash 仅作读回审计，不证明返回主题，也不因无关字段变化拒收同一有效投影。

这是原调用路径被实际采用后的一次事后检出/纠正能力，不是平台自动订阅、发送前拦截、自由文本语义判定或零偏差保证。两个工具不进入安装运行闭包；实际调用证据、独立复核和真实采用分别验收。

发送前由父端在原执行单元调用同一纯 JS 文件的 makeDispatch(senderThread, targetThread, requestId, payload, results)。requestId 是父端本次新建且不复用的请求标识；payload 是父端保存的精确逻辑指令字符串，不裁剪、规范化 Unicode 或转换换行。摘要为该字符串 UTF-8 字节的 SHA-256，排除合同字段、平台包装、子端 FCO 和发送后生成的标识。纯 JS SHA-256 已可在无 Node/crypto/TextEncoder 的 functions.exec 中运行。父端先保存返回的 expected/prompt，再将同一个 prompt 字符串传入原生发送调用；不从子 final 反推 expected。该 packet 是 canonical finite-dispatch-v1 JSON，包含 payload 与 expected；整个 prompt 的另一个摘要只作发送审计，不能与 payload 摘要混用。

子端在原收到委托的操作中对同一逻辑 packet 调用 parse_dispatch(prompt)，再把 packet 的 payload/expected 送入原成功合并读回的 produce。解析器核对完整逻辑 packet、SHA 与原格式，不假定 provider 包装规则，也不以父端标识认证子 FCO 原件或授予权限。父端原生读取实际 final 后仍用发送前保留的 expected 进行全文比较；缺失的原生完成/正文/身份仍为 unknown。多个独立父端请求各自保存，不按最新覆盖。

### 结果尚未知的普通交接

普通独立审查在发送时只知道输入对象和待答问题，不应猜测未来结论、报告 hash 或 PASS 来套用已知结果模式。`scripts/review_handoff.js` 的 `createReviewHandoff(sha256Utf8, receiveFinal)` 复用上述固定接收器，增加 `makeReviewDispatch(sender, target, requestId, payload, subjects, questions, supersedes=null)`。每个输入对象为 `{object_key, ref, sha256}`；问题为 `{question_key, text, object_keys}`。`supersedes` 仅显式引用同一发送者、目标的另一个完整 request；多发送方及同发送方的独立请求各自保留，不依赖单个 active key 或最新消息。

原发送调用直接通过 `sendReview(record, io)` 执行，其中 `record={dispatch, attempt:"prepared", native_receipt:null}`；`io.retain` 在原有交接证据中保存同一 record 并确认读回成功，`io.send(target,prompt)` 调用这次原生消息工具一次。未保留记录不会发送；原调用开始后抛错、无确认或记录再次出现均不自动重发，先回查原任务。此方法只记录所传请求，不创建全局待办、队列或调度，也不能证明未提供的历史请求不存在。调用示例：

```js
const api = createReviewHandoff(sha256Utf8, receiveFinal);
const dispatch = api.makeReviewDispatch(sender, reviewer, newRequestId,
  exactPayload, knownInputSubjects, questions); // 此处没有结果或未来报告
const record = {dispatch, attempt:"prepared", native_receipt:null};
const sent = await api.sendReview(record, {
  retain: retainInOriginalHandoffEvidence, // 原证据持久化并读回，返回 true
  send: (target, prompt) => tools.mcp__codex_app__send_message_to_thread({
    threadId: target, prompt // 沿用该动作既有消息授权
  })
});
```

工作方在自己的原报告生成/读回处调用 `scripts/review_handoff.py` 的 `produce_review(prompt, report_ref, report_readback, request_pointer=..., result_pointer=..., evidence_readbacks=...)`。两个 JSON pointer 指向原报告中实际保存的 request 与 result，适配原报告字段；不写总表。result 保存原输入 subjects、逐对象/问题的 answers、limitations 和 next_scope；answer 为 `{object_key,question_key,status,answer,evidence:[{ref,sha256}]}`。status 可为 completed、failed、limited、blocked、not_run、unknown，不能用已完成的旧主题填缺项。实际答案和证据在工作后产生。证据读回是显式传入的 UTF-8 字节，不沿报告里的任意路径自动取文件；已完成/失败答案必须有证据。原报告可保留其领域 schema，纯文本/大文件/二进制的实质审阅仍由原工作方完成，结构化伴随报告引用其实际字节，不宣称本适配器理解任意文本含义。

完整 final 是 canonical `review-return-v1`，含原 request、实际 report 引用/hash/pointers，以及完整 result；不得加额外正文。原收件动作调用 `readReview(dispatches, selectedRequest, {readNative,readReport})`，把原生 read_thread 回执和原报告/证据读回直接交给它；readReport 由原调用者按获准对象有界读取，适配器不授权任意子端路径。回执支持当前 codex_app schemaVersion1 的 thread/turn/items 外层及其两种已见输入：原生 userMessage 的 codexDelegation，或 cloud_threads.send_message 的 functionCallOutput 委托。按精确发送 prompt、发送者、目标和唯一原 turn 选择真实 final；可见压缩标记不能改变请求，额外 prose 不裁剪，缺失、截断及歧义不通过。

```js
const got = await api.readReview(retainedDispatches, dispatch.contract.request, {
  readNative: target => tools.mcp__codex_app__read_thread({
    threadId: target, turnLimit: 2, includeOutputs: true, maxOutputCharsPerItem: 20000
  }),
  readReport: readOriginalReportAndEvidence // {ref,text,truncated:false,evidence:[{ref,text,truncated:false}]}
});
// binding=bound 只证明当前输入、报告投影、证据字节和实际 final 对应；
// coverage=complete 只证明所有问题有已执行的答案，包括真实失败答案。
// 原协调方实质审查后，才用 decideReview 绑定本次 final/hash/report hash 的决定。
```

`receiveReview` 永不自动给实质 accepted。缺问题或 limited/blocked/not_run/unknown 保留 partial；`decideReview` 不能将 partial 标为完成。即使新头和报告内部一致，旧内容或虚假自报仍须原协调方实质拒收；关系检查不是自由文本语义判定、原件真实性或权限证明。拒收不回滚已成功工作，也不暂停独立获准步骤。

### 执行单元重置和报告预检

每个新 V8 执行单元重新绑定已审核源码中的函数；代码缓存与历史合同分别恢复。先用固定审核的 `sha256Utf8` 检查扩展源码的已知 hash，再求值固定源码；不能执行子端返回的代码。`functions.store` 只作缓存，不能作为派发或验收的唯一原件。没有发生发送的重置只为下一个真正新请求恢复代码；已发送后丢失合同则用 `recoverNativeDispatch(nativeRead, {sender_thread,target_thread,turn_id,prompt_utf8_sha256})`。四个 pin 来自原发送记录：它只从原目标的原 turn 中读取父来源的完整已发送 prompt，核对 SHA 后恢复原 review 或 finite 合同，不从子 final 推造；旧 finite 恢复还调用原 `receiveFinal` 校验合同。只有这一原请求被找回，不能据此宣称全部待办已恢复。需 `includeOutputs:true` 才能读到 cloud_threads 输入正文；原件缺失、超长截断、提供方不支持或 pin 缺失均保留 unknown，禁止重发已可能合并的操作。恢复代码或原合同不等于接受实际返回。`recoverDispatch` 也可从原发送者保存的完整 record 及独立已知 SHA 恢复，仍要求查原任务状态，绝不自动重发。

已知结果合并在原写表前调用 `return_receipt.preflight(expected,payload=payload)`，核对所有报告当时可读、UTF-8 字节和 hash；原报告可以是 JSON 或纯文本。合并后 `produce` 仍核对原投影及最新报告字节。报告变化或返回失败只拒收该次返回，不重做已成功合并；旧失败记录不被新模式追认，补回执需新的明确请求。

这些方法接入调用者实际执行的原 send/read/report 操作，不能全局注册原生回调或拦截模型 final。代表性调用链须逐条记录实际发送、报告读回、真实 final、绑定检查和父方实质决定；单测及一条调用通过不能签所有发送方、所有宿主、自然压缩恢复或后续模型采用。当前 Hook 的注册范围与执行状态另验，不因有工具源码就声明平台级覆盖。

交接库不在当前标准 CLI 的七模块闭包中。标准 `install` 分发运行包与共享正文；原调用者从已核发布提交取得上述 Python/JS 库的确切字节，核对已验指纹后加载到原执行单元。验收分别报告 CLI/正文安装、发布库的实际加载和原生 IO 调用，不把版本指针切换写成库已全局注册。原生补证、人工收讫及受控 pending 的处置按 `york-task-alignment` 的原生补证与结果收讫方法；校验库本身不提供空闲唤醒、持久调度或自动回收。

实际 `cloud_threads.read` 的 `content` 可仅含 `Action completed.`，正文位于 `structuredContent={threadId,latestTurn,items,nextCursor,...}`。适配器直接识别并保留此原结构，不改造成 `schemaVersion:1`。当前已见助手投影没有原始委托或原生 final 身份，因此识别后仍为 `unknown`，返回原游标供原调用者有界核对；不会自动翻页、读取报告或重发。`latestTurn` 的完成状态不能签历史请求 turn。尚缺的输入、分页/排序和 final 身份合同须从原提供方核实，不能由报告自报 request、助手 role 或人为包装补足。

纯函数的64 KiB上限也不扩大宿主原生读回能力：当前 codex_app read_thread 的单项读取配置最多20000字符。完整 final、原输入或证据不可取得时保留 unknown 或格式未匹配，不截取可用片段来通过；支持其他提供方须沿其实际原件另验。


### 原目标结果与当前执行机会的接续

普通未知结果继续使用上面的 `review_handoff`，不预填未来 PASS/hash。
当前宿主轮次可调用 `api.continueReview(record, retainedDispatches, selectedRequest, io)`：
沿原 `readNative/readReport` 核当前输入和实际 final，先经 `io.retain` 在原交接证据
持久化 consumption 意图，再调用一次 `io.consume(bound)`（原消费者）并保存实际
`{status:"committed",receipt_ref}`。这里 committed 仅为该消费者的有限读回，父目标
仍是 pending_original_scope_review。原消费中断、丢回执或 UNKNOWN 时，下次只调用
`io.inspectConsumption(originalIdentity)` 核原效果；未核清不重复消费、模型或投递。
该回调须沿实际已有能力与授权；适配器不提供调度、原生作者认证或新的读/写权限。
实际入口若只返回缺输入/终态的 projection，继续 UNKNOWN，不换产品接口代签。

已采用持久任务的当前原目标，可在 spec 附可选 `result_scope`：
`{object_ref,goal,source_ref,obligations:[{id,acceptance}]}`。不是所有小查询的必填门禁。
原输入经 amend 增加切片后，运行提示与状态的 `requirements` 从既有记录还原累计义务及最早匹配的输入出处；不重播历史阶段指令，不将声明来源冒充人类授权。原件缺失时来源留空，未完义务仍保留；无需为此另建任务或台账。
采用有限适配且当前目标确实来自原生计划时，原 `request_identity` 可附可选
`automation_id`，与原 `thread_id/turn_id/message_id` 一同参与当前输入及结果绑定。
这里 turn/message 标识原目标来源；实际执行回合沿已有 native_result/checkpoint 另记。
这些字段只关联现有原生对象，不认证来源、启停调度或新增权限，也不要求每次 heartbeat amend 输入。

`task collect-result` 在实际工作后读取本任务 workspace 内 `{path,sha256}`：
报告保留原 `request_id/task_id/input_revision/task_input_sha256/request_identity` 绑定，
另有 `object_ref`、实际 `answer:{path,sha256}`、
`native_result:{thread_id,turn_id,final_ref,final_sha256}`、
`answers:[{id,status,method_fit,evidence:[{path,sha256}]}]` 及 `origins`。
status 为 answered/partial/unanswered/unknown，method_fit 为
applicable/limited/not_applicable/unknown。原生字段须来自上面实际已绑定的原 read；
文件中的字段自述不能认证来源或证明专业方法正确。

原独立实质审查实际完成后，`task review-result` 读取同一原请求/对象的报告，
用 `result_sha256` 锁当前实际结果，`reviewer_thread` 标识原独验，
`assessments:[{id,verdict,reason}]` 记录领域判断（PASS/FAIL/NOT_RUN/UNKNOWN）。
只有当前 answered/applicable 且原独验 PASS 的对应义务从 pending 移除；
程序不理解任意数学/市场/诊断推理，必须由原独验核方法、实际过程、结论和原问题覆盖。
已答较窄子问题不会关闭其余义务。amend 保留原目标/未决义务和旧结果历史；
不能静默换对象、删义务或改原验收。accept 和当前读回重新核实际回答/审查字节。
有限程序检查、原生模型终态及用户结果在 `result_dimensions` 分别列出。

`task checkpoint` 只关联原 step 已有 operation/attempt/native 与持久效果：
`origin:{operation_id,attempt_id,session_id,turn_id}`，
`effects:[{kind:"input|freeze|commit|delivery",path,sha256}]`。
它不创建业务 operation，不执行取数、冻结、提交或发送。首次输入/freeze 的引用不可改写。
同一原生执行尚在途时保留该 execution，不能按新名字再运行。
`task continue`（同 task、expected_revision、step）被动核原效果，可附实际保存的
`terminal_ref:{path,sha256}`；该原件为 `{origin,status,source_ref}`，status 为
unknown/running/completed/not_executed/failed。它只接受同一原映射，已知终态不可回写。
NOT_EXECUTED 不撤销已经提交/投递的效果，也不重置原预算；输入取得仍独立保留。
UNKNOWN/running 不启动第二模型、提交或发送；COMPLETE 提示消费原结果。
此入口不替代原 native final 结果消费和领域 operation journal：旧 native 未决执行
仍须在有获准原结果能力时沿既有恢复入口核清，不能用一个 JSON 状态宣告实际恢复。

上述命令只在宿主实际给定的轮次执行。workset 仍不是后台 scheduler；未读到原触发启用、
真实 fire/接收、后续事实与实际消费时，跨轮现场验收保持 NOT_VERIFIED。
资源局部拒绝和本步 UNKNOWN 只限制其动作及真实消费者，独立步骤继续。

原义务初始最多32项，明确修订后累计最多64项；当前实际结果与对应一次实质审查均可
覆盖完整64项，仍使用原文件/IO/调用预算。同一当前输入的不同实际结果及其审查按原义务累积，
后到的部分结果保留前份已核覆盖。结果和审查的相同字节重复收取不改修订或已有覆盖；
同一原生 thread/turn 的返回若报告字节冲突则拒收；
final_ref 或 final_sha256 改变不产生第二原生身份。合法纠偏沿实际当前输入修订和来源，
不在原输入内累积相互矛盾的同一返回。每次输入修订重新要求当前结果，旧输入仅留历史。
后续审查只替代同一结果中明确评估的义务；不同结果出现 FAIL/UNKNOWN 冲突仍待解决。
原记录最多保留64份结果与合计64份审查引用，不复制原文、另建收件台账或扩大调用预算。动态报告、回答、执行证据和终态引用按实际打开的
只读FD对象核本任务及peer限制，未列在原checks中也不跳过既有读拒绝。

被动引用按实际读取对象核限制，不借第一步的身份、文件检查或拒绝。A 的确切对象被拒
不妨碍独立 B 的安全动态报告收取；B 只回答自己范围，A 的拒绝、UNKNOWN、原义务
和外层依赖/验收继续保留。部分结果只列自身真正完成的origins，不把未决A冒作已完成；
未决原operation始终留在原任务记录，不能由缺列/部分结果解除。

当前执行机会需要一起核收已到结果并继续独立工作时，可对原有明确 task_ids 调用
有界 `task work`，附 `results:{task_id:{result:{path,sha256},review:{path,sha256}}}`
（review 可省）。仍须原 `expected_revisions`，先用原 collect/review 入口落同一任务，
再在原1..4次上限内执行可做项；external 项须提供原 `observations`，不启动模型。
某项收取失败只跳过该任务本轮执行，独立项继续；重复收取回 `duplicate_reused`。
`continuation` 给出原目标尚需执行/取证的 next_actions、scoped_waits 及 must_continue/may_wait，
不因有限命令 exit0 或子项返回清除父目标。原清单由其既有 owner 依据本次实际结果更新，
此投影不是第二执行队列。调用方仍负责真实宿主接入、原通知送达及原清单消费；
没有这些实际证据，程序通过不能签自动接续。

已到且可安全读取的当前未审结果在 `reviewable_results` 和原 `next_action/continuation`
中暴露审阅动作。其他原步骤 UNKNOWN 不遮住该动作；实际读取仍核原报告、回答、证据
及 origins 的准确拒绝/未决关系，B 审过也不解除 A 的原 attempt、限制或未答义务。
已审但其范围仍有未决义务的安全结果返回 `continuable_results`，继续原范围查证或纠正，
不要求重复审同一不足结果；已核完 B 后只剩 A 的真实未知时可等待 A。
