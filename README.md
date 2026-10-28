# Codex 共享框架

## 产品版本

核心从 **0.1.0** 开始以产品版本号规划、选择安装目标和报告实际加载；此前未编号的发布保持未编号，不把历史提交追认为某个产品版本。版本号的唯一源码声明为 `scripts/codex_framework.py` 的 `RELEASE_VERSION`，使用无前导零的 `major.minor.patch`。后续按功能、兼容性和修复范围更新此声明；格式编号 `FORMAT`、输入契约编号和 Git hash 各自保留原用途。

从 `0.1.0` 起，正常入口 `codex-framework version` 显示实际运行的 `version`、源码声明及 runtime 的 `tool_version` / `active_version` / `document_version`。旧未编号程序不支持该子命令；先复用 Hook 或 `sync-status` 的实际运行结果，缺少 `tool_version` 时保留未编号或未知，不从新版正文推定旧命令能力。源码调用的实际运行版本为 null，不能从活动指针或请求字段补成已加载版本。`check` 的 `tool_version` 同样绑定实际物理模块；版本名不证明业务、模型或专家能力。

安装或更新先选择产品版本，例如 `install --release-version 0.1.0`，并继续以原机制核对发布分支、完整 `--revision` 和 `--expected-runtime`。版本名不匹配时不准备或切换包；Git hash 只绑定与验证这份版本内容，不充当主线名称。旧调用保持兼容，不新增所有调用方的必填字段、标签服务或第二发布台账。首次从未编号入口升级时，从已核对发布提交的源码入口执行带版本目标的安装；旧入口不具备新参数，不先试错。

**所有设计与任务发起均基于核心框架支持的 Codex 套餐模型编排；业务、项目及独立任务在其中保留各自职责，数据节点机器承担执行。核心长期规范、Skills、引用及共享可复用源码仍由所属 GitHub 项目维护和发布。**

执行节点上的全局文档和 Skills 是部署副本，供 Codex 加载使用；本地检出可以用于开发修订，长期变更应回到所属 GitHub 项目。运行数据、历史记录、设备配置和凭据不随框架入库。

