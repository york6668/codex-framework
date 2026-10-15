# 执行与修复的即时检查

当任务涉及跨端接续、重复副作用、共享资源冲突、预算、故障恢复或缺陷关闭，而现有事实不能一眼判断时，可运行框架仓库的 `scripts/codex_framework.py check`。先由当前任务读取项目契约、原目标与原生/节点记录，检查器消费本次临时输入，不创建任务、持久日志、权限或业务数据，也不替代 Codex 原生审批。普通明确且低风险的动作不必为此建立记录。

工具失败后重排、进入等待、阶段收尾和交接均沿用该 `check` 命令，以 `phase: progress` 检查本轮剩余步骤。输入的 `progress.checkpoint` 为 `after_failure|wait|stage_end|handoff`，`claim` 为 `working|waiting|completed|continuous_tracking`；`steps` 最多 32 项，每项有唯一 `id`、现有 `action` 结构及引用前面步骤 ID 的 `depends_on`。输出逐项为 `direct_refusal`、`dependency_blocked`、`ready`、`needs_evidence` 或 `blocked`。这是当前剩余工作的有界投影，模型仍需从原任务核对是否遗漏步骤，不能把 `status: ready` 误读为全部步骤均可执行。

`context.tool_failures` 可列出多条仍有效的失败；原有单条 `tool_failure` 输入继续兼容，若两者同时存在会合并检查。每条平台拒绝按实际资源、原件提供的 effect_key、目的地与副作用匹配；write 改称 invoke 或更换通道、线程不会消除同一拒绝；资源路径的父子范围也会匹配。只把直接拒绝及其已证依赖标为暂停，其他获准动作单独检查。调用者须从原生记录保留所有仍有效的失败，检查器不能从省略的输入恢复历史；权限或事实确实改变时依原目标和平台规则复核，不能借输入删去拒绝来重试。

`claim: waiting` 仅在没有独立可执行或待取证步骤、剩余步骤均直接受拒或依赖受阻，且直接拒绝的 `ref` 有可核原生 `failure` 证据时返回 `wait_allowed: true`。缺少失败原件、仍可本地归因或仍需核对旧资源测量时不能宣布全面等待。资源与额度动作在 `progress` 中须提供当前节点观察的 `node_snapshot_observed_at`（带时区）及 `node_snapshot_max_age_seconds`，过期或缺失不会给出 `ready`；单动作输入若提供这两个字段也检查时效。观察时间必须来自节点原件，不因重新读取而刷新；程序只比较所给值，来源真实性仍须用工作站检查和原生记录核实。

`claim: working` 却没有列出剩余步骤时返回 `remaining_steps_unlisted`；这不能说明已全部完成或正在持续跟踪。未尝试的消费者只读核对等独立步骤应列为待做，不能伪造成平台拒绝。检查只覆盖调用者列出的步骤，仍须对照原任务核实完整性。

`progress` 的交接、等待及阶段收尾也核对本轮选定的 `goal_ref`（如已给 `resume.goal_ref`，可沿用），不能只在 `resume` 审查较新用户目标。`claim: completed` 须明确目标、版本、相同目标及版本的验收原件；当前有效用户目标或项目验收契约要求独立验收时，再提供该资源的独立验证历史。只给开发自测、旧目标验收或空步骤不能表示完成。`publish` 同样需要 `release.goal_ref` 和已通过的开发测试；独立验收适用时，另需与发布版本一致的 `release.independent_verification`。这两个入口复用下面的独立验证关系检查；局部失败仍只影响有依赖的步骤。

`claim: completed` 仍需目标验收来源，且不能留下剩余步骤；本地事故还须使用 `close_issue` 核对实际节点采用和效果，合成 CI 不能替代。`continuous_tracking` 始终返回未验证：静态检查与 SessionStart Hook 不提供空闲时模型执行或未来唤醒保证。若没有实际执行机会，只能报告当前无法持续跟踪。此入口不自动拦截计划工具、等待或交接，使用点依任务规范由模型执行，真实行为仍需正常任务入口复测。

