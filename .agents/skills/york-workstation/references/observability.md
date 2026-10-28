# 工作站异常与可溯源性

核心框架定义跨项目的模型职责、判断语义与边界；每个节点提供实际来源，业务仓解释业务专用状态。复用 Codex 原生任务及工具记录、现有节点状态和系统日志，不读取私有 session 存储，不复制完整敏感载荷，也不建立第二套任务历史。工作站健康是与配置发现分开的当前事实，服务于任务执行与有效资源利用；配置缓存的 24 小时有效期不是健康记录期限。异常/未知不自动构成所有动作的门禁：新增负载按容量风险判断，已核实安全的释放资源按其具体副作用执行，不等待全局健康。

## 模型在何时读取

新建或恢复相关任务时先用已收到的 `SessionStart` 摘要；未收到则运行一次 `codex-framework workstation-entry`，它只读选定 Codex 安装目录内的 `workstation/observation-sources.json`。沿实际安装入口绑定的目录或显式 `--codex-home` 取值，不把另一机器的 home 或平台 `CODEX_HOME` 当作已确认的框架安装位置。若清单缺失或结果未知，保留未知和待处理点，不递归枚举工作站寻找替代清单，也不打断无关工作。具体异常与当前任务有关时，先确定原生任务、项目、节点和采用版本，再按清单给出的精确路径及运行 ID 读取必要原件。准备会改变共享资源的动作、动作结果不明、故障反馈和修复复测时，同样从受支持的原生任务/工具记录与定向节点来源核对。用 `list_threads` / `read_thread` 或当前任务的原生工具记录追溯原目标、操作和结果；系统日志按对象、时间、尺寸限界读取。不能仅用一次检查通过、命令退出 0、容器当前 healthy 或清理声明推定资源稳定、业务效果或实际退出。

清单缺失、来源无法生成或观察过程失效，属于需要定位和修复的工作站问题；已在当前维护授权内时，补齐实际资源管理入口的生产与接续，再以正常操作、来源写入、核心读取和原任务继续执行核验。不能只补一份手填健康文件、登记未知或等待后续回合。来源生产由项目或节点已有工具承接，其专用实现、操作副作用及有效故障回归从该工具入口发现；核心只读取下述通用契约，不携带具体服务、端口或项目清单，也不为单个节点新增抽象层。生产者应保留原动作失败，并使观察自身失败可见；开始新观察时撤下旧正常声明，无法撤下也需报告失败，不能沿用旧健康。实际配置、连接和结果留在节点。

`python3 scripts/codex_framework.py workstation-check < "$CHECK_INPUT"` 是无持久状态的本地核对入口；`CHECK_INPUT` 指当前任务在可用临时目录生成的输入文件。输入只放本次需要的来源路径和判断范围；不得长期保存含原生响应或敏感内容的输入。结果为 `normal`、`abnormal`、`unknown`；仅 `normal` 退出 0，异常或未知退出 2，输入无效退出 1。检查器最多读 32 个普通 JSON 文件，每个不超过 64 KiB，不扫描目录、不启动服务、不执行 Docker 命令、不写节点状态。来源需包含项目、原生线程、节点、采用版本、带时区的观察时间；不具备这些字段的旧记录可作为故障线索，但不能证明健康。调用者仍须核实文件身份、来源完整性和状态的业务语义；检查器不能认证任意输入，也不能自行唤醒模型。

若当前节点已安装并信任 `SessionStart` Hook，它在 `startup|resume|clear` 时使用同一固定入口，将有界状态摘要注入模型上下文。节点清单只指定选定安装目录的 `workstation/` 内已有的普通 JSON 状态文件、状态值与时效；可用 `follow` 沿安全运行 ID 读取一份最新运行原件。目录越界和符号链接不作为自动摘要来源。状态值的业务解释、路径和新鲜度由节点配置，不写死在核心。清单缺失、文件损坏、观察入口失败、正常状态过期或身份不匹配均显示“未知”；已知失败保留“异常”线索，即使旧记录缺身份或已过期，其归属仍待原件核实。健康需来源同时匹配项目、原生任务、节点、采用版本及新鲜度。每次最多读 1 份 8 KiB 清单及 8 个来源（每个最多一份后续记录），合计最多 17 个文件和 64 KiB，只读且不联网、不启动模型或服务。摘要只代表已配置来源，不证明整机或未登记资源健康；具体版本、操作和资源后置条件仍按原件及下述即时入口核实。

显式入口输出 `status`、短摘要、读取次数与字节数；仅配置内已核实的新鲜健康状态退出 0，异常或未知退出 2。异常或未知不能因单次命令退出码、日志上限、CLI 版本不符而被丢弃：保留原失败及恢复点，查明原因后在实质修正版本上定向复测；外部阻断只暂停依赖步骤，未验收不标完成。不靠提高日志上限弥补宽范围扫描。

节点清单示例先解析本机选定目录，再输出 JSON；不会写已有清单或状态。将本段保存为临时脚本时可传 `--codex-home`；未传时使用 `CODEX_HOME`，再回退当前用户的 `.codex`。平台目录与实际框架安装位置不同时必须传已核对的安装目录。项目、任务、节点、版本和状态值仍须换成当前来源的实际契约。

