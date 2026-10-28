# 具体问题的持续处置

本方法把逐问题根治接入共识整理、任务追踪、进度更新及结果核收，复用原核心模型编排、真实状态读取和既有调度，不另建助手模型或执行系统。普通小任务直接处理；反复修复、复发、原因未明或明确要求持续根治的问题才选用此承载。

## 问题与阶段分开

每个具体问题有独立标识、原要求/对象、实际影响、当前事实、原因假设、责任及根治条件。问题 ID、阶段任务 ID、版本、原生执行轮和根因假设是不同对象。一问题可跨多个调查、修复和采用阶段；一任务/补丁可涉及多问题，仍逐问题验收。分类仅用于浏览，跨问题关系另标 hypothesis/supported/ruled_out 并保留证据，不共享根因、状态或关闭权。

先映射原共识、问题/需求文件、原任务和结果的准确 pointer、原写入者及读回入口。原文件缺少跨阶段目标承载时才补最小私人记录，不再复制任务/运行总表。问题记录引用原任务的范围、输入修订、在途、结果及核收；不接管其事实写入。受拒 Task Store/effects、未决占用保留原位，新问题笔记不承接它们的核收、迁移、写回或清占用。

根治标准按具体问题确定，可能包括原失败复测、机制与反证、实际恢复、采用或足够的跨轮接续。源码、自测、独验、发布、安装加载、真实采用及长期效果分别说明证明范围，不把所有问题固定为同一模板。有效要求改变时沿原需求权威修订，保留旧定义及同一问题的关联；不能重写标准以便通过，或以新 ID 消除旧限制。

## 每个真实机会的工作流

1. **识别及历史。** 新用户纠正、失败/拒绝/UNKNOWN、阶段返回、发布采用、恢复、复发、自然回合触发模型判断。先关联已有具体问题，核历次修复的机制、版本/节点/输入、复测范围、复发及领域影响。相似现象不先合并根因；时间过去不证明失败，缺少返回不等于失败。
2. **核原结果。** 读取原任务当前输入、在途/终态、匹配产物及原 owner 的有限核收。事实、未核假设、计划、实际执行结果分开。依赖步骤必须取得适用对象/节点/产品及内容版本/请求/input_revision 的真实返回和新鲜源观察；重读不刷新源事实。复用原生读回或 `check` 的适用能力，普通任务不补登记；旧 Task Store 仅按[原结果交接](result-handoff.md)核对原身份、结果和必要恢复，已归档的 `task` 执行链不再作为当前入口。缺失、过期、错绑、截断、UNKNOWN、ACK、预计 PASS、历史成功均不能代理本次成功；先取证，真正独立的工作继续。
3. **模型选择。** 对照本问题根治标准与实际证明，解释因果未明项、已排除路径、尚未探索且合法的路径、真实依赖及什么新增证据会改变结论。具体路径说明能区分哪些解释、对象/目的地/副作用、实际入口及授权依据。字段校验、修复次数和提示词不能代替模型判断。旁支缺陷修好仍核它是否解释原失败；观测不足仍查独立合法路径。
4. **行动及有界分派。** 本范围可做就执行；确需原责任时，只沿已有消息授权及受支持的原入口交有限输入、范围和验收。先核在途及原效果，不因新任务名、sender、问题名或调度轮重派。选定路径、建议、消息 ACK 各自只证明该阶段，不填成已执行。原责任回收真实返回后再判断。工程范围及时交付，不能把其他系统修复塞入一个大任务。
5. **逐问题关闭及复发。** 调查完成只关闭调查，阶段 PASS 不关闭根治。只有该具体问题的标准与适用实质核收支持才关闭；补丁或分类不联动关闭他项。复发关联同一问题，保留原修复、关闭与适用版本/节点，重新判断和复测，不直接重用旧 PASS。

平台拒绝只停止准确对象/目的地/副作用，独立获准调查继续；不换任务、工具、通道、节点、目录、sender 或去掉升级参数重试。UNKNOWN 先核原效果/在途，不超时清占用或补发。同效果没有实质新相关证据就不盲重试；原允许的一次证据复核仍按异常方法处理，文件工具不解除限制。

