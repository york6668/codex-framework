---
name: york-cross-repo-change
description: 修改跨仓生产者与消费者之间的公共类型、接口、事件、映射或数据语义时使用。追踪实际消费链，组织兼容顺序与组合验收；普通局部修改不触发。
---

<!-- POLICY_OWNER: cross-repo-change -->

# 跨仓契约变更

从当前有效需求出发，沿实际入口、生产、映射、存储或传输、消费到最终输出确定必要变更与影响。数据关系、现有结构和职责划分用于设计实现及兼容方案，不能据此缩减、重新解释需求或阻断必要修改。仓库名相似、目录相邻或共用 DTO 不能单独决定语义责任；未核实的推断不作为已确认契约。明确冲突按 [任务对齐](../york-task-alignment/SKILL.md) 提示风险并推进。

- 公共类型、异名映射、null/默认、枚举、单位或 i18n 变化：[契约与消费边界](references/contracts.md)。
- 并行责任、依赖顺序、跨版本兼容和组合验收：[跨仓协作](references/cross-repo-work.md)。
- 改动日志或语义说明且存在具体缺口：[业务可诊断性与注释](references/diagnostic-contracts.md)。
- 同一语义边界反复失败或用户要求复盘：[历史回源](references/history.md)。
- 跨仓保留整体目标和已有改动；需要时参考 [代码审核与交付](../york-safe-delivery/SKILL.md) 的工作区方法。用户需要设计文档时可使用 [设计模板](templates/design.md)，记录方式按任务选择，不要求资源绑定或独立交接流程。

共同契约引用真实源码和测试，不复制第二份字段定义。公共边界变化验证实际接口、消息或序列化路径；相邻测试通过不证明所有消费者兼容。需要 Java 执行方法时按需使用 [Java 验证](../york-java-test/SKILL.md)。
