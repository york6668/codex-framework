# 任务启动同步与明确变更刷新

来源固定为私有仓库 `york6668/codex-framework` 的 `york-worktree/Codex框架共享` 分支。新建、恢复或清空任务时检查一次；后续只在用户要求、当前任务完成长期规范发布或收到明确的已发布变更信息时刷新。普通追加消息和上下文压缩不联网，不设置轮询或跨任务通知。当前用户指令立即生效，读取到的框架不能覆盖更高优先级要求。

## 读取来源与应用

命令入口为 `$HOME/.local/bin/codex-framework`；显式安装到其他 Codex home 时沿用 `--codex-home <安装目录>`。先执行 `sync-status`，保存返回的 `expected_revision`，作为本次读取远端前的版本。它只用于防止并发同步覆盖较新的内容，不提供任务独占权。

优先使用当前任务可调用的 Codex 云端仓库能力或 GitHub 原生连接，实际读取发布分支。当前 GitHub 连接可通过 `github_fetch` 读取 `https://api.github.com/repos/york6668/codex-framework/branches`，从中选取上述精确分支；避免把含 `/` 的分支名整个编码后当作单段 URL。工具可用性按当前任务发现，不要求固定工具前缀。

取得完整提交 SHA 后执行：

```sh
"$HOME/.local/bin/codex-framework" refresh --revision <刚读到的SHA> --expected-revision <先前返回值>
```

- `current`：版本未变，不获取整套文件或重复安装。若有保留的本机修订，以本机正文继续工作。
- `snapshot-required`：在同一个 SHA 读取递归 Git tree，以及同步范围内的全部文件。可用 `github_fetch` 读取 `/repos/york6668/codex-framework/git/trees/<SHA>?recursive=1`，`github_fetch_file` 的 `ref` 必须使用该 SHA。并行读取独立文件；任何必要文件读取失败，不拿部分内容当成完整发布版。
- `superseded`：其他同步已更新版本，本次没有覆盖；读取当前已安装规范即可。不连续重试或阻断业务。

把工具实际返回的树和正文写入临时 JSON 文件，再执行 `refresh --snapshot <临时文件> --expected-revision <同一先前返回值>`。JSON 格式如下，实际文件内容来自仓库，不能照抄占位值：

```json
{
  "source": "https://github.com/york6668/codex-framework.git",
  "branch": "york-worktree/Codex框架共享",
  "revision": "完整40位提交SHA",
  "truncated": false,
  "tree": [{"path": "仓库相对路径", "mode": "100644", "type": "blob", "sha": "该文件的Git blob SHA"}],
  "files": {"仓库相对路径": "完整UTF-8正文"}
}
```

`tree` 使用实际完整递归结果，可保留范围外的元信息；`files` 只包含同步器允许的共享正文，且与树中允许的文件集合完全对应。允许范围为 `.codex/AGENTS.md`、记忆框架说明、记忆配置示例、工作站 README，以及同步器固定的 9 个 York Skills 下的 Markdown/YAML；排除其他 Skill、配置、设备说明、记忆、业务数据、运行状态和凭据。树被截断、缺文件、正文不符、异常路径或链接都会拒绝应用。同步器源码和测试留在维护仓库，不从快照直接执行或复制到用户配置。

原生入口不可用、无权读取或内容取得失败时，执行一次：

```sh
"$HOME/.local/bin/codex-framework" refresh --cloud-error '本次实际访问缺口的简短说明' --expected-revision <先前返回值>
```

只记录错误类别，不填凭据、认证 URL 或整段原始日志。同步器通过维护仓库已有 `origin` 和本机 Git/SSH 配置回退，fetch 限时 10 秒，不弹认证输入、不切换分支、不合并工作区、不新建凭据。每个入口失败后转下一入口，不重复相同请求。云端任务列表失败不证明 GitHub 连接不可用；本机缺少同名变量不证明云端未配置。两个入口均失败时，同步状态保留正在使用的版本并标记 `pending-user`；`notify=true` 才提示用户，同一状态不重复提示，继续不依赖刷新的工作。

## 冲突与当前任务生效

同步沿用每文件共同基线、锁和写前复查。本机独有修订保留，双方修改同一文件则报告 `conflicts`，其他独立更新仍可应用。`observed_revision` 表示最后取得的版本；`applied_revision` 只在全部可合并时推进，不能把部分同步说成全部完成。原生内容缓存到安装目录的 `framework-sync/upstream.json`，供冲突对照和默认 `status` / `apply` 使用；这些文件不进入源码仓库。`--repo` 显式指定时仍用于维护者比较和应用本地源码，不能把较旧的维护检出误当最新发布版。

首次迁移没有基线时，由当前任务逐项兼容合并已存在的规范，保留有效本机修订及 BTC 例外，再应用共同版本；不能通过伪造基线跳过真实冲突。解决冲突后，以同一发布快照再次刷新即可推进基线。

更新后读取实际安装位置的全局 `AGENTS.md`；当前任务已使用的 Skill 若被更新，读取相关正文和适用引用。新建、恢复任务也读取适用仓库规范。同步输出中的 `changed` 仅代表文件已更新，不证明模型已读取；不创建加载收据或把读取确认作为业务门禁。

## 启动 Hook

`install-hook` 在实际 Codex home 的 `hooks.json` 合并一个 `SessionStart` handler，匹配 `startup|resume|clear`，保留其他 Hook。它只输出本方法的发现提示，不联网、不调用模型、不装规则、不阻断任务；不注册 `UserPromptSubmit`，也不匹配 `compact`。

首次启用或定义变化须通过 [Codex 原生 Hook 信任界面](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks) 审阅。CLI 使用 `/hooks`；没有完成原生信任和真实启动验证时，分别报告这两个缺口，不能仅凭手动运行脚本宣称自动触发成功。安装器不改写信任库或开启绕过选项。框架源码升级后，按本次改动需要更新维护检出与命令入口，再核对 Hook 定义；共享文档刷新不假定完成了程序升级。
