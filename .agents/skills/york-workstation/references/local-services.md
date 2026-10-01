# 本机服务操作入口

## 已有 Docker 与 Compose

从当前设备工作站入口定位已有工具，`$HOME/docker/mi-local` 仅作为发现线索；其 Compose、`dbctl`、配置、命名卷和运行记录继续作为维护入口。服务种类、版本、容量与连接读取实际工具目录的 `README.md` 及相应配置；不把一次运行值复制成新工作站默认。

先用 `command -v docker`、`docker context show` 和所选 context 的 endpoint 核对实际执行位置。已有 Docker Desktop 本机 socket 是发现线索；当前 context 可能被切到远程。再读取 Compose 项目标签、容器、端口及挂载，区分本地 daemon、远程 daemon、named volume 与宿主机业务目录。不要将 `docker inspect` 中完整环境变量或认证配置输出到聊天。

当前工具的 `guard_local` 检查项目、运行状态和端口绑定，但不能单独证明 Docker daemon 位于本机。远程 context 中同名容器不属于本机服务授权；先定位实际本机入口，避免修改共享默认 context 影响其他任务。

从工具目录调用已有命令：

| 命令 | 适用操作及副作用 |
|---|---|
| `./dbctl status` | 读取 Compose 服务状态。 |
| `./dbctl logs <服务>` | 读取指定服务日志，按本次故障限制结果量。 |
| `./dbctl capacity` | 读取本地库统计、卷占用及当前容量策略。 |
| `./dbctl report` | 读取已有同步运行快照，不代表当前服务或数据已验证。 |
| `./dbctl up` | 启动已有 Compose 环境并执行本地初始化和就绪检查；已可用时无需重复启动。 |
| `./dbctl restart` / `./dbctl stop` | 改变共享服务运行状态，先核对使用者与恢复影响。 |
| `./dbctl test` | 会写入、恢复和清理本地夹具，不能当作只读健康检查。 |

`restart` 会先重启传入的服务，再调用整个环境的 `up` 与就绪检查；即使传了单服务，也可能启动其他已停止服务。需要严格局部维护时查看当前 Compose 依赖并选择相应工具操作，不把它当作单服务无关动作。

`dbctl` 自带 `.venv`，优先复用已有依赖。其 `sync --help` 被外层解析器接管；同步细项帮助实际使用 `.venv/bin/python scripts/sync.py --help`。数据选择、覆盖和续跑使用 [dbctl 数据操作](../../york-local-data/references/dbctl-operations.md)，不为服务启动自动同步业务数据。

## 容量与故障

服务启动失败先从指定服务日志定位端口占用、配置、兼容性、卷或空间问题；识别占用者，避免关闭无关进程来抢端口。读取当前 `config/capacity.json` 和 `./dbctl capacity` 判断是否受同步预算或主机空间影响。

现有同步预算包含未选中的历史对象；不能通过换库、并行同步或删除运行锁绕过。同步停止线由工具执行，不是卷文件系统配额，业务应用直接写入也不会受到实时限制。镜像、引擎日志和业务记录分别判断；容量数值以当前配置为准，不自动定期删除数据。

停止或重建容器不等于删除其命名卷。维护前确认 Compose 所用卷和 bind mount，按 [生命周期](resource-lifecycle.md) 处理实际共享消费者与恢复。修复后分别核对服务状态、真实客户端连接和所需功能；端口可达与容器健康不代表数据同步或业务测试成功。
