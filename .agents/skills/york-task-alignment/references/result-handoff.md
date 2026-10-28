# 原结果交接

交接服务已有请求和原责任核收，不要求所有任务采用统一报告或 JSON final。先使用原业务记录、原生请求身份及结果引用；仅有真实消费者需要时使用本仓 `scripts/review_handoff.py/js`。库按调用者确切审核版本加载，不在标准 CLI 运行包内，不提供调度、发送权限或平台拦截。

## 读取原结果，final 说明结果

原调用者保留已有请求及当时输入；结果仍写原报告。可选 Python `review_source(prompt, report_ref, report_readback, request_pointer=..., result_pointer=..., evidence_readbacks=...)` 从原节点报告读回产生 `{request, report}` 定位对象，report 保存原引用、指纹和指向原 request/result 的 JSON pointers，不复制报告正文或完整 result。它不写新台账、不自动沿路径读证据，也不发送任何内容。

接收方从原结果记录取得这份定位对象，作为 `io.source` 传给 `readReview(dispatches, selectedRequest, {source, readNative, readReport})`。机器先从原生结果核对原委托的 sender/target、完整输入、唯一 turn 与 final item，再读取 source 指向的原报告和明确证据。实际答案从报告取得，核对请求、输入对象、指纹和逐项问题；final 可以使用正常说明文字，无需重复机器结果。source 的字段不认证作者或授予任意文件读取权，`readReport` 必须由调用者按已授权对象限定范围。

原生读取支持已核的 codex_app schemaVersion 1 及其已见委托输入。cloud_threads 投影缺少原始委托或原生 final 身份时继续 UNKNOWN；不能把助手 role、latestTurn 或自报字段伪造成所缺身份。分页由调用者沿提供方原游标有界读取；缺页、截断、冲突或未知结构不签成功，也不视为业务失败。只适配实际见过且已核实的提供方字段，专用节点格式适配归原工作站。

`readReview` 返回本次所需结果和原生引用，不回传整份 native/tool/report/evidence 原文。调用者在输出到模型或跨聊天前仍按[数据流边界](../../york-workstation/references/environment-sync.md#读取模型上下文交接与发布)选择允许内容；库不会自动识别所有私密文本。受保护正文在原节点处理，只有获准的最小结论进入模型上下文。

## 兼容、核收和恢复

旧 `task-runtime` 已归档退出当前命令入口和安装包；历史 Task Store/effects、原结果及已批准版本包保留原位。恢复先读原身份和效果，仍有效义务由现有原生任务与原责任承接，不恢复旧执行链、不迁改原拒绝或 UNKNOWN。此处的请求格式兼容属于结果读取，不能作为旧执行器入口。

已发出的 `review-return-v1` 可继续使用 `produce_review` 与无 source 的旧读取方式核对，严格旧格式不迁改历史请求；有限结果回执也只保留原请求恢复。新请求不借此预填未知结果，不从 final 反造请求。来源缺失保留 UNKNOWN；定位冲突拒收，不自动重发或改目标。

`receiveReview` 只做关系绑定，不自动实质 accepted。`decideReview` 由原责任根据真实证据决定，未答/limited/blocked/not_run/unknown 仍为 partial。修订只替代明确指定的同发送者原请求；其他请求、有效旧结果、拒绝及恢复责任继续保留。旧输入的结果不能满足新义务。

在真实宿主回合调用 `continueReview` 时，先把消费意图保留在原记录，再交原消费者一次；丢回执或结果未知则 `inspectConsumption` 核原效果，未核清不重复消费。原生完成、收件、责任核收、阶段退出及后继实际开始分别判断。核收后在原授权内执行合法后继；自然调度只由既有自然触发及实际结果消费证明，不能由本库或合成测试代签。
