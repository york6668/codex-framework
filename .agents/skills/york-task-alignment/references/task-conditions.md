# 原 Task Store 的普通条件、停止与拒绝

`task block` 写的是当前原任务的具体状态，不能把普通等待保存为平台拒绝。新的调用必须明确 `reason_kind`：dependency、capability、business_failure 或 user_stop，不从 condition 的自然语言猜测。前三类还需稳定 condition_id；缺分类拒写，避免旧调用把“取消”和“暂缺输入”混为一类。动态任务依赖继续优先使用已有 `requires/depends_on` 及其真实来源，不为采用本接口重复登记任务。

普通条件保存在同一 Task Store 可选 `conditions` 字段，保留原 step、请求/输入、原 owner、作用范围、条件来源和修订；不是另一套任务库。普通条件只影响相应步骤及已证消费者，不进入 `.effects` 拒绝/占用索引，也不使同对象的独立 peer 被误报为 retained_refusal。业务失败交原责任诊断，能力不足等待准确能力/证据，依赖缺失等待相应输入；都不能以字段为由无限搁置独立工作。

user_stop 继续保留硬限制，真实拒绝沿已有 approval/native worker 观察保存，UNKNOWN 仍核原执行/效果。内部索引既有 denial 存储标识保守保留原硬决定，不认证其为平台拒绝；status 的 restriction_kinds 分开显示 explicit_denial、user_stop 和 legacy_unclassified_stop。用户停止高于普通条件满足；平台动作许可仍由原生工具决定，这个接口不批准权限。

## 注册普通条件

```json
{"operation":"block","task_id":"original-task","expected_revision":4,"step":"a","reason_kind":"dependency","condition_id":"input-ready","condition":"等待准确的生产者输入","evidence_ref":"original:missing-input-observation"}
```

只选择已获准的原任务。命令返回当前 revision、conditions 记录及 current_constraints/work_partition；同一 condition_id 不重复入账，结果不明先读原记录。仅文件从缺失变为存在或时间过去，不自动解除原责任的条件判断。

## 实际满足后解除该条件

原责任先读真实返回，判断它确实消除哪一条件，写已取得的有限 JSON 报告，再调用 `resolve-condition`。报告必须绑定当前 task_id/request_id/input_revision/task_input_sha256、当前 step_id、condition_id/condition_kind、原 condition 的 condition_sha256、记录内 owner_ref、当前 file_scope 的 scope_sha256，且 kind=condition_satisfaction、result=satisfied，带源 observed_at 和非空 evidence_refs。owner 未声明时保持 null，不编造身份；owner 标签、引用和 JSON 本身不认证来源或替代原责任的语义核收。

condition_sha256 由原 open 记录按现有 task encoded/digest 规则取得；scope_sha256 同样对应当前原任务/步骤的 file_scope（实际工作区及对象身份）。输入或目标改变后，旧报告不能解除新上下文的条件；仍适用的原条件继续保留，原责任基于新上下文报告准确满足结果。报告的路径与 sha256 锁定已读字节，沿已有有界只读 FD 入口核对，保留准确拒绝及 peer UNKNOWN，不通过被拒对象取证。

```json
{"operation":"resolve-condition","task_id":"original-task","expected_revision":5,"step":"a","condition_id":"input-ready","path":"evidence/condition-satisfied.json","sha256":"<报告的sha256>","max_age_seconds":600}
```

max_age_seconds 是本次选择的源观察新鲜度预算，不能以报告重读时刻刷新源事实。操作在原 CAS/索引锁和现有部分提交语义下完成：读回准确报告，核当前对象未在取证中变化，只将对应普通条件标记 resolved，保留原记录及满足出处，然后刷新原步骤/依赖。不调用模型、不执行领域效果、不重置 runs、不清 restrictions。其它未满足条件、用户停止、真正拒绝和 UNKNOWN 继续保留；已有 verified 效果只在同输入、同当前对象观察仍适用时复用，变更则交原责任核查，不能重放已完成效果。

同报告的已提交结果可在当前 CAS 下重用，不再产生一次领域动作。CAS 冲突先读原记录；保存任务后索引失败仍返回 task_partial_commit，先核 committed_revision 与实际状态，再按原 sync-effects 恢复索引，不能把报错当未执行重放。普通条件解除成功后，模型在真实回合继续相应获准动作；接口不提供后台机会或自动重试授权。

## 旧记录与边界

旧的 external_condition restriction 没有分类，可能来自普通等待或明确取消；本接口不猜测、不迁移、不自动清除，输出 legacy_unclassified_stop。它不在新 conditions 集合中，不能用 resolve-condition 解除。原责任须先核原来源和允许的准确处置；不可通过新任务、amend、改路径、索引清空或新条件 ID 绕过。真实原拒绝及未决占用在本修复中保持原样。

未选新条件功能的既有记录保持原格式字段。选择后使用同一 format=1 的可选字段；不支持该字段的旧程序应拒绝而不改写，不要求普通会话迁移。核心的来源真实性、实际权限、当前完整在途、原责任是否正确判断“已满足”及最终业务效果仍须真实消费者核验；程序校验只证明对应有限关系和原件字节。
