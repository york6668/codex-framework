---
name: york-java-test
description: 使用本机 Maven、JDK、Surefire、JUnit 和本地制品执行针对性 Java 验证。需要选择能覆盖实际错误的测试边界、处理跳过测试或核对真实依赖行为时使用。
---

<!-- POLICY_OWNER: java-test -->

# Java 本机验证

通用的测试边界选择、原失败反馈、独立预期值及生产者到消费者断言由 [通用验证方法](../york-engineering/references/verification.md) 承担；需要时读取，不再为其他语言要求加载 Java 工具。此处负责 Java 执行适配，复用本机现有入口，同批修改按模块和影响范围合并编译与测试。

- Maven/JDK 选择、实际本地仓库、跳过测试、JUnit Console 与结果回读：[Maven 测试入口](references/maven-testing.md)。
- SQL、事务、消息、并发，以及业务回放中的依赖与异步断言：[真实依赖断言](../york-engineering/references/dependency-testing.md)。

检查所选生命周期及测试实际连接，避免测试顺带发布制品或调用远程业务。常规入口受阻时使用有真实断言的更小入口，说明原路径和替代路径的覆盖。

回读实际目标、内容版本、通过/失败/跳过数量；编译、服务健康、退出码和零用例都不代替行为通过。临时执行配置恢复后核对最终差异，不回退用户修改。本地结果与环境验收分别报告。