先从 Codex 原生 `read_thread(includeOutputs=true)` 定位原线程、用户目标与相关工具调用；从节点实际入口读取能力、预算、数据状态及采用版本。项目规范、Git 提交和节点事实仍在原处。把适用规范引用、项目与任务范围、原线程、拟执行动作及相关来源放入临时 JSON，经标准输入调用：

```sh
python3 scripts/codex_framework.py check < /tmp/current-action.json
```

输入的 `project.id` 和 `task.project_id` 必须一致，`task.native_ref` 与 `context.task_ref` 都指向要接续的同一原生线程（`codex:thread:<id>`）。`project.scopes`、`task.scopes` 分别给出项目和本任务范围；每项由 `kind`、`id`、可选的相对 `path` 与 `access: read|write` 组成。`action.resource` 使用同样的身份字段，`kind` 是 `read|write|invoke|repair|publish`。资源身份应取自实际仓库、数据或服务，不用路径相似性推断归属。范围和路径的包含检查会拒绝跨项目及同项目其他任务越界。

新建或恢复相关任务时，`phase: resume` 以 `resume.resource`、`resume.goal_ref` 和选中的 `resume.accepted_corrections` 核对原目标来源、项目与任务边界、适用的旧纠正及其版本。先从项目现有契约和原任务历史识别适用纠正；程序无法自行发现调用者遗漏的项目历史。`ready` 后仍须在本轮行为中实际使用纠正并留下原生记录。接续若要进入真实持续验证，标明 `resume.stage: continuous`、`resume.version` 和 `resume.local_verification`；缺少本地复测计划或结果不得进入该阶段。普通 `work` 或 `local` 接续不因此被阻断。

`goal_ref` 必须指向原生用户消息；将同一原任务中所有相关的直接用户目标及纠正作为临时证据提供。若其中可核的较新目标晚于选定目标，`resume` 返回 `newer_goal_requires_scope_review`：模型须判断较新消息是替换、补充还是独立要求，重定当前产出并保留仍有效的旧事项；多个来源无法判定先后则返回 `goal_order_unverified`。检查器不能只凭顺序理解消息语义，也不能发现被调用者遗漏的消息。来源或跨任务转交不完整时保留具体缺口，不以旧摘要填补。`resume` 是接续时的有界自检，不是所有动作的自动拦截。

`verify_local` 及其 `continuous_eligible` 只说明实施任务提供的本地计划和结果满足进入后续验证的输入条件，不签署独立验收。核心框架候选由实施任务固定版本与自测结果，独立验证任务按相同目标锁定代码版本、数据或脱敏夹具、环境和验收标准；不得为验证将本机真实数据迁云或扩大项目、任务与平台权限。失败或未运行保留原状态，失败交回实施修订后按确切新版再测。

纠偏的本地复测先于真实持续验证。先在原任务中固定可手动执行的原失败和受影响行为、输入、预期、适用版本与正常入口；长期效果确实无法本地复现时说明理由及单独的后续验收，不用这个理由跳过其他本地检查。手动执行一次固定案例，失败先定位修复，沿同一输入再测，不反复生成测试直到碰巧通过。若修复涉及 Hook 自动加载，必须在正常 CLI 启动中观察专属注入内容或受支持的 Hook 事件；单独运行 handler、查看配置或等待将来任务都不能代替。平台不暴露该证据时如实保留未验证。`phase: verify_local` 接受 `local_verification.resource`、`version`、`plan_ref` 和非空 `cases`。每个 case 给唯一 `id`、`resource`、`expected_ref`、`observed_ref`。计划来源为原生 `local_plan` item，正文含版本、每项 `case:<id> expected:<SHA-256>`；预期来源为 `local_expectation`，其 `related_ref` 指向计划；实际来源为 `local_result`，其 `related_ref` 指向预期，版本与被验证版本一致。预期和实际文本摘要必须相同，实际来源须为完成、退出码 0、输出未截断且含版本的原生命令；`result: passed` 单独无效。程序检查来源片段、版本与精确输出，执行者还须核实计划覆盖完整可执行风险、原生来源真实性、计划先于实测及输出的业务语义。`ready` 仅表示这些可验证输入满足本地准入，不代表未来效果已经发生。输出 `continuous_eligible` 仅在本地准入通过、连续阶段接续通过或缺陷关闭检查通过时为 true；普通 `resume` 即使 `ready` 仍为 false。关闭问题的 `issue.local_verification` 使用同一计划与案例；`resume(stage=continuous)` 也重复检查，不会凭先前标签跳过。