| 归属 | 职责 |
|---|---|
| [york6668/codex-framework](https://github.com/york6668/codex-framework) | 共享框架的权威来源，维护用户偏好、10 个 York Skills、引用、记忆使用方法、配置模板、工作站环境与数据生命周期规范及分发工具；发布分支为 `york-worktree/Codex框架共享`。 |
| 领域项目方法 | 归各项目自己的权威来源；助手确认归属，工作站通过既有项目映射提供仓库和方法入口，共享框架不保存私人项目清单。 |
| 执行节点 等数据节点机器 | 保存实际数据、历史、配置和运行状态，提供编排所需工具与执行资源；由适用核心方法支持的模型编排落实当前要求并核验效果。机器不是独立的设计、任务发起或选择采用主体；不默认全部节点升级全部规范。 |
| Codex 云端执行环境 | 按需使用的另一执行环境，从同一 GitHub 项目安装框架；其配置、任务和环境发布不等于 GitHub 框架发布。 |

本文的“GitHub 项目”指源码与规范来源，“Codex 云端执行环境”指任务运行位置。执行节点直接通过当前可用的仓库连接或 Git 访问 GitHub，读取规范不要求中转到另一个 Codex 任务。本框架以本项目发布内容为准；具体组织与项目的来源选择、旧配置迁移和历史状态由助手及工作站在原任务维护，不作为其他机器的默认事实。

历史记忆、任务记录、业务代码/数据、会话、认证、工具授权规则、插件包和运行缓存不在本框架同步范围内。各项目源码、契约与长期测试由其已确认的权威来源维护，本地检出负责实现与测试，不将其他项目原件迁入共享框架；运行材料、实际机器配置与数据留在执行节点。获准文档、页面运行资源和效果图按项目交付范围导出。

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

运行版本链与隔离验证见下节。源码契约、独立验收、公开发布、节点实际安装及消费者采用分别依据已发生的证据判断；具体工作计划和后续事项由助手在原任务维护，项目专用约束的权威来源及节点实际配置不因框架更新改变。

## 助手协调与持久文件

反复修复、复发和原因未明的问题使用[逐问题持续处置](.agents/skills/york-task-alignment/references/issue-continuity.md)，将根治目标与有限阶段分开。缺承载时通过现有 `coordination` 的 `kind=goals` 持久化，`operation=goal-review` 消费模型对真实证据的判断并投影下一行动/有据等待；它复用核心来源校验和任务编排，不执行动作、不另建调度。原任务责任者在已有且获准的执行机会读取原记录，核对在途和结果并按职责承接推进；具体执行安排由原任务与工作站持有，用户停止优先。源码与合成测试不证明自然采用或跨轮根治。

助手侧的共识整理、任务追踪和进度核收使用 [助手协调方法](.agents/skills/york-task-alignment/references/assistant-coordination.md)，数据权威仍在用户资源和原系统工作站。`coordination` 是 0.2.0 源码新增的显式文件适配能力；[契约与模板](.agents/skills/york-task-alignment/references/coordination-file-contract.md) 说明 `read/view/commit`、CAS、完整事件追加及部分回写恢复。它独立于旧 Task Store，不登记全部会话，不联网、发送、执行或改变调度。源码声明与候选、已发布版本、节点加载及实际采用分别取证，离线通过不表示平台强制采用。

## 只读展开已有义务引用

```sh
python3 scripts/obligation_projection.py /path/to/existing.json \
  --frontier '#/integration_coordination/current_actionable_and_limited_frontier'
```

这是维护仓库中的独立纯读入口。默认同时读取 `first_ready_unassigned`、`assigned_inflight_do_not_duplicate`、`current_real_backlog_action`、`current_authorization_propagation_rootcause` 四族各自的 `_ref` 与 `_refs`；另选字段族时重复 `--family <不含后缀的名称>`。每条来源位置都保留，解析到同一实际 JSON pointer 的引用只展开一次。

支持本文件 JSON pointer/fragment 及唯一精确 `workstreams[id=...]` 选择；普通字段名优先，重复 ID、错误转义、空/坏引用分别报具体位置。输出保留所引对象的完整 `value` 和文档 hash，逐条区分存储状态、原目标、回合、发送/接收、结果和验收字段；不自动查询原生状态或推断可执行。新回合完成不能代替旧义务验收。拒绝或错误仅留在该行，其他合法引用继续展开；退出 0 表示引用解析无误，2 表示部分引用有误，1 表示文档/入口输入无效，均不是业务验收结果。

原任务在真实通知或既有获准执行机会核收当前需求成果并实际推进本端合法后继；通知可用时及时处理，在这些机会内继续集中核收、发现缺口和推进，不只轮询旧示例/索引。可选复用纯读引用入口，但引用去重不等于事件去重、远端恰好一次或后继已执行。父和总表不作每步前置；跨设备不靠父直接读/发会话，各端只在自身范围消费获准共享内容。此脚本不是队列/调度，未进当前运行包不冒已采用；不沿引用打开别的文件、发消息或改原文档。

## 在另一台电脑安装

需要 Git、Python 3.9+ 和对应 GitHub 仓库的必要访问权限；配置与认证按 [共享资源入口](.codex/workstation/README.md) 发现。支持 macOS 与 Linux；Windows 可在 WSL 中使用。不要复制其他设备的登录凭据。

```sh
git clone --branch 'york-worktree/Codex框架共享' \
  https://github.com/york6668/codex-framework.git "$HOME/work/codex-framework"
python3 "$HOME/work/codex-framework/scripts/codex_framework.py" install
"$HOME/.local/bin/codex-framework" status
```

安装将偏好与说明放到 `${CODEX_HOME:-$HOME/.codex}`，将 York Skills 放到 `$HOME/.agents/skills`，并创建上述命令入口。源码只承担明确的安装/更新引导，正常入口不再指向检出脚本。安装从已核对的发布分支头 Git 对象取代码与正文，未提交差异保留并单列；不修改 shell 配置。更新需要可用的获准源码位置，日常运行不依赖检出仍存在。若 `$HOME/.local/bin` 未加入 PATH，使用下方完整命令路径。

首次安装遇到已有不同内容会标记 `conflict` 并保留双方文件。比较节点副本与 GitHub 发布内容：共享规范修订归回本仓，领域方法归回对应项目，设备路径与资源线索留在设备说明。保留有效偏好和项目例外，按所属项目授权发布后再刷新；尚未发布的差异明确标为未发布修改，不覆盖、不伪造共同基线，也不将其当成另一套权威规范。不要整目录覆盖。其他 Skill 和设备说明保持原样。

## 固定版本运行与恢复

`install` 按 `scripts/framework_runtime.py` 的 `MODULES` 唯一声明部署完整 CLI 闭包（当前 13 个模块，含 `coordination_files.py`）；从旧受管入口升级时，先按原活动指针、已批准包指纹及完整包校验，静态还原原入口模板并逐字匹配，随后由标准安装事务更新；未知或已改入口保持阻塞；`acceptance_capture` 是独立工具，不在运行闭包。每个完整提交在 Codex home 的 `framework-sync/runtime/versions/<commit>/` 保存模块、正文快照和 hash/模式/输入契约 manifest。`active.json` 指向一个已校验版本，`approved.json` 保存已校验版本的 manifest hash。正常 `$HOME/.local/bin/codex-framework` 是无仓库导入的稳定 loader，每次先固定物理目录、核对整个包，再导入；旧进程继续使用原目录。

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

## 原生执行与旧入口退役

当前持续工作使用已有 Codex 原生任务和调度。原生平台持有执行机会、thread/turn/item 身份、启停和工具权限；模型在实际回合读取原需求、回收原结果、由责任方核收并推进合法后继。业务事实写回原系统，文件和自报字段不提供唤醒或权限，不为同一工作另建执行入口。

`task-runtime` 自建执行入口自 0.4.0 起停止新登记与模型运行：`task register/run/work` 返回 `retired_operation`，不创建记录、不派发、不重放。原有 `task-runtime/` 数据及版本包保持原位。仍有历史消费者或未决效果时，必要的 `status`、仅恢复原执行的 `resume`、原结果核对/收取和条件修复暂保兼容；它们只处理原身份，不作为新任务的登记或调度方式；`resume` 不再派发就绪后继或自动签署核收。

退役按已确认节点和明确来源逐项核对：有效义务由谁承接、原结果能否找回、未知效果如何核对、正常原生入口是否实际运行。单节点无目录不能证明其他节点无任务。核清之前不删除记录或旧版本，不批量重放；核清并完成替代验证的范围再退出安装模块和剩余兼容实现。历史协议见 [0.3.6 原版本](https://github.com/york6668/codex-framework/blob/ec8e228c0227b524aebabf0ec23232f8af8effe9/README.md#持久任务执行入口)，仅用于恢复已有对象，不引导新任务使用。回退运行代码不覆盖业务状态，也不恢复旧任务的执行授权。

## Codex 云端执行环境

以下是可选执行环境的部署方式。GitHub 仍持有共享框架；执行节点 直接访问 GitHub 时无需先更新或启动这些环境。

1. 按当前产品受支持的环境入口维护任务所需执行环境。共享框架使用本仓已批准的发布分支；领域项目使用其实际来源和分支，具体环境、项目与仓库映射由工作站保存。GitHub 连接只授予环境所需仓库的访问权限，领域仓不维护另一套共享规范副本。
2. 在框架实际检出目录运行下方安装和状态命令，纳入环境的 Install script；规则明确安装到 `$HOME/.codex`，10 个 Skills 及引用完整安装到 `$HOME/.agents/skills`。业务仓提供依赖契约和配置线索，执行节点按工作站规范准备任务所需能力；共享框架安装本身不启动业务服务或同步业务数据。
3. Start skill 在任务开始时明确读取 `$HOME/.codex/AGENTS.md` 和当前仓库适用说明，并按需要使用 `$HOME/.agents/skills` 下的 York Skills；已读取的规范后续沿用。Codex 云端执行环境按实际安装与启动能力检查和采用 GitHub 发布版本；本机 SessionStart 的版本检查见上节。版本不变且部署完整时不重复安装或执行无关全量测试，worktree 按实际需要选择。业务仓自己的隔离和禁止真实发送边界继续生效。
4. 准备验证后保存并发布环境，用新任务核验规则读取和至少一个适用 Skill。长期规范先发布到 GitHub，再更新受影响环境的源码与安装内容；需要重新发布 Codex 环境时另行核验该结果。已有任务保留自己的状态，采用修订时读取相关部分。仓库刷新不会重新执行安装；GitHub 发布、保存环境配置草稿、发布环境和新任务验证分别报告，接口未支持的步骤不能称为完成。

配置与认证遵循 [共享资源入口](.codex/workstation/README.md)：先使用已有 Codex 云端配置或原生连接，当前入口不可用时再检查本机已有配置。环境准备时引用入口中已确认的配置键，核对是否已请求并提供给任务；各业务仓共用这一入口，不新增变量或重复填写凭据。具体名称、类型、适用范围与节点映射保存在该工作站的既有配置或 local.md，通用入口只说明发现方法，凭据值继续由原有配置渠道管理。

Codex 云端执行环境加载共享规则和方法，不获得任何一台设备的历史记忆。原生记忆功能取决于客户端与账号能力，安装脚本不声称开启了该环境的原生记忆。

Codex 云端安装和状态检查沿用同一显式目标；`cloud-install.sh` 透传同步工具已有参数，不改写平台运行时 `CODEX_HOME`：

```sh
# 先进入环境实际检出的 codex-framework 仓库目录
sh scripts/cloud-install.sh --codex-home "$HOME/.codex"
"$HOME/.local/bin/codex-framework" --codex-home "$HOME/.codex" status
```

安装位置与平台运行时 `CODEX_HOME` 不同时，由 Start skill 指向实际共享规则位置；不能只凭文件已复制就断言平台已将它作为全局偏好加载。框架仓根 `AGENTS.md` 也指向同源正文，工作站入口兼容显式安装位置、当前 Codex home 和框架源码。云端检出分支可能由平台命名为 `work`；同步命令不会自动切换分支，环境更新使用现有编辑、仓库刷新、安装和发布能力。

同一主会话可按需要通过已授权的原生任务接口，协调固定在不同 执行节点 或 Codex 云端执行环境的子任务。读取已有任务帮助定位；向运行中的任务发送消息可能改变其当前执行，先按 [任务对齐](.agents/skills/york-task-alignment/SKILL.md) 核对本次目标、必要依赖和拟影响的对象与优先级，不能把接口可用当成打断或扩大该任务范围的授权。确需交接时发送有界请求并回读结果；执行机、输入版本和输出交接见 [多机任务分工](.agents/skills/york-workstation/references/environment-sync.md#按依赖拆分多机任务)。这不要求迁移既有任务、业务状态或调度，也不将任务间通信作为规范加载前置。历史记忆继续各自保留。

官方说明：[Cloud environments](https://learn.chatgpt.com/docs/environments/cloud-environments)、[Memories](https://learn.chatgpt.com/docs/customization/memories)。

## 开发验证

`python3 scripts/codex_framework.py check < "$CHECK_INPUT"` 提供无持久台账的原任务接续、项目/任务范围、原生副作用去重、依赖、预算、节点能力、偏差反馈、失败恢复及后续纠正接续检查。`CHECK_INPUT` 是调用者已生成的本次短时输入文件，不是固定目录或新增认证配置。反馈和缺陷关闭会核对临时输入中的 Codex `read_thread` 原生 item 或节点文件原件；缺少来源、输出截断、命令失败和局部烟测均不能当作目标完成。它不读写另一套任务状态或同步状态；用法与覆盖边界见[执行与修复的即时检查](.agents/skills/york-task-alignment/references/execution-check.md)。检查结果不能代替平台实际权限、来源真实性核对或运行效果解释。

```sh
python3 -m unittest discover -s tests -v
```

测试使用独立临时用户目录和本地 Git 远端，不访问业务服务或修改真实 Codex home。可用 `--home "$TEST_HOME"` 和 `--codex-home "$TEST_CODEX_HOME"` 指定测试目标；两个变量由本次隔离工作区创建并填写，不能指向实际用户配置。显式 `--home` 会忽略环境中的 `CODEX_HOME`，避免误触真实目录。

## 工作站异常与追溯

`python3 scripts/codex_framework.py workstation-check < "$CHECK_INPUT"` 对当前任务选定的节点状态做有界只读核对，输入文件由当前任务在可用临时目录生成。它将已知故障、来源过期/缺失、身份或版本不符、资源清理后置条件不符分别呈现为异常或未知；仅 `normal` 退出 0。来源契约与限制见[工作站异常与可溯源性](.agents/skills/york-workstation/references/observability.md)。该入口不创建采集或调度，也不代替原生任务记录和模型对实际原件的核实。

已安装的 `SessionStart` Hook 还可在本地任务启动或恢复时读取节点来源清单，注入简短异常或未知摘要；摘要缺席时只读运行 `codex-framework workstation-entry` 直读固定清单，不递归扫描工作站。该入口限制来源目录、文件数量和合计字节，旧记录可报告故障却不能证明健康。它不会在任务空闲时唤醒模型；正常启动的自动注入须单独验收。

节点通过已有资源管理工具将正常操作与真实观察接到上述固定入口。具体服务、连接、操作副作用及专用观察实现与有效回归由该项目或本机工具承接，从工作站入口发现；本仓维护通用读取契约，不预设节点技术栈。清单或采集缺失在所属工作站维护范围内修复、实际复测，不能只登记未知后停止推进；不为保留历史专用实现另建通用服务。

核心 0.3.1 也支持 [普通文件元数据适配器 0.1.0](adapters/file-metadata/README.md)：按明确来源分别呈现候选、安装、保存的加载、选择、回退保留、效果、健康和退役信息。资产投影保留各来源行及活动失败，历史范围注记单独呈现且不认证完成。适配器为可选源码，未加入受管运行包，不自动改节点清单或运行；元数据不证明健康、Hook 生效或领域效果。

## 原结果交接与核收

机器核收读取原结果，聊天 final 说明实际结果、限制和后继。原请求身份、当前输入及原生 thread/turn/item 与结果引用共同关联本次工作；格式适配不替代实质验收。缺失、冲突、截断或未核格式保持错误或 UNKNOWN；新返回不清除旧拒绝、未答义务和未知效果。

可选的 `review_handoff.py/js` 由原调用者在已授权读取中使用，具体见[原结果交接](.agents/skills/york-task-alignment/references/result-handoff.md)。库不增加平台回调、队列或权限，也不注册全局拦截。旧 `review-return-v1` 和有限结果回执仅保留已有请求兼容；新工作不要求 final 与整段 JSON 相同。

当前受管 CLI 模块由 `scripts/framework_runtime.py:MODULES` 声明，共 13 个；七模块仅指早期兼容版本。交接库不在此闭包中，CLI/正文安装不能证明调用者已加载它。节点实际采用须核对对应源码版本及真实调用。后续完整退役以已核消费者及恢复责任为依据，不能仅按模块数量裁剪。

取证与交接须分别核对本地读取、模型上下文、跨聊天和公开发布边界，见[数据流方法](.agents/skills/york-workstation/references/environment-sync.md#读取模型上下文交接与发布)。本机执行不等于内容仅留本机。测试用临时生成标记，验证工具输出和交接只带本任务允许的最小内容；历史处理范围未知时单独保留。
