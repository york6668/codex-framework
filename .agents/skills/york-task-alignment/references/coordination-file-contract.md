# 可选本地文件契约与模板

`codex-framework coordination` 从标准输入消费一个 JSON 请求，输出一个 JSON 结果。格式编号 `format_version=1` 独立于核心产品版本、需求/计划修订、原生轮次和内容 hash。只对已明确选择的本地文件读写；不扫描目录、不跟随记录内 URI、不联网、不启动模型、不发送消息、不执行领域动作、不创建 Task Store。实际权限仍由当前工具、环境及用户授权决定，owner 字符串不认证调用者。

已有系统优先用原格式/写入机制，逐字段映射所需关系；不把本格式未知字段塞进旧 `FORMAT=1` Task Store。文件绑定的系统、节点、字段、唯一写入者、允许共享范围及读取入口须由原责任核实。缺机制才使用此适配，不要求所有会话/系统迁移为统一 schema。

## 数据角色

| 角色 | 必需内容及权威 |
|---|---|
| agreements 快照 | `format_version/revision/authority{owner_ref,storage_ref}/agreements`；每项稳定 `id` 与字符串 `version`、`status`、`kind`、`source{ref,level,occurred_at}`、`statement/interpretation`、`applies_to{systems,objects}`、`canonical_refs/supersedes/changes/unresolved`。`systems` 可附具体任务/进度/需求及节点入口绑定，保留“已核/未核/不可达”观察。原聊天仅在私人资源保存必要摘录。 |
| tasks 快照 | 上述格式/修订/权威，另有 `system_id/tasks`。每项 `task_id/request_ref/requirement_ref/input_revision/plan_revision/agreements`、`scope{goal,objects,excluded}`、`acceptance[{id,criterion_ref}]`、`assignment{executor,delivery_owner}`、`write_scope/execution/native_binding/dependencies/plan_changes/stop_conditions`。执行/原生身份缺项明确 null/unknown，不能用助手 ID 补造。 |
| progress 事件 | 每行一个对象，`format_version/system_id/event_id/seq/task_id/request_ref/input_revision/plan_revision/kind/actor`、`occurred_at/observed_at/read_at`、`evidence_refs`。时刻带时区，未知明确 null。事件附实际节点、编排宿主、原生 provider/对象/轮次/修订、产品/内容版本、有限结果和未完义务；仅所需原件引用，不复制完整执行日志。 |

共识状态为 candidate/effective/superseded/withdrawn/conflict，kind 为 requirement/fact/decision/suggestion；版本及替代引用唯一且无环。来源真实性、语义适用与先后仍由原责任判断。任务消费准确共识版本。`dependencies` 每项为 `{producer_ref,consumer_ref,required_input_ref,required_version,reason,source_ref,status}`，status 为 proven/unknown/hypothesis；工具展示关系，不自动发起或等待消费者。

任务需求变化把旧 `request_ref/requirement_ref/agreements/scope/acceptance/write_scope/input_revision` 完整追加到该任务 `input_history`，`input_revision` 与 `plan_revision` 各加 1；`write_scope` 的增减涉及原写入范围，属于需求输入变化，字段本身不授予权限。单纯实施变化仅 `plan_revision` 加 1。每次变动追加 `plan_changes`，含 `{revision,input_revision,kind,source_ref}`，需求变化的 kind 必须为 `requirement_change`。不能删除原任务、义务历史、共识版本或旧事件。共识既有版本的内容不可修改；状态变化只允许附 `status_changes[{from,to,source_ref,reason}]`，保留前缀和新修订依据。必要归档/权威迁移由原 owner 使用原机制另定，不能静默丢弃历史。

## read / commit

以下都是合成模板，`/explicit/...` 必须换为本轮已核且获准文件；示例不创建权限或真实节点。

```json
{"operation":"read","binding":{"kind":"tasks","path":"/explicit/authorised/tasks.json","format_version":1}}
```

`kind=agreements|tasks|progress`；绝对规范路径不含 `..` 或 symlink。read 不创建文件、目录或锁，返回实际 `sha256/read_at` 与快照 `document/revision` 或 `events/last_seq/history_complete_within_file`。缺文件返回 read_unavailable，不能等同已接入。读取时刻不替代源观察时刻；完整文件也不能证明调用者没遗漏外部历史。

