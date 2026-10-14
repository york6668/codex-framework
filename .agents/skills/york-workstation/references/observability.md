# 工作站异常与可溯源性

核心框架定义跨项目的模型职责、判断语义与准入；每个节点提供实际来源，业务仓解释业务专用状态。复用 Codex 原生任务及工具记录、现有节点状态和系统日志，不读取私有 session 存储，不复制完整敏感载荷，也不建立第二套任务历史。工作站健康是与配置发现分开的当前事实；配置缓存的 24 小时有效期不是健康记录期限。

## 模型在何时读取

新建或恢复相关任务、准备会改变共享资源的动作、动作结果不明、故障反馈和修复复测时，先确定原生任务、项目、节点和采用版本，再从节点现有入口读取有界状态。用 `list_threads` / `read_thread` 或当前任务的原生工具记录追溯原目标、操作和结果；节点源文件及系统日志按对象、时间、尺寸限界读取。不能仅用一次检查通过、命令退出 0、容器当前 healthy 或清理声明推定资源稳定、业务效果或实际退出。

`python3 scripts/codex_framework.py workstation-check < /tmp/workstation-check.json` 是无持久状态的本地核对入口。输入只放本次需要的来源路径和判断范围；不得长期保存含原生响应或敏感内容的输入。结果为 `normal`、`abnormal`、`unknown`；仅 `normal` 退出 0，异常或未知退出 2，输入无效退出 1。检查器最多读 32 个普通 JSON 文件，每个不超过 64 KiB，不扫描目录、不启动服务、不执行 Docker 命令、不写节点状态。来源需包含项目、原生线程、节点、采用版本、带时区的观察时间；不具备这些字段的旧记录可作为故障线索，但不能证明健康。调用者仍须核实文件身份、来源完整性和状态的业务语义；检查器不能认证任意输入，也不能自行唤醒模型。

本机已有 `SessionStart` Hook 在 `startup|resume|clear` 时还会读取节点的 `$HOME/.codex/workstation/observation-sources.json`，将有界状态摘要注入模型上下文。节点清单只指定已有 JSON 状态文件、状态值与时效；可用 `follow` 沿状态文件中的安全运行 ID 读取一份最新运行原件。状态值的业务解释、路径和新鲜度由节点配置，不写死在核心。清单缺失、文件损坏、观察入口失败、正常状态过期均显示“未知”；已知失败保留“异常”，即使失败记录已旧。Hook 最多读 8 个来源，每个文件不超过 16 KiB，只读且不联网、不启动模型或服务。摘要只代表已配置来源，不证明整机或未登记资源健康；具体版本、操作和资源后置条件仍按原件及下述即时入口核实。

节点清单结构示例（仅说明字段，具体状态值由节点实际实现确定）：

```json
{"schema":"workstation-entry-v1","node_id":"node-name","sources":[
  {"id":"project-service","path":"/absolute/state.json","status_path":"status",
   "time_path":"observed_at","max_age_seconds":900,
   "ok_values":["HEALTHY"],"failure_values":["FAILED"],
   "follow":{"id_path":"last_run","directory":"/absolute/runs",
             "status_path":"status","time_path":"finished_at","max_age_seconds":900,
             "ok_values":["PASSED"],"failure_values":["FAILED"]}}
]}
```

这是任务入口的**启动或恢复时感知**：仍须在正常 Codex 启动中确认 Hook 已获信任且摘要确实进入模型、模型按原任务处理。任务空闲时即时唤醒模型不由此实现，也不应把手动运行 handler 当作端到端验收。

例如从节点已有状态入口得到一份绑定后的状态文件，再按当前任务构造短时输入：

```json
{
  "schema": "workstation-check-v1",
  "scope": {"project_id": "example", "task_ref": "codex:thread:original-id", "node_id": "mac-mini", "version": "commit-sha"},
  "sources": [{"id": "service", "path": "/absolute/node/status.json", "max_age_seconds": 900,
               "status_path": "status", "ok_values": ["HEALTHY"]}]
}
```

默认字段为 `project_id`、`task_ref`、`node_id`、`version`、`observed_at`；已有源字段不同时可分别用 `project_id_path`、`task_ref_path`、`node_id_path`、`version_path`、`observed_at_path` 指向嵌套 JSON 字段。新鲜度从源内观察时间算起；只打开文件或重新执行读取不延长时效。读取失败、缺失、超期、身份/版本不符、节点失联、观察程序停止、未知状态和磁盘写失败都不能得到 `normal`。已绑定来源报告故障时，即使后续过期仍保留异常线索，直到有新证据证实处理。

资源清理另给 `resource_claims`，每条包含 `effect_key`、`source_id`、`resource_id`、`expected_state`。来源必须是新鲜且身份匹配的完整资源快照（`complete: true`，`resources` 列表含准确 ID 和状态）；完整快照中缺席才可支持 `absent`。清理命令返回或缺少对象的局部查询都不够。模型还应核对操作请求、工具返回、业务后置条件和真实资源退出分别属于哪个任务与版本；结果不明先查原效果键，不重做副作用。具体容器、卷、进程等的发现、清理和恢复仍归实际业务节点实现。

## 保留与资源约束

优先引用现有轮转系统日志与原生记录。节点如确需补充少量摘要，候选边界为原子状态文件不超过 8 KiB、变化事件最多 14 天或 16 MiB；未解决故障只保留必要索引和原件位置，由节点按复现价值与实际容量设上限。不得把采样、留存或核对本身变成内存风险。启用持续观察前，在隔离输入上实测单次 CPU、峰值 RSS、I/O 和异常时写入失败路径；全工作站预算要包含共享宿主或 VM、已有服务、临时实例和并发，不以单容器限制替代总预算。

本地先固定故障、观察自停、过期/缺失、读失败、失联、错误归属、版本未采用、清理残留、重复效果、受限资源和健康路径的输入与预期，再手动经正常入口复测。离线通过只证明框架解释能力；实际 Hook 注入、异常触发进入模型处理、用户通知和长期资源稳定需分别有端到端证据。若当前没有模型执行机会或节点彻底离线，模型不能即时感知；任务间主动唤醒须另核实受支持入口，不承诺绝对零漏报，也不为本方法默认增设后台巡检或模型循环。
