# 执行与修复的即时检查

当任务涉及跨端接续、重复副作用、共享资源冲突、预算、故障恢复或缺陷关闭，而现有事实不能一眼判断时，可运行框架仓库的 `scripts/codex_framework.py check`。先由当前任务读取项目契约、原目标与原生/节点记录，检查器消费本次临时输入，不创建任务、持久日志、权限或业务数据，也不替代 Codex 原生审批。普通明确且低风险的动作不必为此建立记录。

先从 Codex 原生 `read_thread(includeOutputs=true)` 定位原线程、用户目标与相关工具调用；从节点实际入口读取能力、预算、数据状态及采用版本。项目规范、Git 提交和节点事实仍在原处。把适用规范引用、项目与任务范围、原线程、拟执行动作及相关来源放入临时 JSON，经标准输入调用：

```sh
python3 scripts/codex_framework.py check < /tmp/current-action.json
```

输入的 `project.id` 和 `task.project_id` 必须一致，`task.native_ref` 与 `context.task_ref` 都指向要接续的同一原生线程（`codex:thread:<id>`）。`project.scopes`、`task.scopes` 分别给出项目和本任务范围；每项由 `kind`、`id`、可选的相对 `path` 与 `access: read|write` 组成。`action.resource` 使用同样的身份字段，`kind` 是 `read|write|invoke|repair|publish`。资源身份应取自实际仓库、数据或服务，不用路径相似性推断归属。范围和路径的包含检查会拒绝跨项目及同项目其他任务越界。

新建或恢复相关任务时，`phase: resume` 以 `resume.resource`、`resume.goal_ref` 和选中的 `resume.accepted_corrections` 核对原目标来源、项目与任务边界、适用的旧纠正及其版本。先从项目现有契约和原任务历史识别适用纠正；程序无法自行发现调用者遗漏的项目历史。`ready` 后仍须在本轮行为中实际使用纠正并留下原生记录。

有副作用的动作还提供稳定的 `effect_key`、`context.native_snapshot_ref`、`native_snapshot_complete: true` 和相关 `native_operations` 的引用、任务、项目、资源、effect key 与 `succeeded|running|failed|unknown` 状态。这里的 complete 只表示**本次副作用范围内**的原生记录已读全；做不到就保留待核实，不能填 true 让检查通过。相同效果已成功、进行中或结果不明时不重复执行；失败后再试需对同一原任务提供失败引用、原因引用及已验证纠正引用。`depends_on` 指向已成功的原生操作；其他任务正在写同一资源或结果不明时先隔离冲突。操作需要能力或消耗额度时，同时提供节点快照引用、当前能力和剩余额度。入口或上下文失败只证明那次调用失败，不能据此断言功能缺失或实现缺陷。

`publish` 动作的 `release.version` 须与 `release.test_ref` 指向的已通过测试版本一致；提交或投递记录不能替代测试。原生工具仅返回命令退出码时，先核对实际副作用或产物，不能凭退出码把业务效果标为 `succeeded`。

发现偏差时用 `phase: feedback`：`feedback.resource` 是观察对象，`expected_ref` 与 `observed_ref` 指向 `context.evidence` 中同一资源的 `expectation` 与 `observation`。两份记录的 SHA-256 摘要须与提供的原生 item 文本或节点文件内容一致，程序比较后输出 `deviation`。期望标准应在观察前形成；摘要差异是诊断入口，不自动确定根因或许可重试。已有修正无效或复发时给 `prior_correction_ref`，仍返回偏差并标记需重新处理。模型沿原失败、实际路径和影响对象确认原因。`repair` 动作需给出失败与根因引用、原因层、受影响资源及原任务 `authorization_ref`；它可指向已有用户授权，不要求每次常规修复重新请示。平台权限仍由平台执行。

项目原有契约、修正代码及回归测试是后续接续的来源；不另建纠正台账。下一轮从这些来源和原生任务记录读出适用纠正，在本次输入的 `project.accepted_corrections` 放入其引用、资源、版本，并以 `project.corrections_source_ref` 指向所核对来源。`resume` 或 `action` 的 `accepted_corrections` 应包含同一资源的纠正及同版依据；程序会要求临时证据中有可核验的旧修正原件，遗漏、旧版或来源缺失不会得到 `ready`。空数组本身不证明没有历史偏差。

缺陷修复后用 `phase: close_issue` 核对原目标、原失败、根因、修正、原场景及受影响路径复测、独立目标验收、正式采用版本、实际效果与后续接续。`issue.goal_ref` 和 `acceptance_ref` 必须关联；目标验收不能复用局部回归命令。原场景复测的 `related_ref` 指向原失败，目标验收指向原目标，后续接续指向修正；回归、验收、采用、效果及接续版本须一致。仅命令退出 0 不证明业务目标完成，执行者须核对实际输出和目标语义。修后尚未正式采用、没有效果或没有后续使用证据时为 `needs_evidence`。

反馈、恢复入口和缺陷关闭的来源核验：`context.native_sources` 可临时放 Codex `read_thread` 的原始 JSON 响应；Codex 证据用 `native_item_id` 指向其中 item，非命令文本以 `digest` 绑定。成功证据要求原生命令 `completed`、退出码 0、输出未截断，带版本的结果还须在命令或输出出现该版本。节点文件证据用 `project.root`、相对 `node_path` 和 `digest` 核对当前文件；`passed/applied/observed/used_correction` 还须由 JSON 文件同时给出相同 `result/version`。临时输入可含原生响应以供本次比对，不能提交到框架仓库或复制成长期日志；取不到完整原件时保留 `needs_evidence`。来源由当前任务的受支持接口取得，检查器不能认证任意调用者提供的 JSON 未被伪造。

分工：程序比对来源片段、摘要、范围、版本、依赖和证据关联；模型负责目标语义、原件来源、根因、影响范围、修正与复测解释；用户、业务系统或节点提供真实期望、运行反馈和平台授权结果。没有后续观察时保持待跟踪。仓库 `tests/test_correction_loop.py` 是隔离程序边界测试；真实任务链需另用原生记录和节点原件复测，不能把夹具结果当实际项目效果。

输出 `blocked` 表示输入中的已知冲突，`deviation` 表示观察值与既有期望不符，`needs_evidence` 表示缺少决定所需事实，`ready` 仅表示这份输入内部一致。退出码分别是 2、2、2、0；格式错误为 1。检查器无法自行证明调用者提供的原始记录是否真实或完整，也不能拦截未经过它的工具调用；执行者仍须核对原件、遵守平台权限和业务授权。结果不明时先查询原动作，不能把一次新的检查当作重试许可。修订规则后用原失败输入和受影响行为复测；代码测试、文件安装和实际运行效果分别报告。