确实没有受支持下一步时，保留 OPEN/外部依赖、原责任、准确缺失证据、可观察恢复条件及实际触发入口，不能自报根治或无限扩查。下一真实机会先比较证据变化；无变化复用原判断，不反复取相同证据/堆积相同记录。有变化才处理相应路径。

**用户停止优先。** 用户停止的具体范围停止排查/执行，保留历史及停止出处，等待其明确恢复；不是模型缺陷自动恢复对象。调度、复发、自然轮、超时和新 owner 均不解除用户停止；其他独立问题继续。

## 即时助手与既有调度兜底

双方共读原权威及准确问题/阶段引用，不各自持有聊天版状态。即时助手有回合即核收并推进；原小时任务在实际可访问和获准的范围运行同一流程，补漏、核当前在途/返回、交原 owner 核收、合并已独验分项、按既有授权发布/采用及完成下一合法动作，并非仅通知。具体动作仍依原目标、能力及消息/发布授权；问题记录不扩权。

启动、中断恢复和助手接回时读当前问题、原任务输入/轮次/效果及调度实际更新，再判断是否在途、结果是否已发生。活动任务回收而不并发重派；UNKNOWN 先核，拒绝不换路。CAS 只保护合作文件写入，不是任务租约，不提供跨端恰好一次。发起与写记录之间结果不明，先核原任务/效果，不重发补账。调度不因助手休眠接管领域关闭权，助手也不能沿休眠前计划猜测调度结果。

接入传递原目标/共识版本、问题文件/pointer、phase_refs 对应的原任务/输入/轮、当前 owner、证据与限制、下一动作或等待及恢复条件。原规划/调度责任维护唯一清单及原调度；本机制不新建/修改第二调度，不改变用户启停。无真实回合/接收/执行证据不声称后台持续运行。

## 可选文件契约

`kind=goals` 复用 [coordination 文件契约](coordination-file-contract.md) 的 CAS、原子替换、读回及 UNKNOWN。仅保存缺失的逐问题目标和判断；原系统已有字段时直接映射，不要求迁移。快照根为 `format_version/revision/change_id/authority/goals`，每项如下。

| 字段 | 含义 |
|---|---|
| `goal_id/source_ref/statement/owner_ref/original_refs` | 一个具体问题的独立根治目标与原权威关联 |
| `criteria[{id,criterion_ref}]/phase_refs` | 本问题自己的标准、有限阶段原记录引用 |
| 可选 `category/relations/repairs` | 浏览分类；关系含 issue_ref/status/statement/evidence_refs；修复含 repair_ref/version_refs/node_refs/verification_refs/recurrence_refs/impact_refs，保留必要原引用 |
| `reviews` | 追加实际触发时的模型判断；历史失败/未知/旧决定保留 |

review 含 `id/trigger{kind,ref}/reviewed_at/basis_refs/rationale`；本问题逐项 `criteria{id,status,reason,evidence_refs}`（met/unmet/unknown）；`causal_questions{id,question,status,required_for_closure,evidence_refs}`（open/answered/ruled_out）；`paths/attempts/decision`。旧因果问题不得在阶段边界消失，排除/回答需证据。basis_refs 用准确原生事件、文件 hash/修订等，不以新时间戳/读取次数/任务名当新证据。

path 可用 `basis_refs` 指明与该效果相关的实际源修订，避免无关共识更新被当成重试证据；未指定时使用本轮 basis_refs。path 含 `id/purpose/question_ids/action{object_ref,destination_ref,effect}`、`permission{status,evidence_refs}`（allowed/denied/unknown）、`capability{status,evidence_refs}`（supported/unavailable/unknown）、`dependencies`。dep 含 `ref/status/reason/evidence_refs`（satisfied/missing/unknown）。宣称 satisfied 还需 `proof{record,expected_scope,max_age_seconds}`，复用核心 source_evidence 校验和精确 resource scope；expected_scope 至少包括 kind/id/node_id/product_version/content_ref/request_ref/input_revision。原 JSON 需同 scope/result、completed 及源 observed_at，在 reviewed_at 的显式预算内。codex 原件使用 review.evidence_context 的真实原生快照；node 原件用明确 proof.project.root、相对 record.node_path/digest/version。只有显式 node proof 调用原有文件读取器，其余 URI 不自动读取。无证明只影响对应消费者，独立取证路径继续。

