# 执行与修复的即时检查

OpenAI 设计与能力依据按 [官方依据与能力核验](../../york-engineering/references/openai-capability-basis.md) 从完整官方体系导航。`action.capabilities/context.capabilities` 是本次提供的能力标签关系，不是官方产品目录或本机可用证明；新产品不因未列示例而排除，别产品/宿主能力也不能用同名标签代签。模型先核适用正文、当前原生入口/安装版本、来源和时效，再构造临时投影；缺失或已失效证据不签可用。检查器不查询或授权新产品，不解析 Markdown 方法为运行配置，不因 `ready` 证明实际能力、连续执行或完整研究已完成。

## 可选项目业务工件适配

正常 `check` 输入可附 `business_verification: {expected_ref, observed_ref}`，旧输入与退出语义不变，不要求所有任务填写。它复用 `context.evidence/native_sources`；预期为 `business_expectation`，关联当前用户 `goal`，实际为 `business_run`，关联该预期，二者资源精确匹配本次 envelope.resource、task/project 与固定测试版本。实际记录须是完整成功原生命令的观察原件及 SHA-256；预期先于本次观察。调用者仍核实原件真实性、原生产过程先保存观察再断言，后读历史文件的时间不冒充原动作时间。

当前适配项目 `BusinessEvidence` 工件，预期正文只选 `level: engineering|business_e2e`、`run` 的 runId/caseId/sourceVersion/testVersion/environment/evidenceKind，以及所需 `operations`（每项 method/path/contract、独立 postconditions 和需要时的 binding_equalities）。实际正文由受支持只读命令汇集同一次运行的 `run.json` 为 run、所有 operation 文件为 operations；文件名去掉 .json，值保留实际对象，不合并覆盖 failed/interrupted/recovery-*。必需操作缺键保持缺口，键存在但为 null、数组或其他非对象则输入无效，不能跳过检查。可选 http-observed 只是先保存的补充 HTTP 观察，须与同操作完整 observed 的身份、method/path/status/request/response/before 一致；不能代替完整 observed、after、bindings 或 verdict。所有恢复 attempt 的 started、失败与未结束状态继续保留，旧操作 PASS 或另一 attempt 的 PASS 不清除它们。这是项目适配器的选定输入，不是通用采集格式或新增台账。文件只读及该检查均不执行或重放动作。

检查精确运行/版本、操作集合、同操作请求与准备状态、契约投影及绑定来源、完整前后状态和 PASS 原件；失败、缺失、未知或恢复痕迹保留待审，不能用后来状态查询补造遗失 Exchange。绑定检查核对“来源值”，postconditions 给本次项目契约的固定 status 或 request/response/before/after 标量投影子集；binding_equalities 选定需要相等的两个绑定名称。未提供领域预期不通过，违反预期或动态关系即拒绝；预期依据与其他领域关系仍由项目独立 Oracle 验证。已知敏感指针被拒，普通字段值是否允许仍须采集端和模型按业务语义核对，检查不回显原正文。

该适配器的 ready 仅代表提供的工程工件关系一致；synthetic-controller 不是 HTTP 或真实 test2。请求 business_e2e 一律保留 business_end_to_end_not_proven_by_adapter，由实际浏览器、网络、存储与独立领域验收继续完成，不能升级标签绕过。progress/close_issue 等入口提供此适配时也消费它，不能在完成判定忽略已提供的业务失败；未提供时不声称自动识别自然语言中的业务要求。真实流程、能力与后续工作仍按 [业务复测](../../york-engineering/references/business-workflow.md) 和原协调判断。

## 现有执行与接续检查

当任务涉及跨端接续、重复副作用、共享资源冲突、预算、故障恢复或缺陷关闭，而现有事实不能一眼判断时，可运行框架仓库的 `scripts/codex_framework.py check`。先由当前任务读取项目契约、原目标与原生/节点记录，检查器消费本次临时输入，不创建任务、持久日志、权限或业务数据，也不替代 Codex 原生审批。普通明确且低风险的动作不必为此建立记录。

工具失败后重排、进入等待、阶段收尾和交接均沿用该 `check` 命令，以 `phase: progress` 检查本轮剩余步骤。输入的 `progress.checkpoint` 为 `after_failure|wait|stage_end|handoff`，`claim` 为 `working|waiting|paused|completed|continuous_tracking`；`steps` 最多 32 项，每项有唯一 `id`、现有 `action` 结构及引用前面步骤 ID 的 `depends_on`。输出逐项为 `direct_refusal`、`dependency_blocked`、`ready`、`needs_evidence` 或 `blocked`。输出 `work_partition` 同时列出逐项条件满足的 `ready_step_ids`、有直接或依赖阻断的 `blocked_step_ids`、仍缺证据的 `unresolved_step_ids`；三组按输入顺序覆盖已提供步骤。逐项保留已有 `access_route` 建议；管理入口未知先按原归属有界发现，不从 GUI 缺席推导凭据不存在。聚合 `needs_evidence` 或 `blocked` 不能被解释为所有分支暂停，仍须核对具体步骤；`ready_step_ids` 也只表示该步骤输入关系满足，不覆盖全局目标、消息权限或省略的停止要求。这是当前剩余工作的有界投影，模型仍需从原任务核对是否遗漏步骤，不能把 `status: ready` 误读为全部步骤均可执行。

`progress.steps[].depends_on` 仅在对应边有可核的必要关系时传播阻塞。`depends_on_basis` 可为单边 `{status, source_ref, reason}` 或按前序步骤 ID 索引的同形对象；每条边都须覆盖。省略依据、hypothesis/unknown、缺失/伪造原件均将该消费者放入 `needs_evidence`，不授予整体等待，也不放行该消费者的写入。其他无依赖步骤仍从原 `work_partition` 继续；核实错误依赖后撤掉该边，再检查实际动作，不凭缺证强行执行。