有副作用的动作还提供稳定的 `effect_key`、`context.native_snapshot_ref`、`native_snapshot_complete: true` 和相关 `native_operations` 的引用、任务、项目、资源、effect key 与 `succeeded|running|failed|unknown` 状态。这里的 complete 只表示**本次副作用范围内**的原生记录已读全；做不到就保留待核实，不能填 true 让检查通过。相同效果已成功、进行中或结果不明时不重复执行；失败后再试需对同一原任务提供失败引用、原因引用及已验证纠正引用。`depends_on` 指向已成功的原生操作；其他任务正在写同一资源或结果不明时先隔离冲突。操作需要能力或消耗额度时，同时提供节点快照引用、当前能力和剩余额度。入口或上下文失败只证明那次调用失败，不能据此断言功能缺失或实现缺陷。

工作空间或授权范围有争议时，可在已有 `phase: action` 的临时输入中附 `action.workspace`：`channel` 仅记录协调入口名称，`execution_node` 取工具实际返回的执行机，`data_node` 是数据所在节点，`destination_node` 是实际操作结果/数据目的地，`side_effect` 为 `read|local_change|transfer|irreversible_delete|synthetic_test`。本地只读诊断沿用项目与任务的读取范围；本地写入等维护动作由 `authorization_ref` 指向原任务已授权的 `authorization` 证据，不因 `channel: cloud_threads` 再请一次许可。证据的 `workspace` 元数据列明已授权的 `execution_nodes`、`data_nodes`、`destination_nodes` 和 `side_effects`，且关联同一资源；这些字段必须按原用户请求和工具结果解释，程序只检查提供的关系，不从名称创造授权。源证据仍须经原生 item 与摘要核对。

目的地与数据节点不同即属跨环境，需单独的 `transfer_authorization_ref` 和 `risk_review_ref`，后者指向说明必要性、数据/权限/运行影响及目的地的原生证据；本地维护授权不能自动覆盖。`irreversible_delete` 即使仍在本机，也需要针对确切资源的 `confirmation_ref`。`context.tool_failure` 若为 `platform_denied`，以被拒资源、`action_kind` 和可选的 `side_effect` 关联本动作；同一动作改通道仍是 `blocked`，另一独立获准动作不会因此整体失权。证据改变时只按实际平台规则复核，检查器结果从不解除平台拒绝或批准传输、删除。`ready` 仍只表示临时输入内部一致，不是审批回执。

`authorization` 与 `confirmation` 的原生来源须是用户消息；助手说明或命令成功不能变成用户许可。证据摘要只绑定所给原文，程序不自动理解自然语言中的范围，执行者必须核对对象、节点、目的地与副作用确在原授权内。已有授权仍适用时复用原引用，不为每个内部操作创建新批准记录。

同一操作遇到 `resource_exhausted` 时，检查器要求原失败、原因与已验证纠正的引用后才允许作为恢复重试；输出 `local_resource_diagnosis_before_retry` 指向本地取证和修复缺口，不代表需要再次找用户批准。读取获准的日志或执行独立修复不依赖该重试；`platform_denied` 则始终保留为外部拒绝，不能用本地诊断或更换通道消除。