快照写入使用下一完整 document：

```json
{"operation":"commit","binding":{"kind":"agreements","path":"/explicit/authorised/agreements.json","format_version":1},"expected_revision":0,"create":true,"change_id":"demo-initial-record","document":{"format_version":1,"revision":1,"change_id":"demo-initial-record","authority":{"owner_ref":"demo:personal-owner","storage_ref":"demo:private-agreements"},"systems":[],"agreements":[]}}
```

空列表仅供新文件初始化，更新既有文件不得静默删除历史。首次创建必须显式 `create=true` 且 expected=0，父目录必须已存在；工具不创建节点或父目录。既有快照只允许 expected_revision 与实际相等、document.revision=expected+1；每次写的 document.change_id 与请求一致。同机 `.coordination.lock` 下重新读取/验证；完整临时文件 fsync、原子 replace、目录 fsync 并读回。锁忙或修订冲突返回 revision_conflict，重读后合并；外部不遵守锁的编辑在替换前再比较字节，仍不提供对恶意并发或跨设备的锁保证。

若原 owner 已约定另一个锁，binding 可显式附 `lock_path`，必须为同父目录下另一个规范绝对文件路径。例如原共识写入者用 `/explicit/authorised/agreements.lock`，继续使用该同一锁；不猜锁路径，也不在另一锁下并行写。未指定时默认 `<path>.coordination.lock`。选择锁仍不转移写入责任，实际采用前由原 owner 确认承接。既有快照根字段及 task 行的未知扩展字段完整保留（扩展值内的嵌套内容一并比较），删除或改变扩展语义走原适配器；不能因只知道最小 schema 就丢弃源分组、保护约束或领域元数据。

已有 `format=1` Task Store 的任务/进度继续使用 `codex-framework task` 的 status、amend、原结果核收等既有入口，按原 expected_revision 和输入契约操作。`coordination read/commit` 不接受它为 tasks 快照，拒写是防止误迁移；不要为绕过格式错误另造 tasks/progress 权威副本。仅通过原字段引用/内存投影消费该记录，是否需要未来只读映射由真实消费者缺口决定。

事件写入使用 `expected_last_seq`，不能混用 snapshot expected_revision：

```json
{"operation":"commit","binding":{"kind":"progress","path":"/explicit/authorised/progress.jsonl","format_version":1},"expected_last_seq":0,"create":true,"change_id":"demo-received","event":{"format_version":1,"system_id":"demo","event_id":"demo-E1","seq":1,"change_id":"demo-received","task_id":"demo-T","request_ref":"demo:request","input_revision":1,"plan_revision":1,"kind":"task_received","actor":"demo:engineering","occurred_at":null,"observed_at":"2030-01-01T09:00:00Z","read_at":"2030-01-01T09:01:00Z","evidence_refs":["demo:native-input"],"result":"received_not_executed"}}
```

事件从 seq=1 连续追加，event_id 唯一。输出为 UTF-8、键排序/无多余空格的 JSON 加一个 LF；相同 event_id 与相同完整行字节返回 duplicate_reused（即使请求 expected 已陈旧），字节冲突返回 event_conflict。读入也只按 LF 分行；CRLF 中的 CR 作为 JSON 尾空白合法保留，裸 CR 不能切开两条记录。外部导入不同空格或 CRLF 的原行与新规范行若字节不同，仍不擅自规范化覆盖。历史字节保持原样；在有限文件内复制旧字节并追加完整行，以原子替换实现逻辑追加。半行/缺序/重复身份不自动修尾，返回 incomplete_history 或 invalid_record，留原文件供原 owner 从原件修复。

单文件 1 MiB，快照各 256 项，事件 4096 行，CLI 输入 4 MiB；这是该有限适配器的读取预算，不是所有系统的数据保留策略。超限报 budget_exceeded，不截断假装完整。JSON 重复键、非有限数、未知格式、普通文件以外对象及硬链接均拒绝；旧格式不迁写，原系统走自己的入口。

## 有限 view

view 请求显式提供已读 `agreements`、`tasks` 两个完整 document、`progress` 事件列表，以及 `history_complete/as_of/max_age_seconds`；可附 `source_read_gaps:[{task_id,source_ref,status}]`，只限制其准确消费者的核收关系，不转为领域 FAIL。它不从这些对象的路径隐式读文件、不查询原生平台。history_complete 是调用方的有界声明，不能由工具认证；序号缺失仍拒绝。

