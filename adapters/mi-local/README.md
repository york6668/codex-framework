# mi-local 节点观察适配器

这是已有 mi-local 数据工具的可选适配器，向核心 `workstation-entry` 提供真实观察。核心读取器不依赖 Docker 或这三个数据库；其他节点沿用自己的资源管理入口。

`observation.py` 及其回归源码由本 GitHub 仓库持有。实际 `common.py`、连接配置、数据库、清单和观察结果留在节点。适配器使用现有 `common.ROOT` 与 `config("local")`，不另建认证渠道：仅探测 Compose 项目 `mi-local` 的三个同名容器与 localhost 标准端口，核对容器身份/健康以及 MySQL `SELECT 1`、Mongo primary、Redis `PING`。既有环境不同则先按实际部署调整适配器，不能把该约定当作所有工作站的资源清单。

## 接入既有工具

使用节点已有虚拟环境（`pymysql`、`pymongo`、`redis`）和 `common.py`。把已发布的 `observation.py` 部署到资源管理工具的 `scripts/`。`dbctl.py` 保留参数解析，将原动作实现放入 `execute(action, remaining)`，入口通过实际适配器执行：

```python
from observation import run_action
return run_action(a.action, remaining, execute)
```

动作枚举增加 `observe`；`up`、`stop`、`restart`、`status`、`sync` 无论成功或失败都会刷新观察。原动作失败码和异常继续返回；原动作成功但观察失败返回 2。`observe` 只探测，不启停服务。节点必须有 macOS `scutil`、当前原生任务提供的 `CODEX_THREAD_ID`，版本由适配器与实际 `dbctl.py` 内容计算。不要编造任务身份或将宿主身份替换为随 VPN 变化的主机名。

固定输出为 Codex home 的 `workstation/observation-sources.json` 与 `workstation/mi-local/observation.json`；合并保留其他已登记来源。开始探测前先撤下旧正常声明，只保留一份有界 `previous-observation.json`。成功撤下后，身份发现、首次写入或探测失败由读取器显示 UNKNOWN，不沿用旧健康；状态最多 8 KiB、权限 0600、时效 15 分钟。若目录不可写或撤下旧文件本身失败，生产者返回 UNKNOWN/2，但旧来源仍可能可读；必须结合本次失败处理权限/存储问题，不能只凭旧来源判正常。凭据、异常消息、Docker 环境和挂载不写入观察文件。状态只说明三个已配置服务的当前探测，不代表整机或业务测试通过。

## 验证与运行

在节点现有 Python 环境中运行，`PYTHONPATH` 指向既有 `scripts/` 供测试取得 `common`；测试先导入本目录的候选适配器，临时数据写入系统临时目录：

```sh
PYTHONPATH=<节点工具>/scripts <节点Python> -m unittest discover -s adapters/mi-local/tests -v
<节点工具>/dbctl status
<已安装核心入口> workstation-entry
```

真实正常入口必须刷新观察时间，后续核心读取应得到新状态。还需隔离验证原故障：身份发现失败、首次写入失败、协议探测失败不能留下可读的旧正常声明；恢复后重新执行正常入口。测试/状态文件不进入 Git。正式独立回归与发布部署按核心现有职责执行。

无常驻采集、调度或主动唤醒；超过 15 分钟未刷新会如实成为 UNKNOWN。下一次实际任务或资源操作再刷新，不能手改时间延长健康。清单缺失或适配器损坏应在所属工作站维护范围内修复并复测，不作为无关业务的全局阻塞条件。自然 SessionStart 的模型采用与手动入口结果分别判断。