同一本地事故经历多次资源不足、工具故障或方案调整时，沿用原目标而非每步重定义。可附 `context.incident` 的原生 `goal_ref`、受影响 `resource`、`responsible_node`、原始 `data_node` 和获准 `destination_nodes`；每个相关动作的 `action.workspace.responsible_node` 必须保持原责任节点，真实数据位置与目的地不能偏离原边界，缺少工作空间事实则为 `needs_evidence`。云端合成代码测试只在有 `synthetic_fixture` 来源且不使用真实数据时可单列通过；它可以改变测试执行机，不改变本地事故责任或真实恢复目的地。若用户以后明确修改目标或授权，从新的原生用户证据更新这份临时输入；不能以 CI 成功、容量故障或协调工具名称暗中改写。该输入只为当前检查使用，不建立永久事故台账。

`publish` 动作的 `release.version` 须与 `release.test_ref` 指向的已通过测试版本一致；提交或投递记录不能替代测试。原生工具仅返回命令退出码时，先核对实际副作用或产物，不能凭退出码把业务效果标为 `succeeded`。

发现偏差时用 `phase: feedback`：`feedback.resource` 是观察对象，`expected_ref` 与 `observed_ref` 指向 `context.evidence` 中同一资源的 `expectation` 与 `observation`。两份记录的 SHA-256 摘要须与提供的原生 item 文本或节点文件内容一致，程序比较后输出 `deviation`。期望标准应在观察前形成；摘要差异是诊断入口，不自动确定根因或许可重试。已有修正无效或复发时给 `prior_correction_ref`，仍返回偏差并标记需重新处理。模型沿原失败、实际路径和影响对象确认原因。`repair` 动作需给出失败与根因引用、原因层、受影响资源及原任务 `authorization_ref`；它可指向已有用户授权，不要求每次常规修复重新请示。平台权限仍由平台执行。

项目原有契约、修正代码及回归测试是后续接续的来源；不另建纠正台账。下一轮从这些来源和原生任务记录读出适用纠正，在本次输入的 `project.accepted_corrections` 放入其引用、资源、版本，并以 `project.corrections_source_ref` 指向所核对来源。`resume` 或 `action` 的 `accepted_corrections` 应包含同一资源的纠正及同版依据；程序会要求临时证据中有可核验的旧修正原件，遗漏、旧版或来源缺失不会得到 `ready`。空数组本身不证明没有历史偏差。

缺陷修复后用 `phase: close_issue` 核对本地准入、原目标、原失败、根因、修正、原场景及受影响路径复测、独立目标验收、正式采用版本、实际效果与后续接续。`issue.goal_ref` 和 `acceptance_ref` 必须关联；目标验收不能复用局部回归命令。原场景复测的 `related_ref` 指向原失败，目标验收指向原目标，后续接续指向修正；本地验证、回归、验收、采用、效果及接续版本须一致。仅命令退出 0 不证明业务目标完成，执行者须核对实际输出和目标语义。修后尚未正式采用、没有效果或没有后续使用证据时为 `needs_evidence`，但不得把可本地复测的部分推迟到后续任务。

当前有效用户目标或项目验收契约要求独立验收时，完整关闭还需 `issue.independent_verification.attempts`，按发生顺序保留每次独立任务的 `task_ref`、`version`、`lock_ref`、`verdict_ref`、`data_digest`、`environment_digest`、`criteria_digest` 和 `outcome: passed|failed|not_run`。验证任务须不同于实施任务；`verification_lock` 原生消息先固定 `code:<version>`、`data:<SHA-256>`、`environment:<SHA-256>`、`criteria:<SHA-256>`，`verification_verdict` 原生结果再引用该锁的摘要、相同版本及结果。通过和失败须有对应退出状态的命令原件，未运行须有验证任务的原生说明。失败后的下一次尝试须换经实施任务修订的版本，并用 `revision_ref` 引用该版本修正；旧失败仍保留在 `attempts`。最后一次通过版本须与回归、正式采用和效果所用版本相同，`acceptance_owner` 指向独立验证任务，`followup_owner` 仍说明后续接续责任。检查器只能核对给定来源、顺序、摘要和版本，不能自行发现被省略的失败或判断测试覆盖、数据脱敏及业务语义；独立任务据原件判定，不能把实施者自测或合成夹具结果写成真实效果。

