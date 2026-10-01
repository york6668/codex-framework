# Maven 与 Surefire 测试入口

## 找到本机已有入口

从当前 checkout 的 pom、profiles、settings 和既有命令确认模块、JDK、生命周期及跳过选项。先复用本机工具，不因系统默认 Java 与仓库要求不同就重装工具链。

- 先用仓库 Maven Wrapper（若有）、`command -v mvn`、`java -version` 与 `JAVA_HOME` 发现实际工具；macOS 可用 `/usr/libexec/java_home -V` 查已安装 JDK，Linux 按实际安装与 PATH 核对。默认 Java 可能不是旧项目需要的版本，不把另一设备的安装路径作为前置。
- 先看仓库是否指定 `-s`、`-Dmaven.repo.local` 或 `.mvn` 设置，再看用户与 Maven settings；不要默认依赖都在 `~/.m2/repository`。从当前 `$HOME/.m2/settings.xml` 或显式指定的 settings 读取实际值，只提取所需信息，不打印 server 凭据。
- common 等本地生产者源码更新后，消费者可能仍加载旧 JAR。根据消费者实际 classpath 核对制品；确需本地安装时使用同一有效本地仓库，并核验产物与当前源码。源码存在新方法或任意目录 install 成功都不能证明消费者已使用它。
- 需要远程依赖时看实际访问目标；专用网络或网络失败按 [本地网络核验](../../york-workstation/references/local-network.md) 处理。已缓存且离线可用的依赖不增加 VPN 前置，也不通过制品发布解决本地测试依赖。

## 优先执行现有目标测试

读取当前 Surefire 配置及其属性引用，再决定命令行覆盖。若配置为 `<skipTests>${surefire.skipTests}</skipTests>`，可以使用 `-Dsurefire.skipTests=false`；其他属性按当前 POM 使用，不假定 `-DskipTests=false` 能覆盖硬编码的 `<skipTests>true</skipTests>`。

目标模块和类由当前测试定位，例如在目标模块目录执行：

```sh
mvn -Dsurefire.skipTests=false -Dtest=<实际测试类> test
```

该例仅适用于 POM 确实使用此属性的模块；先选与仓库匹配的 JDK，再检查测试输出和报告中的实际类与计数。`test-compile` 是合法编译阶段，`testCompile` 不是 Maven 生命周期命令；任何编译命令都不能代替测试执行。

## 常规路径跳过或被无关编译阻塞

已有更小测试入口优先。可先编译需要的代码，生成目标模块的测试 classpath，再用已有 JUnit Console 执行选中的类或方法：

```sh
mvn -DskipTests test-compile
mvn dependency:build-classpath -DincludeScope=test -Dmdep.outputFile=<本次 classpath 文件>
java -jar <已核验 Console JAR> --class-path <当前编译输出及测试依赖> \
  --select-class <实际测试类> --fail-if-no-tests --reports-dir <本次报告目录>
```

- 从当前实际 Maven 本地仓库的 `org/junit/platform/junit-platform-console-standalone/` 选择已有版本，再运行其 `--help`。例如 1.7.0 使用上面的直接选项，不支持较新版的 `execute` 子命令，不能照搬另一版本命令。
- classpath 包含本次 `target/classes`、`target/test-classes` 与实际测试依赖，避免旧 class 文件或重复版本遮蔽。用 `--select-class` 或 `--select-method` 限定目标；`--fail-if-no-tests` 防止零测试误报成功。
- 如果整个 `test-compile` 被无关测试阻塞，可根据实际依赖单独编译目标测试到任务临时输出，再使用同一套真实断言。分别报告常规路径的阻塞和替代路径覆盖，不把目标测试通过说成全套通过。
- 确需临时调整 Surefire 时先保留原内容，测试后恢复并核对最终 diff；临时调整只改变执行入口，不改变业务配置、断言预期或冻结的失败测试。

## 回读结果

核对测试报告中的实际目标、通过/失败/跳过数量和当前内容版本。`BUILD SUCCESS`、编译、`Tests are skipped` 或零用例不能证明行为通过。若后续要执行 clean，将需要保留的证据放到不会被该 clean 删除的位置。

明显耗时的多仓或全量验证先说明必要性；依赖解析失败时保留具体缺口，继续不依赖该环境的有效验证。选择的生命周期服务于测试，不推定发布制品、部署或环境验收。