需要程序投影核收时，task 附准确 `result_binding={kind,id,path,node_id,product_version,content_ref,effect_key}`；目标效果事件也带同一完整对象。复用 `source_evidence.resource_scope_matches` 比较所给全部字段，不能用 A 节点/输入/版本的结果替 B。这里 content_ref 必须由调用方绑定确切内容/原件，工具不认证引用或自动计算领域内容。

1. `diagnostic_returned/selftest_pass/native_terminal`、读取失败和传输 ACK 不关闭验收。
2. `goal_verification_pass` 需 result=PASS、`covers` 指定 acceptance IDs、准确 result_binding、非空原证据引用及非未来 observed_at。
3. 后续 `delivery_accepted` 需同请求/输入、actor 等于所给 delivery_owner、非空 `decision_ref/evidence_refs/effect_event_ids/closes`，所引较早效果覆盖该义务。程序只核关系，不能认证 owner 或代替实质领域决定。
4. 当前有效且适用的共识、完整历史及匹配结果齐全才返回 `scope_accepted_in_supplied_records`。系统/对象按准确集合匹配；共识附 stages/product_versions 时，任务也须提供所消费 stage/准确产品版本，不猜测适用。旧输入事件留在 historical_event_ids；旧 FAIL/UNKNOWN 及限制仍列出。逐义务以核收实际引用的最新匹配 PASS 为效果依据（acceptance_effects）；其后任一同结果范围 goal_verification_fail/delivery_rejected 的 FAIL/UNKNOWN 都令相关义务待核，包括失败先于核收或再次核收。重复核收旧 PASS 不刷新效果；只有引用失败之后的新目标 PASS 才能支持后续核收。其他义务继续按各自所引效果判断。稳定核收不因年龄过去自动失效；最新观察的新鲜度只用 observed_at，与 read_at 分开。新事实可能影响稳定结果时仍由原 owner 判断。
5. `platform_denied/effect_unknown` 按稳定效果身份 kind/id/path/node_id/effect_key 关联，不因任务 ID、需求或内容版本变化清零。普通 PASS 不解除限制。UNKNOWN 只有所给原 owner 的 `original_effect_reconciled`、同一效果、related_event_id、result=committed 及原效果读回证据才可在视图标记已核记录；原 UNKNOWN 仍保留，不给予重试许可。平台拒绝不由本适配器消除，具体新证据交原允许入口核实。
6. `successor_executed` 带原 acceptance 的 related_event_id 及实际证据才列为后继；或 `legal_stop_recorded` 带具体 reason 和证据。缺项标 next_action_review_needed，不生成队列或实际执行。

源入口不可读作为 source_read_gaps 保留，不能转为业务 FAIL；调用方不得省略决定所需的读取缺口来宣称完整。关系视图是当前已读输入的有限投影，不是另一个可编辑状态真值或全系统完整性证明。

## 部分成功与恢复

返回 read/read_only_view/committed/duplicate_reused 退出 0，其余退出 1；均不代表领域结果。CLI 的 tool_version/tool_revision 复用已有物理运行绑定，源码直调为 null，不能用活动指针补成当前实际加载。commit 成功给本对象/修订或序号、change_id、实际 hash 和 readback_verified。替换前写失败为 write_unavailable，原文件保留；replace 后落盘或读回失败为 write_outcome_unknown，先读同一对象和 change_id/hash/事件 ID，不能盲写。

每个文件独立提交，无跨文件事务。使用同一逻辑 change_id 关联一次变更的不同记录；例如任务 CAS 已成功而进度追加失败，应明确“计划已写，进度缺失”，保留各自返回并读原对象补记录。若先前是真实业务动作成功，记录失败只补记录，不能重做真实动作补账。snapshot UNKNOWN 读回后若同 change_id/内容已存在就消费该结果；event 完整行相同可重用。仍不明保留 UNKNOWN，交原责任处理。

文件机制只保护选用它的本地合作写入者。跨端仍由原 owner 通过已支持且已授权的入口操作和读回，不能共享路径同时写。格式验证、公开源码、安装或离线测试不证明平台采用、真实授权、原文/owner 身份或领域交付。
