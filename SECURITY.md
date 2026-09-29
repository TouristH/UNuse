# Security Policy

> [!CAUTION]
> **废弃在线仓库：UNuse 已停止维护。** 本安全策略仅作为历史安全边界保留；请勿预期公开 Issue、
> 审核机器人或维护流程仍会得到处理。若存量内容存在可利用风险，请避免公开披露敏感细节。

## 报告问题

请不要在公开 Issue 中披露可直接利用的恶意插件、凭据泄漏或供应链漏洞。请通过仓库的
GitHub Security Advisory 私密报告，并附上插件 ID、版本、Release URL、SHA-256 和复现步骤。

一般的失效下载、兼容问题或作者主动下架可以使用 Remove/Yank Issue。

## 信任边界

- 插件与宿主客户端在同一进程运行，能力授权不是系统沙箱。
- `listed` 只表示元数据、固定下载、哈希、ZIP 结构和包内清单通过自动验证。
- `verified` 表示可信审核者审查了精确的 `pluginId + generation + version + SHA-256`，不是绝对安全保证。
- 未审核版本仍可被收录，但启动器会显示警告并在下载前要求用户确认。
- Topic 自动发现只接受 `io.github.<仓库所有者>.` 名空间；ID 或历史仓库冲突会停止自动收录，
  不会按“先提交者优先”覆盖现有身份。
- 中心同时固定 GitHub `repositoryId + ownerId`。两者不变的仓库改名可由机器人核验并把新 canonical
  URL 追加到本代 immutable history；old Release URL 仍可匹配旧 alias。ownerId/repositoryId 变化必须
  走人工 transfer。GitHub 原生 Transfer ownership 会保留 repositoryId 并改变 ownerId，不能静默成为
  同代转让；安全恢复是先转回已绑定 owner，再以不同 repositoryId 的独立目标仓库执行 lifecycle
  transfer。旧路径被其他 numeric 仓库重新占用时不会继承发布权，URL 本身也不是身份。
- 每次首次收录生成不可复用 UUID `lineageId`。管理员定向转让只推进 `generation`，不替换 lineage；
  旧代所有版本必须撤回，审核不跨代继承，启动器禁止跨代自动升级。
- v1 是冻结的旧合同，只含 active generation 1 且至少一个未撤回版本；它绝不接收新身份字段或
  generation 2。v2 才携带 publisher bindings、visibility 和完整撤回历史。

作者仓库的 `_manifest.json` 可以更新到新版本，但每个版本必须指向固定 GitHub Release ZIP。
中心仓库会保留历史版本并拒绝修改已记录版本；同版本包被替换时，启动器下载校验或维护者运行
`python tools/validate.py --check --verify-assets` 会发现不匹配。中心记录不会被覆盖，已有审核也不会
迁移到新字节。定时同步只严格验证本轮新增候选，不会反复下载全部历史 ZIP。

中心写入由仅安装在本仓库的 GitHub App 使用短期 installation token 创建机器人 PR；App 不得绕过
`main` 的可信策略和完整验证检查。插件清单与 ZIP 的处理阶段不持有长期个人 PAT。

## 应急处理

可信维护者可以：

1. 将受影响版本标记为 `yanked`，保留原因和历史记录；
2. 将对应审核记录改为 `status: revoked`，立即撤销绿色标志但保留 numeric actor 与命令顺序墓碑；
3. 在确认修复后要求作者使用新的 SemVer 版本和新的固定 Release ZIP 发布。

不得通过改写旧版本 URL 或哈希来“修复”已经发布的记录。

## 身份生命周期安全边界

- `retire` 与 `transfer` 同时要求 source owner 的 numeric identity 确认、可信管理员的 numeric user ID、
  当前 generation 和精确 source/target repositoryId。任何旧命令在代际推进后都会因 expected 值过期
  而失败。
- 已归档但仍公开的 source 仓库保留 numeric identity，允许退役或转让；target 仍必须未归档。已经
  删除、disabled 或 private 的 source 无法建立可信 API/确认链，必须先由原 owner 恢复并公开。
- 用户仓库确认必须来自 source `ownerId` 对应账号在同一 Issue 的更早精确评论；组织仓库必须在默认
  分支提供字段完全相等的历史生命周期确认文件，并由组织侧分支保护管理员审批。
- 事务把管理员 numeric ID、时间、Issue/comment、原因、作者确认和 source/target 数字 ID 写入
  identity ledger；登录名仅用于显示，不能单独授权。
- 所有版本撤回只会使商店发现层隐藏；完整 v2 历史继续向已安装用户提供撤回告警。文本 ID 和
  numeric publisher binding 不会因此释放。
- `purge` 只允许从未公开、main 无 active 指针、无任何审核墓碑的 staging 误收录。工作流绑定
  head SHA 后先关闭原 PR 防止 auto-merge，但直到受保护 lifecycle PR 真正合并才删除 staging 分支；
  失败或超时会保留分支供恢复。工具会扫描公开索引 git 历史；曾公开的 lineage 永久拒绝 purge。
  允许的 purge 也会写入不可删除 tombstone，防止旧 lineage 被伪造复活。
- `identity.json`、`generations/` 与 `tombstones/` 只能由同仓 GitHub App 的受保护 lifecycle PR
  修改；普通贡献者和人工审核员都不能直接编辑这些身份记录。