```python
import argparse
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))
args = parser.parse_args()
codex_home = Path(args.codex_home).expanduser()
if not codex_home.is_absolute() or not codex_home.is_dir():
    parser.error("codex-home must be an existing absolute installation directory")
root = codex_home.resolve() / "workstation"
manifest = {"schema": "workstation-entry-v1", "node_id": "node-name", "sources": [
    {"id": "project-service", "path": str(root / "project" / "state.json"),
     "status_path": "status", "time_path": "observed_at", "max_age_seconds": 900,
     "ok_values": ["HEALTHY"], "failure_values": ["FAILED"],
     "identity": {"project_id": "project", "task_ref": "codex:thread:original-id", "version": "commit-sha"},
     "follow": {"id_path": "last_run", "directory": str(root / "project" / "runs"),
                "status_path": "status", "time_path": "finished_at", "max_age_seconds": 900,
                "ok_values": ["PASSED"], "failure_values": ["FAILED"]}}
]}
print(json.dumps(manifest))
```

底层清单要求已解析的绝对路径，且仅接受选定 `workstation/` 内的普通文件；它不会展开 `$HOME`、环境变量或模板占位符。上例通过本机解析满足同一接口，不放宽路径范围，也不把共享文档中的示例目录当作真实节点配置。

来源自身须包含与清单相同的 `project_id`、`task_ref`、`node_id` 和 `version`；字段嵌套时由 `identity_paths` 指向实际字段，`follow.identity_paths` 可指定后续记录的字段。旧来源缺这些字段时仍能报告故障线索，却不能证明健康。这是任务入口的**启动或恢复时感知**：仍须在正常 Codex 启动中确认 Hook 已获信任且摘要确实进入模型、模型按原任务处理。任务空闲时即时唤醒模型不由此实现，也不应把手动运行 handler 当作端到端验收。

例如从节点已有状态入口得到绑定后的清单，将下段保存为临时脚本并传入该清单文件名。它复用当前节点已经解析的来源路径与身份，输出可供 `workstation-check` 读取的短时 JSON，不复制另一机器的路径：

```python
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("manifest", type=Path)
args = parser.parse_args()
manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
source = manifest["sources"][0]
scope = dict(source["identity"], node_id=manifest["node_id"])
check = {"schema": "workstation-check-v1", "scope": scope,
         "sources": [{key: source[key] for key in ("id", "path", "status_path", "max_age_seconds", "ok_values")}]}
print(json.dumps(check))
```

默认字段为 `project_id`、`task_ref`、`node_id`、`version`、`observed_at`；已有源字段不同时可分别用 `project_id_path`、`task_ref_path`、`node_id_path`、`version_path`、`observed_at_path` 指向嵌套 JSON 字段。新鲜度从源内观察时间算起；只打开文件或重新执行读取不延长时效。读取失败、缺失、超期、身份/版本不符、节点失联、观察程序停止、未知状态和磁盘写失败都不能得到 `normal`。已绑定来源报告故障时，即使后续过期仍保留异常线索，直到有新证据证实处理。

资源清理另给 `resource_claims`，每条包含 `effect_key`、`source_id`、`resource_id`、`expected_state`。来源必须是新鲜且身份匹配的完整资源快照（`complete: true`，`resources` 列表含准确 ID 和状态）；完整快照中缺席才可支持 `absent`。清理命令返回或缺少对象的局部查询都不够。模型还应核对操作请求、工具返回、业务后置条件和真实资源退出分别属于哪个任务与版本；结果不明先查原效果键，不重做副作用。具体容器、卷、进程等的发现、清理和恢复仍归实际业务节点实现。

## 保留与资源约束

优先引用现有轮转系统日志与原生记录。节点如确需补充少量摘要，候选边界为原子状态文件不超过 8 KiB、变化事件最多 14 天或 16 MiB；未解决故障只保留必要索引和原件位置，由节点按复现价值与实际容量设上限。不得把采样、留存或核对本身变成内存风险。启用持续观察前，在隔离输入上实测单次 CPU、峰值 RSS、I/O 和异常时写入失败路径；全工作站预算要包含共享宿主或 VM、已有服务、临时实例和并发，不以单容器限制替代总预算。

本地先固定故障、观察自停、过期/缺失、读失败、失联、错误归属、版本未采用、清理残留、重复效果、受限资源和健康路径的输入与预期，再手动经正常入口复测。离线通过只证明框架解释能力；实际 Hook 注入、异常触发进入模型处理、用户通知及任务所需资源效果需分别有相应证据；不把长期稳定作为可重建工作站的默认验收目标。用户启动的 App 调度所带来的真实回合就是模型执行机会，模型在其中处理原目标及可做工作；不能以本观察器不提供空闲唤醒解释回合内的漏执行。用户未启动或主动停调度与节点失联、观察程序意外退出分别判断，暂停不清除已有异常和未完成目标。实际没有回合或节点离线时不能承诺即时感知；不以模型自行启停、自动删计划、父会话复活或另建后台巡检作为本方法前提。