attempts 仅记录实际动作的 `id/action/result/basis_refs/evidence_refs`，result=done/failed/denied/unknown。计划或 ACK 不填 done。拒绝/UNKNOWN 按原效果跨问题/路径 ID 保留；本帮助器不提供清除限制接口。

原任务的普通条件与停止/拒绝按[条件方法](task-conditions.md)分流，原责任在原权威核真实满足后仅解除对应条件；已归档 Task Store 与受拒写回保持原位。新验证返回后重新读原事项、刷新本轮依赖及下一动作；已经满足的缺口不继续沿用旧 BLOCKED/等待。原系统状态由原写入者在授权内更新并读回，问题侧只补相应引用；记录回写失败先核已发生效果，不能重放领域动作补账。需要记录 UNKNOWN 已核结果时，review 可附 `reconciliations[{id,attempt_ref,action,result,evidence_refs,proof}]`，attempt_ref 使用 `{goal_id,review_id,attempt_id}` 结构身份；旧斜杠字符串只在所有所给原尝试中唯一可拆解时兼容，歧义不解除任何拒绝或 UNKNOWN，result=committed/not_executed。proof 仍用核心原件校验；expected_scope 还须包含 original_attempt_ref、effect_action、effect_outcome 并与原结果完全一致。核明已提交就不重放，核明未执行只消除这个 UNKNOWN 观察缺口，实际重试仍按原授权/入口及新证据判断；拒绝不被 reconciliation 清除，也不回写原 Task Store/effects。冲突或未证实继续未决。

decision 由模型选择：act 带 path_id/owner_ref/reason；wait 带 owner_ref/reason 及 `resume_when[{source_ref,condition,owner_ref}]`；close 还需原 owner 的 acceptance_ref、全部本问题标准已满足和必需因果问题已解。`CLOSED_IN_SUPPLIED_RECORDS` 只说明所给关系支持，不能认证身份、原因真实性或领域验收。recurrence 本轮重开，不能直接复用旧关闭。

用户控制在首次真实变化的 review 附 `user_control{id,kind,source_ref}`；kind=user_stop 或 user_resume，恢复还需 supersedes 精确引用原停止 ID。来源级别由模型按原授权核对，字段不是身份认证。原停止一直传到后续回合，输出 USER_STOPPED，不产生可执行候选；只允许用户明确恢复对应范围。

```json
{"operation":"read","binding":{"kind":"goals","path":"/explicit/authorised/issue-goals.json","format_version":1}}
```

模型读回当前原件并形成 review 后调用：

```json
{"operation":"goal-review","goals":"<替换为已读document对象>","goal_id":"<具体问题>","review":"<本轮模型判断对象>"}
```

省略 review 返回目标及最新判断，要求在本次真实触发重新判断，不复用旧行动执行。提供 review 输出未满标准、因果未知、候选关系、decision_supported/issues、selected_next_action 或 wait、evidence_changed/record_needed；始终 action_executed=false/root_cause_verified=false。有独立路径却等待、旧效果无新证据、受拒或 UNKNOWN 会显式指出。程序不证明模型列全路径、读到了全体在途、引用真实、权限有效或解释正确。

模型实际执行、核原结果后沿原权威记录，再用 commit 追加必要 review。冲突先重读，UNKNOWN 先核原文件；phase_refs/repairs/relations/reviews 仅追加，定义和旧判断不能静默改写。原需求真修订保留来源和同问题关联。没有新证据无需重复写同一等待，仍在真实机会由模型判断变化。

源码/合成反例、真实本机样本、独验、发布加载、助手/调度自然采用及跨轮效果分别验收。自测和一次实际选择均不能把持续根治目标由本次实施 completed 代签结束。