`proven` 引用沿原 `context.evidence/native_sources`，证据 `kind: dependency`、同 project/task、`resource` 精确对应消费者，带原正文 SHA256。原件须为成功命令取得的契约/观察（`result: observed`），或直接用户给定的必要关系；助手标签和委托转述不能自证。结构正文为 `{producer:{step_id,action}, consumer:{step_id,action}, requires:{kind,resource,reason}}`，两端的完整 action 与本次输入一致，reason 与该边依据一致。`required_input` 的 resource 是生产者实际对象，原件说明消费者为何必须取得该输入；`shared_effect` 的 resource 是两端实际重叠的对象，不能仅用共同项目/目录把不同文件连成共享写入。真实原子事务、同对象在途或实际不兼容仍依据其必要输入/共享效果局部处理；原 action 的作用域、准确拒绝、UNKNOWN 及预算检查不被依赖关系清除。程序只绑定已给原件与关系，不能认证调用者快照或理解任意业务正文；原件是否真实且确为必要输入/效果仍由模型判断。已有普通对话不必为此重写成表单，只有选择该可选检查器时使用其有界合同。

处理、排查、独立验证和集成的职责及真实依赖由[工作生命周期](../SKILL.md#职责与新问题)承担，检查器不从任务称谓推导权限，也不决定自然语言是否要求全项目停止。只读排查用真实 `task.scopes.access: read`；写入、invoke、修改旁任务仍按实际范围检查。用户停止或纠正先由执行者根据原件更新当前有效范围，不能凭可并行标签忽略整项目停止，也不能删掉仍有效的取消或拒绝。

`context.tool_failures` 可列出多条仍有效的失败；原有单条 `tool_failure` 输入继续兼容，若两者同时存在会合并检查。每条平台拒绝按实际资源、原件提供的 effect_key、目的地与副作用匹配；write 改称 invoke 或更换通道、线程不会消除同一拒绝；资源路径的父子范围也会匹配。只把直接拒绝及其已证依赖标为暂停，其他获准动作单独检查。调用者须从原生记录保留所有仍有效的失败，检查器不能从省略的输入恢复历史；权限或事实确实改变时依原目标和平台规则复核，不能借输入删去拒绝来重试。

`claim: waiting` 还需下面的本轮目标回看原件；仅在没有独立可执行或待取证步骤、剩余步骤均直接受拒或依赖受阻，且直接拒绝的 `ref` 有可核原生 `failure` 证据时返回 `wait_allowed: true`。缺少失败原件、仍可本地归因或仍需核对旧资源测量时不能宣布全面等待。资源与额度动作在 `progress` 中须提供当前节点观察的 `node_snapshot_observed_at`（带时区）及 `node_snapshot_max_age_seconds`，过期或缺失不会给出 `ready`；单动作输入若提供这两个字段也检查时效。观察时间必须来自节点原件，不因重新读取而刷新；程序只比较所给值，来源真实性仍须用工作站检查和原生记录核实。

`claim: working` 却没有列出剩余步骤时返回 `remaining_steps_unlisted`；这不能说明已全部完成或正在持续跟踪。未尝试的消费者只读核对等独立步骤应列为待做，不能伪造成平台拒绝。检查只覆盖调用者列出的步骤，仍须对照原任务核实完整性。

`progress` 的交接、等待及阶段收尾也核对本轮选定的 `goal_ref`（如已给 `resume.goal_ref`，可沿用），不能只在 `resume` 审查较新用户目标。`claim: completed` 须明确目标、版本、相同目标/资源及版本的验收原件，并提供 `continuation.review_ref`；该本轮 `goal_review` 正文除 `goal_ref/step_ids` 外还须绑定 `version/resource`。缺少回看不能用空 steps 表示完成，已给同目标较新回看时不能选旧空回看跳过；当前有效用户目标或项目验收契约要求独立验收时，再提供该资源的独立验证历史。只给开发自测、旧目标验收或空步骤不能表示完成。`publish` 同样需要 `release.goal_ref` 和已通过的开发测试；`release_test.related_ref` 关联该原目标，`resource` 精确匹配发布对象，不能复用另一目标/资源的成功；独立验收适用时，另需与发布版本一致的 `release.independent_verification`。这两个入口复用下面的独立验证关系检查；局部失败仍只影响有依赖的步骤。

`claim: completed` 仍需目标验收来源，且不能留下剩余步骤；本地事故还须使用 `close_issue` 核对实际节点采用和效果，合成 CI 不能替代。`continuous_tracking` 的静态投影仍返回未验证：它不能承诺未来执行，也不判定 App 调度不可用、不暂停已开启调度或拦截实际回合中的获准步骤。SessionStart Hook 不提供空闲唤醒；用户控制的 App 原生触发及其运行记录按实际范围单独验收，缺少执行机会时只说明该范围未知。此入口不自动拦截计划工具、等待或交接，使用点依任务规范由模型执行，真实行为仍需正常任务入口复测。

先从 Codex 原生 `read_thread(includeOutputs=true)` 定位原线程、用户目标与相关工具调用；从节点实际入口读取能力、预算、数据状态及采用版本。项目规范、Git 提交和节点事实仍在原处。把适用规范引用、项目与任务范围、原线程、拟执行动作及相关来源放入临时 JSON，将 `CHECK_INPUT` 设为该文件的实际位置，经标准输入调用；不要求固定临时目录或另一机器的路径：

```sh
python3 scripts/codex_framework.py check < "$CHECK_INPUT"
```

输入的 `project.id` 和 `task.project_id` 必须一致，`task.native_ref` 与 `context.task_ref` 都指向要接续的同一原生线程（`codex:thread:<id>`）。`project.scopes`、`task.scopes` 分别给出项目和本任务范围；每项由 `kind`、`id`、可选的相对 `path` 与 `access: read|write` 组成。`action.resource` 使用同样的身份字段，`kind` 是 `read|write|invoke|repair|publish`。资源身份应取自实际仓库、数据或服务，不用路径相似性推断归属。范围和路径的包含检查会拒绝跨项目及同项目其他任务越界。

新建或恢复相关任务时，`phase: resume` 以 `resume.resource`、`resume.goal_ref` 和选中的 `resume.accepted_corrections` 核对原目标来源、项目与任务边界、适用的旧纠正及其版本。先从项目现有契约和原任务历史识别适用纠正；程序无法自行发现调用者遗漏的项目历史。`ready` 后仍须在本轮行为中实际使用纠正并留下原生记录。接续若要进入真实持续验证，标明 `resume.stage: continuous`、`resume.version` 和 `resume.local_verification`；缺少本地复测计划或结果不得进入该阶段。普通 `work` 或 `local` 接续不因此被阻断。

`goal_ref` 必须指向原生用户消息；将同一原任务中所有相关的直接用户目标及纠正作为临时证据提供。若其中可核的较新目标晚于选定目标，`resume` 返回 `newer_goal_requires_scope_review`：模型须判断较新消息是替换、补充还是独立要求，重定当前产出并保留仍有效的旧事项；多个来源无法判定先后则返回 `goal_order_unverified`。检查器不能只凭顺序理解消息语义，也不能发现被调用者遗漏的消息。来源或跨任务转交不完整时保留具体缺口，不以旧摘要填补。`resume` 是接续时的有界自检，不是所有动作的自动拦截。

`verify_local` 及其 `continuous_eligible` 只说明实施任务提供的本地计划和结果满足进入后续验证的输入条件，不签署独立验收。核心框架候选由实施任务固定版本与自测结果，独立验证任务按相同目标锁定代码版本、数据或脱敏夹具、环境和验收标准；不得为验证将本机真实数据迁云或扩大项目、任务与平台权限。失败或未运行保留原状态，失败交回实施修订后按确切新版再测。

纠偏的本地复测先于真实持续验证。先在原任务中固定可手动执行的原失败和受影响行为、输入、预期、适用版本与正常入口；长期效果确实无法本地复现时说明理由及单独的后续验收，不用这个理由跳过其他本地检查。手动执行一次固定案例，失败先定位修复，沿同一输入再测，不反复生成测试直到碰巧通过。若修复涉及 Hook 自动加载，必须在正常 CLI 启动中观察专属注入内容或受支持的 Hook 事件；单独运行 handler、查看配置或等待将来任务都不能代替。平台不暴露该证据时如实保留未验证。`phase: verify_local` 接受 `local_verification.resource`、`version`、`plan_ref` 和非空 `cases`。每个 case 给唯一 `id`、`resource`、`expected_ref`、`observed_ref`。计划来源为原生 `local_plan` item，正文含版本、每项 `case:<id> expected:<SHA-256>`；预期来源为 `local_expectation`，其 `related_ref` 指向计划；实际来源为 `local_result`，其 `related_ref` 指向预期，版本与被验证版本一致。预期和实际文本摘要必须相同，实际来源须为完成、退出码 0、输出未截断且含版本的原生命令；`result: passed` 单独无效。程序检查来源片段、版本与精确输出，执行者还须核实计划覆盖完整可执行风险、原生来源真实性、计划先于实测及输出的业务语义。`ready` 仅表示这些可验证输入满足本地准入，不代表未来效果已经发生。输出 `continuous_eligible` 仅在本地准入通过、连续阶段接续通过或缺陷关闭检查通过时为 true；普通 `resume` 即使 `ready` 仍为 false。关闭问题的 `issue.local_verification` 使用同一计划与案例；`resume(stage=continuous)` 也重复检查，不会凭先前标签跳过。

有副作用的动作还提供稳定的 `effect_key`、`context.native_snapshot_ref`、`native_snapshot_complete: true` 和相关 `native_operations` 的引用、任务、项目、资源、effect key 与 `succeeded|running|failed|unknown` 状态。这里的 complete 只表示**本次副作用范围内**的原生记录已读全；做不到就保留待核实，不能填 true 让检查通过。相同效果已成功、进行中或结果不明时不重复执行；失败后再试需对同一原任务提供失败引用、原因引用及已验证纠正引用。`depends_on` 指向已成功的原生操作；其他任务正在写同一资源或结果不明时先隔离冲突。操作需要能力或消耗额度时，同时提供节点快照引用、当前能力和剩余额度。入口或上下文失败只证明那次调用失败，不能据此断言功能缺失或实现缺陷。

工作空间或授权范围有争议时，可在已有 `phase: action` 的临时输入中附 `action.workspace`：`channel` 仅记录协调入口名称，`execution_node` 取工具实际返回的执行机，`data_node` 是数据所在节点，`destination_node` 是实际操作结果/数据目的地，`side_effect` 为 `read|local_change|transfer|irreversible_delete|synthetic_test`。本地只读诊断沿用项目与任务的读取范围；本地写入等维护动作由 `authorization_ref` 指向原任务已授权的 `authorization` 证据，不因 `channel: cloud_threads` 再请一次许可。证据的 `workspace` 元数据列明已授权的 `execution_nodes`、`data_nodes`、`destination_nodes` 和 `side_effects`，且关联同一资源；这些字段必须按原用户请求和工具结果解释，程序只检查提供的关系，不从名称创造授权。源证据仍须经原生 item 与摘要核对。

目的地与数据节点不同即属跨环境，需单独的 `transfer_authorization_ref` 和 `risk_review_ref`，后者指向说明必要性、数据/权限/运行影响及目的地的原生证据；本地维护授权不能自动覆盖。`irreversible_delete` 即使仍在本机，也需要针对确切资源的 `confirmation_ref`。`context.tool_failure` 若为 `platform_denied`，以被拒资源、`action_kind` 和可选的 `side_effect` 关联本动作；同一动作改通道仍是 `blocked`，另一独立获准动作不会因此整体失权。证据改变时只按实际平台规则复核，检查器结果从不解除平台拒绝或批准传输、删除。`ready` 仍只表示临时输入内部一致，不是审批回执。

`authorization` 与 `confirmation` 的原生来源须是用户消息；助手说明或命令成功不能变成用户许可。证据摘要只绑定所给原文，程序不自动理解自然语言中的范围，执行者必须核对对象、节点、目的地与副作用确在原授权内。已有授权仍适用时复用原引用，不为每个内部操作创建新批准记录。 用户批准的需求及明确限制决定边界，普通内部方案、步骤、顺序或候选版本变化不要求重批；新版仍须有对应验收证据。授权资源按规范身份匹配；明确授权的本项目目录可覆盖安全子路径，仅在已有 `project.root` 和同一执行/数据/目的节点能核实实际目录、解析后仍在授权对象内且未跨卷时扩展。缺本地路径事实为 `authorized_resource_path_unverified`（交接为 `handoff_authorized_resource_unverified`），由模型沿获准入口补事实，不改作缺用户许可；未知身份、模糊非目录资源、兄弟路径和符号链接越界不会获准。原接收任务、原授权节点和副作用及平台单独确认仍分别核对；不能以待执行三节点相同替换原节点边界。目录继承须保留原授权的节点/副作用字段；缺原节点与目录关联为待取证，不推导成新增用户批准。

节点确有别名时，可用可选 `context.node_identity_ref` 引用当前任务的 `node_identity` 原生观察：成功、完整的节点命令结果 JSON 恰含 `node_id/aliases/root`，记录为同一 `node_id` 的 `result: observed`，且实际目录与 `project.root` 相同。该事实只归一已证明相同的节点名称，原用户授权、拒绝、目的地和副作用仍独立比较；助手文字、自报映射或不同目录/节点的观察不提供同一性。模型须核实原件确为实际节点观察，不能用 echo、自造输出或另一任务代造证明；检查器不认证任意调用者 JSON。无别名需求的旧输入不新增此必填字段。

同一操作遇到 `resource_exhausted` 时，检查器要求原失败、原因与已验证纠正的引用后才允许作为恢复重试；输出 `local_resource_diagnosis_before_retry` 指向本地取证和修复缺口，不代表需要再次找用户批准。读取获准的日志或执行独立修复不依赖该重试；`platform_denied` 则始终保留为外部拒绝，不能用本地诊断或更换通道消除。

同一本地事故经历多次资源不足、工具故障或方案调整时，沿用原目标而非每步重定义。可附 `context.incident` 的原生 `goal_ref`、受影响 `resource`、`responsible_node`、原始 `data_node` 和获准 `destination_nodes`；每个相关动作的 `action.workspace.responsible_node` 必须保持原责任节点，真实数据位置与目的地不能偏离原边界，缺少工作空间事实则为 `needs_evidence`。云端合成代码测试只在有 `synthetic_fixture` 来源且不使用真实数据时可单列通过；它可以改变测试执行机，不改变本地事故责任或真实恢复目的地。若用户以后明确修改目标或授权，从新的原生用户证据更新这份临时输入；不能以 CI 成功、容量故障或协调工具名称暗中改写。该输入只为当前检查使用，不建立永久事故台账。

`publish` 动作的 `release.version` 须与 `release.test_ref` 指向的已通过测试版本一致；提交或投递记录不能替代测试。原生工具仅返回命令退出码时，先核对实际副作用或产物，不能凭退出码把业务效果标为 `succeeded`。

发现偏差时用 `phase: feedback`：`feedback.resource` 是观察对象，`expected_ref` 与 `observed_ref` 指向 `context.evidence` 中同一资源的 `expectation` 与 `observation`。两份记录的 SHA-256 摘要须与提供的原生 item 文本或节点文件内容一致，程序比较后输出 `deviation`。期望标准应在观察前形成；摘要差异是诊断入口，不自动确定根因或许可重试。已有修正无效或复发时给 `prior_correction_ref`，仍返回偏差并标记需重新处理。模型沿原失败、实际路径和影响对象确认原因。`repair` 动作需给出失败与根因引用、原因层、受影响资源及原任务 `authorization_ref`；它可指向已有用户授权，不要求每次常规修复重新请示。平台权限仍由平台执行。

项目原有契约、修正代码及回归测试是后续接续的来源；不另建纠正台账。下一轮从这些来源和原生任务记录读出适用纠正，在本次输入的 `project.accepted_corrections` 放入其引用、资源、版本，并以 `project.corrections_source_ref` 指向所核对来源。`resume` 或 `action` 的 `accepted_corrections` 应包含同一资源的纠正及同版依据；程序会要求临时证据中有可核验的旧修正原件，遗漏、旧版或来源缺失不会得到 `ready`。空数组本身不证明没有历史偏差。

缺陷修复后用 `phase: close_issue` 核对本地准入、原目标、原失败、根因、修正、原场景及受影响路径复测、独立目标验收、正式采用版本、实际效果与后续接续。`issue.goal_ref` 和 `acceptance_ref` 必须关联；目标验收不能复用局部回归命令。原场景复测的 `related_ref` 指向原失败，目标验收指向原目标，后续接续指向修正；本地验证、回归、验收、采用、效果及接续版本须一致。原场景与受影响回归、目标验收、采用、效果和后续接续的证据还须给出与问题相同的 `resource`；缺范围为待证，明确异域证据阻断，不能用同版本命令退出 0 接受另一对象。仅命令退出 0 不证明业务目标完成，执行者须核对实际输出和目标语义。修后尚未正式采用、没有效果或没有后续使用证据时为 `needs_evidence`，但不得把可本地复测的部分推迟到后续任务。

当前有效用户目标或项目验收契约要求独立验收时，完整关闭还需 `issue.independent_verification.attempts`，按发生顺序保留每次独立任务的 `task_ref`、`version`、`lock_ref`、`verdict_ref`、`data_digest`、`environment_digest`、`criteria_digest` 和 `outcome: passed|failed|not_run`。验证任务须不同于实施任务；`verification_lock` 原生消息先固定 `code:<version>`、`data:<SHA-256>`、`environment:<SHA-256>`、`criteria:<SHA-256>`，`verification_verdict` 原生结果再引用该锁的摘要、相同版本及结果。通过和失败须有对应退出状态的命令原件，未运行须有验证任务的原生说明。失败后的下一次尝试须换经实施任务修订的版本，并用 `revision_ref` 引用该版本修正；旧失败仍保留在 `attempts`。最后一次通过版本须与回归、正式采用和效果所用版本相同，`acceptance_owner` 指向独立验证任务，`followup_owner` 仍说明后续接续责任。检查器只能核对给定来源、顺序、摘要和版本，不能自行发现被省略的失败或判断测试覆盖、数据脱敏及业务语义；独立任务据原件判定，不能把实施者自测或合成夹具结果写成真实效果。

失败历史按整个尝试序列关联：中间一次未运行或新版通过，都不把旧失败代码变成有效版本；旧版通过不能抹去待修失败。相邻尝试的原生 verdict 须能证明先后，输入数组不能倒置已知历史；跨任务顺序不可证时保留待核实。须保留失败、修订版本及复验原件。调用者仍须核实尝试历史完整，检查器无法从遗漏的原生来源恢复被省去的失败。

本地 Docker/OOM 事故中，云端合成 CI 的通过记录只能作为代码层的有限证据；`close_issue` 仍须独立的本地原失败复测、实际采用、资源与恢复效果及后续使用证据。日志未报错、临时容器健康或合成回放也不能冒充这些事实。若平台拒绝某次真实恢复或备份动作，保留该动作的外部阻断，但仍完成不依赖它的本地调查和修复，原任务持续负责待验部分。

若这份临时检查输入带 `context.incident`，`close_issue` 还核对同一原目标与资源，并要求 `adoption` 和实际 `effect` 证据标明原责任节点的 `node_id`；云端 CI 的节点身份不能冒充本机采用或本地恢复。调用者须以工具返回和节点原件核实该身份，检查器只能比较提供的元数据，不能认证伪造的节点标签或代替真实恢复测试。

反馈、恢复入口和缺陷关闭的来源核验：`context.native_sources` 可临时放 Codex `read_thread` 的原始 JSON 响应；Codex 证据用 `native_item_id` 指向其中 item，非命令文本以 `digest` 绑定。成功证据要求原生命令 `completed`、退出码 0、输出未截断，带版本的结果还须在命令或输出出现该版本。节点文件证据用 `project.root`、相对 `node_path` 和 `digest` 核对当前文件；`passed/applied/observed/used_correction` 还须由 JSON 文件同时给出相同 `result/version`。临时输入可含原生响应以供本次比对，不能提交到框架仓库或复制成长期日志；取不到完整原件时保留 `needs_evidence`。来源由当前任务的受支持接口取得，检查器不能认证任意调用者提供的 JSON 未被伪造。

分工：程序比对来源片段、摘要、范围、版本、依赖和证据关联；模型负责目标语义、原件来源、根因、影响范围、修正与复测解释；用户、业务系统或节点提供真实期望、运行反馈和平台授权结果。没有后续观察时保持待跟踪。仓库 `tests/test_correction_loop.py` 是隔离程序边界测试；真实任务链需另用原生记录和节点原件复测，不能把夹具结果当实际项目效果。

输出 `blocked` 表示输入中的已知冲突，`deviation` 表示观察值与既有期望不符，`needs_evidence` 表示缺少决定所需事实，`ready` 仅表示这份输入内部一致。退出码分别是 2、2、2、0；格式错误为 1。检查器无法自行证明调用者提供的原始记录是否真实或完整，也不能拦截未经过它的工具调用；执行者仍须核对原件、遵守平台权限和业务授权。结果不明时先查询原动作，不能把一次新的检查当作重试许可。修订规则后用原失败输入和受影响行为复测；代码测试、文件安装和实际运行效果分别报告。

独立验收适用性来自有效用户要求和项目契约，输入 `project.independent_verification_required: true` 表明该要求；已有 `independent_verification` 输入无论标记如何仍受核验。普通单任务修复没有此要求时不自动创建第二任务。检查器不能从布尔值识别自然语言中的义务，调用者仍须核对原生目标，不能填 false 回避明示要求。原生 `userMessage.content` 文本块和旧式 `text` 均可核对摘要；委托文本不自动变成直接用户授权。共享服务的并发写冲突按实际资源交叠检查，不能因项目 ID 不同略过；独立文件或命名空间可继续。

## 可选认证与维护分流

`phase: action` 可附 `action.access` 对照[认证与操作能力](../../york-workstation/references/environment-sync.md#入口、认证与操作能力)，无需为旧输入补填；不提供时保持原检查和退出语义。六项分类分别为 `resource_owner: managed_workstation|external_business|unknown`、`identity_role: maintenance|business|test|unknown`、`authentication_path: existing_session|managed_provider|missing|unknown|failed|new_credentials|native_authentication`、`permission_change: none|persistent`、`secret_handling: provider_only|expose|transfer`、`platform_requirement: none|confirmation|denied|trust_record_edit`。不接受秘密值、账号名或 `internal:true` 作为授权依据。

既有会话或提供方且范围匹配时建议 `reuse_existing_authentication`；内部和外部身份都受原项目、任务、workspace 及平台拒绝检查，身份分类不创造权限。路径缺失/未知返回 `needs_evidence` 与 `discover_existing_authentication`，缺的是有界路径证据，不是重新申请维护权。失败路径或仍有效的 `context.tool_failures.kind: authentication_failed` 对同资源的认证重试返回 `failed_authentication_no_retry`；不登录的发现另用 `authentication_path: missing|unknown` 检查，只能读取允许的非敏感引用。普通 1045 不自动升级成平台硬拒绝。已证实只能由本人完成原生认证时，用 `native_authentication` 返回 `native_authentication_step_only`，仅交该步骤，不新增用途授权。

新凭据、持久权限、秘密传输需要 `change_authorization_ref` 引用原生用户授权，关联相同资源、`result: granted`，其 `workspace.access_changes` 分别列 `new_credentials`、`persistent_permission`、`secret_transfer`；秘密传输还须提供真实 `action.workspace` 的 `transfer` 效果及原有目的地/风险核对，不能用本地读标签掩盖。`expose` 阻断秘密读取/输出，`trust_record_edit` 阻断直接改信任。平台逐次确认须原生 `confirmation_ref` 绑定同资源且其 `workspace.effect_keys` 含本动作 `effect_key`，不能用一般维护授权替代。`access_route` 仅给当前输入的下一步建议；实际受支持入口、真实认证是否可用、原授权语义和平台确认仍由执行者核验，不是登录工具或自动审批。

## 阶段结果后的接续建议

`progress.continuation` 是对原任务和唯一清单的临时有界投影，不要求调用检查器或建立新文件。`review_ref` 指向当前线程的 `goal_review` 原生消息/命令输出，正文为 `{"goal_ref":"本轮已选目标ref","step_ids":["全部仍有效剩余步骤ID"]}`；与 `progress.steps` 比较已给集合，缺项或额外未回看的步骤不能通过。回看内容由模型依据全部原目标解释，程序不能发现调用方未提供的目标。`waiting/paused/completed` 或 `stage_end/handoff` 无此原件返回 `remaining_goal_review`；其他阶段仍可作旧输入关系检查，输出 `continuation_state: not_checked`。已提供的同目标较新回看不能用较旧回看跳过。不能以删掉可做步骤、最后子任务返回或清单合并宣布整体完成。

有可做步骤时用 `selected_step_id` 指向 `ready` 项；本任务直接执行或以现有授权交接。可选 `handoff` 只保存 `target_task_ref`、固定 `version`、`claim: suggested|sent|received|executing|returned`、原生 `authorization_ref` 和 `sent_ref/received_ref/execution_ref/result_ref`。直接用户授权可复用，委托包装不是消息授权；授权证据关联所选资源，`workspace.target_task_refs` 列出已授权目标任务，不以泛化维护授权替代消息授权。发送证据须为当前线程的原生发送工具回执且含目标身份；按完整工具名与已支持的 codex_app/cloud_threads 命名空间组合核对来源，不能由同名函数尾部推导提供方。回执按已支持的直接目标对象或 MCP content/structuredContent 包络解析，不从任意嵌套对象搜索目标。显式 success/isError 必须分别为严格布尔 true/false；状态须是已确认发送值，error/errors 须是相应无错形状。缺目标、非法类型、未知状态、冲突目标、重复 JSON 字段、内外层失败及不支持的包络均不确认发送，也不绑定接收。原生成功包络未提供可选标志时仍按原格式核对，不强制增加标记；额外元数据不能创造发送证据。接收证据须为目标线程的原生委托输入，带当前发送线程及相同版本；执行和返回引用同目标、版本与相应原生消息/命令。各证据的 `kind` 为 `handoff_sent|handoff_received|handoff_execution|handoff_result`，均用原生 item/digest 绑定；跨线程事件不使用本线程的 `linked` 冒充。只有实际可核接收时 `handoff_received` 为 true；`coordination_owner` 始终保留在发送协调方，只有 `step_owner` 在接收核实后体现子任务承接本次范围；整体目标责任不释放。

建议、发送、接收、执行和返回分开输出；较高声明缺来源返回 `handoff_<状态>_unverified`。结果返回只证明本次交接已返回，不表示验收通过或全部目标完成，模型仍在本轮回看并选择下一步。`context.tool_failures` 中 `user_cancelled` 以同资源/动作效果匹配并停止该动作，不与平台拒绝或普通超时混同；原在途去重、拒绝、预算、版本及独立验收检查继续优先。发送超时不自动转重试，先回读现有动作和目标；检查器不派发任务、不确认外部真实性、不提供后台唤醒。真实接续验收使用正常结果链，未发生的分支保留未运行。

`stop_allowed` 只在已核输入支持 `completed`、具体阻断下的 `waiting` 或直接用户 `paused` 且聚合 ready 时为 true；working 即使 ready 也为 false。`next_operation` 分别提示回看目标并选择、实际执行/获准交接、核对原发送接收、回收返回后继续或报告具体合法停止。它不拦截最终回复，也不能证明主会话调用了本检查。各可做步骤仍从 `work_partition` 取回，不因聚合缺证据而全局停工。

在 `stage_end/handoff` 声明 working 时，仅 selected/sent 不足，返回 `continuation_action_unverified`；已核接收或本任务实际动作才支持本步已推进。handoff returned 返回 `returned_result_requires_next_action`，须把结果作为新事件回看当前完整目标并实施下一步，不能复用旧交接当作返回后的动作。可选 `event_ref` 为当前线程 `continuation_event`，`related_ref` 关联本轮目标，来源须为原生用户输入/工具输出/命令；其后才有本轮 review。可选 `execution_ref` 为当前线程 `continuation_execution`，`result: observed`、`related_ref` 指向本轮 review、`resource` 精确匹配所选动作，来源为 review 后实际成功结束的原生命令；失败、未知、旧命令或助手承诺不算动作。输出 `local_action_observed` 仅核对这些已给关系，不验证调用方原件真实性或命令业务效果，也不把一次动作写成全部目标完成。无需为使用这些建议新建记录表或固定调用。

`claim: paused` 使用 `pause_ref` 指向当前线程直接用户 `pause` 输入，`related_ref` 关联当前目标、`resource` 与 progress.resource 相同；文本 `<codex_delegation>` 包装、结构化 `codexDelegation` 标记及助手消息均不是直接用户暂停或消息授权；两者共用来源判定。暂停须在目标后且在回看前，覆盖所有已列剩余资源，不得同时选择、执行或交接暂停工作。局部暂停外仍有独立资源时不能据此停止整体。用户意思与遗漏目标仍由模型核对，字段标签本身不证明暂停。平台拒绝、取消、在途效果去重、最新目标及原有授权检查不被上述建议豁免。实际采用验收另取真实返回→下一动作→接收/执行证据，至少覆盖再次返回后的下一条边；模拟输入、发布安装和单次交接均不能替代。

## 原生补证与结果收讫

按实际提供方及版本解释读回。`cloud_threads.read` 的已见 `structuredContent` 投影可核对目标 thread、返回时最新轮、消息所属轮、正文与截断标志；助手 role、自报 request/hash 和最新轮 completed 不能补成原入站来源、历史轮终态或原生 final 身份。完整排序、分页及 final 合同未取得时保持具体未知，不从一个样本推断平台永久没有该能力，也不将它改装成另一个提供方的原件。

先寻找同一已授权对象的既有补证入口。例如已支持的 Codex App 原生历史能够提供原委托、逐轮 status/error 和 `agentMessage.phase` 时，以真实分页、精确原输入/轮次及报告读回核验；入口真实可用及权限须实际确认。注明是哪一调用方、哪一提供方完成程序绑定，不能把补证执行方的通过写成父 cloud 调用已通过。不能为补字段新建平台、权限、凭据或绕过拒绝。

程序绑定、原协调的实质验收、按事实人工收讫分别报告。机械证据不足时，原协调仍可在原授权内审阅已核的原生操作、提交读回、报告字节、对象投影与限制，明确接受该有限结果；这不伪造缺失的 final、撤销原失败或免除真实剩余义务。沿原任务和原有清单记录决定，不新增必填审批或第二台账。

已经成功合并而回执生产/返回失败时，先只读核对原成功 committed readback、当前相关投影、原报告身份和下一范围。相关事实吻合并已获原协调收讫后，不重合并、不重放原发送，也不为补形式回执另起循环。原消费合同确实仍要求新的原生回执时，用原任务的一次明确只读补修请求和新请求 ID，引用旧请求与原提交，消费原读回及绑定而不重做写入；新回执对应新请求，旧 final 不替换。未来有写入的原合并仍先做报告字节/类型预检。

多来源、缺项、迟到等受控回归的 pending 是该测试输入的预期投影。测试目的已验证并获验收时保留对照，不自动生成业务续跑；未提供或未履行的真实请求继续按来源及范围保留。测试标签或调用者自报完成本身不构成豁免。

交接校验库的用法见核心仓 README。原调用者取得已核发布提交的确切源码和指纹后，在原 send/read/report 执行单元加载；可复用仍适用的已验字节，每个新 V8 单元重新绑定函数。库 API、标准 CLI 包、当前任务实际采用和持久触发分别验收，不把库源码发布说成已全局注册回调、原生 Hook 或后台调度。

## 当前委托与返回对象

现有目标、回看、验收与独验互相一致，仍不能证明调用者选中了当前委托。需要核对该关系时，可选 `progress.current_request: {input_ref, report_ref?}`；沿用原 `context.evidence/native_sources`，不建立新表单、队列或必填门禁。`input_ref` 显式选择当前线程 `task_request` 或原 `continuation_event`，关联已核 `resource/version`；原件为直接用户输入、原生 `cloud_threads.send_message` 接收输出或带 `codexDelegation` 的用户输入。冻结版本须作为完整标识出现在该原件中，来源、作用域及顺序仍需核验；委托只定义本次工作，不产生消息、暂停或资源操作授权。

`report_ref` 指向本线程 `task_report`，`related_ref` 精确关联选定输入、`resource/version` 相同、`result: observed`；原件须是输入后成功完成的报告读取命令。JSON 报告顶层 `version`、`manifest_sha256`、`candidate_manifest_sha256` 至少一项且所有已给项完整相等；提及新 SHA、旧报告追加新字段或同系列旧版本均不能替代。结果使用精确 `outcome: passed|failed|not_run`，或明确的顶层 `verdict: PASS/FAIL/NOT_RUN`（可接大写字母数字下划线限定项）；同时出现时须一致，未知或冲突保留缺口。这是支持的结构化报告标记，不能从任意正文判断结果。项目报告不符合该合同的，保留缺口并由项目方法提供适用原件，不自动改写旧报告或真实结果。

输出仅在提供该关系时增加 `current_request`：`state: unverified|selected|report_matched`、原输入/资源/版本、报告及 `outcome`，并列出本次已提供 `task_request` 中尚无匹配报告的 `pending_request_refs`。已提供同一冻结委托的有效失败报告不能被另一 PASS 隐去，保留 `current_request_failure_unresolved`。FAIL 返回 `current_request_report_failed`，未运行返回 `current_request_report_not_run`，均不能作候选通过；报告匹配也不代替原目标验收和独立 lock/verdict。声明 completed 时，回看必须在当前输入后，完成资源/版本及验收、独验均与选定对象比较，缺报告或其他已提供待回收对象不允许停止。普通事件的版本可以与下一全局动作不同，不全转成新目标；多个独立委托可显式返回其中一个，其他对象继续待处理。明确 `task_request.related_ref` 指向所选旧输入的修订须核顺序并重选，不能静默替换。 重选后，仅沿本次已提供、同任务项目且来源/版本/旧先新后顺序均可核的显式替代链剔除过时输入；目标原件不是可替代委托，独立待办不因较新时间或相同名称消失。缺失原件、断链、未知顺序或环保持具体缺口，不要求已替代且未执行旧版本另交报告。`superseded_request_refs` 保留已核过时输入，`superseded_failed_request_refs` 保留其已核失败；同资源同冻结版本的失败仍不能以换委托引用清除。

未提供该关系的旧输入及完整输出继续保留原意义，不声称自动识别遗漏的委托、拦截最终回复或验证宿主实际采用。回归原错配时保留原输入输出，以逐字原生输入/报告配上显式选定关系分别检查；旧无关系投影的历史盲点不能冒称已经自动消失。实际同线程接续仍沿正常委托与返回取证，不重放模型或伪造事件。

## 可选资源角色与方案核对

资源方案先由模型按[资源角色与处置](../../york-workstation/references/resource-lifecycle.md#资源角色与处置)理解实际用途、用户重建契约、准确对象及依赖。复杂或反复偏移时，正常 `phase: action` 可附 `action.maintenance`，不提供时旧输入、输出和退出语义不变，不增加每次维护必填表、台账或审批。检查只校验提供的有界事实关系，不解释自然语言、发现遗漏消费者或授权停删；模型仍须理解原件、用户日常基线及实际目标。

该可选输入含 `contract_ref`、`observation_ref`、`strategy: rebuild|preserve|hot_takeover|discover|temporary_relief`、`max_age_seconds`（按本动作选1–86400秒的整数）和可选 `claim: plan|complete`、`goal_ref`。引用沿用 `context.evidence/native_sources`，同 project/task 且 resource 精确匹配；不是另建资源管理程序。`resource_contract` 是有摘要的原生直接用户声明或成功命令取得的有结构契约，正文选定 `resource`、`role: rebuildable_model|retained_state|external_business|personal|unknown`、`retention: none|required|unknown`、非敏感 `rebuild_ref`（无来源为 null）、`availability: restart_allowed|continuous_required|unknown`。普通对话无需为检查器重写为JSON；只有已有此类结构原件、且本次检查确有帮助时才用，未结构化事实由模型按同一方法直接判断。

`resource_observation` 须为成功、完整原生命令及摘要，正文为 `resource`、原观察时刻 `observed_at`、布尔 `target_verified/retention_conflict/normal_baseline`、`dependencies: clear|quiesced|busy|unknown`、`goal_result: passed|failed|not_run`。仅用于本轮判断，真实原件和字段语义仍须核实；某一瞬间无连接不能推出无间歇消费者，重读不刷新时刻。complete 还需选择当前原生用户 `goal_ref`，观察证据的 `related_ref` 关联该目标；通过标记不代替实际客户端/容量预期及原 owner 验收。

rebuild 需要当前契约可重建、当前数据无需保留、重建来源、允许中断，及准确对象和已处理依赖；冲突、陈旧或未知均不直接停删。hot_takeover 必须有实际连续性要求，不能从数据库名称推定；temporary_relief 不能签 complete，关闭 Chrome/IDE 不能作为正常基线容量方案。新字段不清除旧的范围、在途冲突、平台拒绝、跨环境授权或删除确认；只读标签也不能包装重建副作用。输出 `resource_route` 仅建议下一步，ready 不证明事实真实或原资源已经恢复。正式验收仍分开核对固定源码、实际安装、模型采用及资源 owner 的真实结果。

## 实施依据的可选关系核对

有争议的前提可在原有临时 `context.evidence` 中以 `kind: premise` 引用原件，附 `epistemic_status: confirmed|assumption|unknown`、适用 `resource`，转述附 `origin_refs`。`action.basis_refs` 仅列本动作实际依赖的事实；未提供时旧输入输出语义不变，不要求每动作填表或运行检查。来源仍使用原生 `task_ref/native_item_id/digest`；跨任务转述可引用同项目的原始来源，不改写它的来源线程。

非只读决定逐层核对所给原件和来源链；assumption/unknown、不明来源、断链、环、缺失摘要或不支持的来源形状返回具体缺证据。助手的 confirmed 标签不能自证根事实；没有原出处的其他任务摘要也不能洗白假设。可核的直接用户事实陈述或成功观察命令只提供根来源资格，原文是否蕴含该事实、是否仅是未来要求、来源是否完整真实及对象是否匹配仍由执行者判断；程序不会读懂任意正文。确认状态不赋予操作权限，原作用域、拒绝、独立验收和平台确认继续核验。

明确纠正可用同一原件引用 `supersedes_ref` 指向被纠正前提。检查器要求已给同对象直接用户原件与可核旧先新后顺序，旧前提及其派生转述不能继续作为依据；输出保留 `lineage_refs/superseding_refs`。跨来源顺序无法核实则保留具体缺口，不能自行拼造时间或用户原话。模型仍负责撤销错误依赖和向活跃任务传播、核实采用；检查器不发消息、不自动改计划，也不能发现未提供的纠正或省略的祖先。

`read` 中的前提仅表示查证方向，返回 `inquiry_only`，不要求先证明问题才可在原权限内取证；资源范围和真实拒绝仍适用。`progress` 沿原有每步 `work_partition` 输出：需要该事实的步骤缺证，独立步骤仍可做。`progress` 的依赖沿上面的原件与消费者合同核对；执行者仍须解释原文的实际必要性、撤掉错误边，不能将程序关系检查当成业务判断。测试 `tests/test_orchestration_basis.py` 验证有限程序边界；还需用原失败委托、纠正接收/采用和集成后实际任务动作独立验收，不能把该测试或 `ready` 说成真实编排保障。


## 持久执行与即时检查的边界

`check` 继续只核已给输入关系，不持有任务。用户明确采用持久执行时，正式 `codex-framework task` 入口负责登记、版本提交、原生运行结果后的必经验证、有界接续与恢复，方法见核心仓 README 的“持久任务执行入口”。节点保存当前需求/验收、执行身份、结果及下一步；模型维护记录，不要求用户填写。没有调用该入口、当前宿主不能接入或原生权限/确认仍缺时，分别保留未采用和具体阻塞，不能以源码/安装通过冒签实际闭环；独立工作继续。


## 项目关系与最终收口

持久 `task` 可保留项目 goal/responsibility/obligations、assignment owner/role/delivery_owner/covers 和原生 request_identity。模型从项目与当前请求原件维护，不以自报身份扩大授权。原任务状态和 `accepted` 只覆盖该任务；`coordinate`/`work` 按明确 task_ids 查看义务遗漏、真实依赖及当前可做项。持久 `task` 的本地 `depends_on` 也逐边核已取得的消费者契约原件；`depends_on_basis` 的 source_ref/source_sha256 锁定 workspace 内原 JSON，准确对应两端当前工作与所需结果/共享效果，合同见核心 README。缺失、hypothesis/unknown、部分边缺证或假引用放入原 work_partition 的 unresolved，不自动硬等待或派发消费者；上游失效沿已证必要关系逐级传播，未证依据的后继仍保留缺证；external 被动文件核验不能从默认输出 write 标签推导 shared_effect。无依赖步骤继续，原准确拒绝/在途 UNKNOWN 保留。跨任务 requires 的 proven 关系必须有准确任务、输入版本、来源与消费者原因；源码关系不能认证来源正文或授权本身。

已登记文件输出及显式逻辑资源使用有界占用元数据协调；索引不持有项目待办，不能按项目/数据库标签制造全局停工。历史拒绝沿原对象效果保存；普通资源共享、兼容读和离线工作分别判断。无原生真实调用的确定性回归只证明程序分流。

finalization 关联当前 components→integration→实际合并/加载 artifact→独立 final_regression→coordination 原清单更新。统一 artifact 逐项绑定项目 Git ref/commit/tree 与实际加载文件；最终报告必须对应本轮 task/request/input、同一 artifact、当前分项结果及原独验内容。汇总读回原组件独验与实际 Git blob/加载映射；受检树不同于合并树时，只能沿原独验明确的受检文件范围复用相同字节和 Git mode，不由集成者自行缩小范围。最终 `component_results` 还绑定结果、review 原件哈希和准确采用对象，不能用输入字段相同代替内容一致。缺合并、实际加载、总回归或原清单文件效果时，三个局部 PASS 不能令 overall_complete 为真。当前请求报告可用 verify-return 核对，旧报告不能仅换任务标题后回收。


可选持久原目标接续使用核心 README 的“原目标结果与当前执行机会的接续”：
复用已有未知结果 `review_handoff`，实际 final 绑定后由原消费者收取；消费前保存原意图，
丢回执/UNKNOWN 只核原消费，不重复操作。`result_scope/collect-result/review-result`
把当前实际结果及领域独验关联原义务，文件检查通过或模型轮次结束不关闭未答原目标。
`checkpoint/continue` 只读回已有 operation/attempt/native turn 和输入/首次冻结/效果，
没有新增唤醒、模型、业务提交或投递能力。字段不能认证原件作者，原生结果不可见就保持未知；
每次继续须有真实宿主执行机会，现场连贯性另按真实触发、接收和后续事实验收。