失败历史按整个尝试序列关联：中间一次未运行或新版通过，都不把旧失败代码变成有效版本；旧版通过不能抹去待修失败。相邻尝试的原生 verdict 须能证明先后，输入数组不能倒置已知历史；跨任务顺序不可证时保留待核实。须保留失败、修订版本及复验原件。调用者仍须核实尝试历史完整，检查器无法从遗漏的原生来源恢复被省去的失败。

本地 Docker/OOM 事故中，云端合成 CI 的通过记录只能作为代码层的有限证据；`close_issue` 仍须独立的本地原失败复测、实际采用、资源与恢复效果及后续使用证据。日志未报错、临时容器健康或合成回放也不能冒充这些事实。若平台拒绝某次真实恢复或备份动作，保留该动作的外部阻断，但仍完成不依赖它的本地调查和修复，原任务持续负责待验部分。

若这份临时检查输入带 `context.incident`，`close_issue` 还核对同一原目标与资源，并要求 `adoption` 和实际 `effect` 证据标明原责任节点的 `node_id`；云端 CI 的节点身份不能冒充本机采用或本地恢复。调用者须以工具返回和节点原件核实该身份，检查器只能比较提供的元数据，不能认证伪造的节点标签或代替真实恢复测试。

反馈、恢复入口和缺陷关闭的来源核验：`context.native_sources` 可临时放 Codex `read_thread` 的原始 JSON 响应；Codex 证据用 `native_item_id` 指向其中 item，非命令文本以 `digest` 绑定。成功证据要求原生命令 `completed`、退出码 0、输出未截断，带版本的结果还须在命令或输出出现该版本。节点文件证据用 `project.root`、相对 `node_path` 和 `digest` 核对当前文件；`passed/applied/observed/used_correction` 还须由 JSON 文件同时给出相同 `result/version`。临时输入可含原生响应以供本次比对，不能提交到框架仓库或复制成长期日志；取不到完整原件时保留 `needs_evidence`。来源由当前任务的受支持接口取得，检查器不能认证任意调用者提供的 JSON 未被伪造。

分工：程序比对来源片段、摘要、范围、版本、依赖和证据关联；模型负责目标语义、原件来源、根因、影响范围、修正与复测解释；用户、业务系统或节点提供真实期望、运行反馈和平台授权结果。没有后续观察时保持待跟踪。仓库 `tests/test_correction_loop.py` 是隔离程序边界测试；真实任务链需另用原生记录和节点原件复测，不能把夹具结果当实际项目效果。

输出 `blocked` 表示输入中的已知冲突，`deviation` 表示观察值与既有期望不符，`needs_evidence` 表示缺少决定所需事实，`ready` 仅表示这份输入内部一致。退出码分别是 2、2、2、0；格式错误为 1。检查器无法自行证明调用者提供的原始记录是否真实或完整，也不能拦截未经过它的工具调用；执行者仍须核对原件、遵守平台权限和业务授权。结果不明时先查询原动作，不能把一次新的检查当作重试许可。修订规则后用原失败输入和受影响行为复测；代码测试、文件安装和实际运行效果分别报告。

独立验收适用性来自有效用户要求和项目契约，输入 `project.independent_verification_required: true` 表明该要求；已有 `independent_verification` 输入无论标记如何仍受核验。普通单任务修复没有此要求时不自动创建第二任务。检查器不能从布尔值识别自然语言中的义务，调用者仍须核对原生目标，不能填 false 回避明示要求。原生 `userMessage.content` 文本块和旧式 `text` 均可核对摘要；委托文本不自动变成直接用户授权。共享服务的并发写冲突按实际资源交叠检查，不能因项目 ID 不同略过；独立文件或命名空间可继续。
