# 普通文件元数据观察适配器 0.1.0

本适配器按节点明确选择的普通文件生成一次 `node-asset-facts-v1` 观察，供核心 0.3.1 的 `workstation-entry` 读取。源码随共享框架维护；配置、来源文件和观察输出由节点维护，不进入源码仓。它沿用可选节点适配器方式，不加入核心受管运行包的 13 模块，也不自行建立服务或计划。

从已核对版本的正常源码检出运行：

```sh
python3 -B <source>/adapters/file-metadata/asset_facts.py --selection <node-selection.json> --output <node-observation.json>
codex-framework workstation-entry
```

第一条只读取明确选择的文件并原子替换指定输出；输出父目录必须已经存在，不能是符号链接。第二条只读节点既有 `${CODEX_HOME:-$HOME/.codex}/workstation/observation-sources.json`。配置和观察文件的采用由原节点维护入口完成，核心安装不会自动复制或执行适配器。独立部署适配器时保留所属源码版本及文件校验值。

选择文件使用 `schema: node-asset-selection-v1`，包含 `identity`、`sources`、`assets`。`identity` 明确 `project_id/task_ref/node_id/version`。`sources` 按来源 ID 声明绝对 `path`、`allowed_root` 与 `kind`（`json/file/constant/link/stat`）；`constant` 还需明确常量名。每项资产有 `id/version_label/owner_ref/stages`，八阶段为 `candidate/installed/loaded/selection/reserved/effect/health/retirement`，各自保留独立值和来源。阶段取值方式为 `literal/field/matches/bindings/normal_entry`；字段名和相关比较必须来自节点实际原件，不能用版本名推定已加载或业务成功。完整可执行形状由源码及持续回归测试维护，不提供可直接套用到真实节点的默认配置。

节点来源清单沿用 `workstation-entry-v1`。资产来源增加 `kind: asset-facts-v1`，并声明输出路径、上述四项准确 `identity`、`status_path: status`、`time_path: observed_at`、明确时效及失败值。观察文件必须处在该节点工作站目录内；入口不递归找文件。普通 v1 来源无需这些扩展，输出保持兼容。

可选 `historical_sources` 最多一项，必须另带 `scope_status: COMPLETED_FINITE_SCOPE` 和非空 `scope_ref`。这只是调用方提供的历史范围注记，输出明确 `scope_annotation_authenticated: false`；原历史故障仍保留，不能用该注记认证完成、停止授权或运行健康。历史配置错误进入局部 `historical_errors`，不抹掉有效资产或活动故障。

元数据当前不证明运行健康。产物始终标记 `runtime_health_assessed: false`，健康阶段只允许 `UNKNOWN/NOT_ASSESSED`。保存的加载时间保留原时间，不在观察时刷新；失配或缺件只影响相关资产。`workstation-entry` 对同 ID 不同行使用 `source_index` 保留 `active_observations`，活动失败优先呈现，投影错误不能洗掉原故障。

producer 成功写入退出 0，报告 `METADATA_PUBLISHED_RUNTIME_UNVERIFIED`；失败退出 2，报告 `OBSERVATION_FAILED`，原输出保留。原输出保留不代表本次观察成功，读取方仍核原件身份及时间。核心入口仅状态 `normal` 退出 0，`unknown/abnormal` 退出 2；只有元数据通常应为 `unknown`。本适配器不探测应用、协议、服务、Hook 或业务效果；实际节点健康与正常启动注入另行验证。
