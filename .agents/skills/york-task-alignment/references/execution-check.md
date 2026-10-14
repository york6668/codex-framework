# 执行与修复的即时检查

当任务涉及跨端接续、重复副作用、共享资源冲突、预算、故障恢复或缺陷关闭，而现有事实不能一眼判断时，可在执行前运行框架仓库的 `scripts/codex_framework.py check`。检查器只消费本次输入的引用和少量状态元数据，不读取或保存另一套任务日志、业务数据和凭据，也不创建任务、分配权限或替代 Codex 原生审批。普通明确且低风险的动作不必为了使用检查器建立记录。

先从原生任务接口定位原线程、相关工具调用与自动化；从节点实际入口读取能力、预算、数据状态及采用版本。已有原件保留在原处。把项目适用规范的引用、项目与当前任务的可修改资源范围、原生线程引用、拟执行动作、相关原生操作引用、节点事实引用放入临时 JSON，经标准输入调用：

```sh
python3 scripts/codex_framework.py check < /tmp/current-action.json
```

输入的 `project.id` 和 `task.project_id` 必须一致，`task.native_ref` 与 `context.task_ref` 都指向要接续的同一原生线程（`codex:thread:<id>`）。`project.scopes`、`task.scopes` 分别给出项目和本任务范围；每项由 `kind`、`id`、可选的相对 `path` 与 `access: read|write` 组成。`action.resource` 使用同样的身份字段，`kind` 是 `read|write|invoke|repair|publish`。资源身份应取自实际仓库、数据或服务，不用路径相似性推断归属。范围和路径的包含检查会拒绝跨项目及同项目其他任务越界。

有副作用的动作还提供稳定的 `effect_key`、`context.native_snapshot_ref`、`native_snapshot_complete: true` 和相关 `native_operations` 的引用、任务、项目、资源、effect key 与 `succeeded|running|failed|unknown` 状态。这里的 complete 只表示**本次副作用范围内**的原生记录已读全；做不到就保留待核实，不能填 true 让检查通过。相同效果已成功、进行中或结果不明时不重复执行；失败后再试需对同一原任务提供失败引用、原因引用及已验证纠正引用。`depends_on` 指向已成功的原生操作；其他任务正在写同一资源或结果不明时先隔离冲突。操作需要能力或消耗额度时，同时提供节点快照引用、当前能力和剩余额度。入口或上下文失败只证明那次调用失败，不能据此断言功能缺失或实现缺陷。

`publish` 动作的 `release.version` 须与 `release.test_ref` 指向的已通过测试版本一致；提交或投递记录不能替代测试。原生工具仅返回命令退出码时，先核对实际副作用或产物，不能凭退出码把业务效果标为 `succeeded`。

缺陷修复后用 `phase: close_issue` 核对原失败、根因、修正、原场景复测、受影响路径回归、正式采用版本与实际效果。各项引用须在 `context.evidence` 中对应同一项目、原任务及期望的记录类型；原场景复测的 `related_ref` 指向原失败，回归、采用、效果的状态与版本须一致。证据数组只传 `ref/source/kind/project_id/task_ref/version/result/related_ref` 元数据；原始内容仍由 Codex 工具或节点读取。接受人与后续跟踪人分别明确。修复后尚未正式采用或还没有运行效果时状态是 `needs_evidence`，不报已闭环。

输出 `blocked` 表示输入中的已知冲突，`needs_evidence` 表示缺少决定所需事实，`ready` 仅表示这份输入内部一致。退出码分别是 2、2、0；格式错误为 1。检查器无法自行证明调用者提供的原始记录是否真实或完整，也不能拦截未经过它的工具调用；执行者仍须核对原件、遵守平台权限和业务授权。结果不明时先查询原动作，不能把一次新的检查当作重试许可。修订规则后用原失败输入和受影响行为复测；代码测试、文件安装和实际运行效果分别报告。
