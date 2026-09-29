# UNuse 收录机器人：历史配置与排错

> [!CAUTION]
> **废弃在线仓库：UNuse 已停止维护。** 本文仅保留原有自动化、安全隔离和故障排查说明，
> 不代表机器人、定时任务或人工审核仍在运行。

本仓库把“自动收录”和“人工审核”拆开：定时工作流验证作者仓库与固定 Release ZIP，GitHub App
只负责把验证结果写成 PR；管理员审核仍由 `trustedReviewerIds` 中的人手工执行。App 没有 `main`
bypass，所有写入都必须经过同一套 policy、validator 和测试。

## 0. 配置前检查

需要具备插件中心仓库的 Administrator 权限，并确认默认分支是 `main`。先在本地运行：

```powershell
py -3 tools/validate.py --write
py -3 tools/validate.py --check
py -3 -m unittest discover -s tests -v
```

三条命令都成功后再推送配置 PR。schema2 main 上的 `--write` 会同时重建 `plugin_details.json`、v1
和 v2；不要手工只改某一个生成文件。

### 0.1 从 schema v1 首次上线 v2（必须两阶段）

远端 main 仍是 schema v1 时，不能把新 policy 与数据迁移塞进同一个 PR：
`pull_request_target` 会执行 base 分支的旧 policy。请严格按以下顺序：

1. 上线前暂停定时 Refresh，取消/等待所有 `registry-write` run 归零，并关闭尚未合并的旧
   `registry-bot/**` 数据 PR；否则旧 run 可能在迁移锚点之后改变 schema1 插件集合。
2. **PR1（基础设施）**只提交 workflow、`tools/`、schema、tests、文档、模板和
   `migrations/v2-bootstrap.json`。锚点逐插件固定现有 URL、真实 numeric repository/owner ID 与预期
   lineage，并固定 v1/目标 reviewer numeric 映射。PR1 不得修改 `repository.json`、`plugins.json`、
   `plugin_details.json`、任何已有插件/review 数据或 `public/**` 生成文件。
3. PR1 合并后不要重新启用写工作流。新 policy 会冻结 schema1 下所有 App 数据 PR，workflow 自身也
   在调用机器人前要求 `schemaVersion == 2`；Refresh 的只读 gate 在精确 schema1 时绿色跳过写 job，
   但缺字段、错误类型、未知版本或损坏 JSON 会红灯失败。锚点一旦进入 base，任何账号或 App 都不能
   修改/删除。
4. **PR2（唯一数据迁移）**从最新 PR1 main 创建，只做确定性 v1→v2 数据迁移：更新
   `repository.json`/active 指针，按锚点新增 g1 identity，仅给既有 release/review 补 generation 与
   reviewer numeric ID，并重建生成视图。不得增删版本或改变 URL、哈希、大小等发布事实。
   `public/v1/index.json` 若字节未变化，不要伪造 diff。
5. PR2 的 base policy、`py -3 tools/validate.py --check` 与全套测试全部通过并合并后，才重新启用
   Refresh/Issue 写入并清理旧 run。schemaVersion 一旦为 2，所有 human/App PR 都永久禁止降回 1，
   一次性 bootstrap 通道不会再次开启。

schema1 阶段运行 `--write/--check` 只维护旧 `plugin_details.json + public/v1`，不会提前生成部分 v2；
上文“同时重建 v1/v2”仅适用于 PR2 完成后的 schema2 main。

## 1. 创建专用 GitHub App

在 GitHub 头像菜单进入 **Settings → Developer settings → GitHub Apps → New GitHub App**。

- GitHub App name：例如 `UNuse Registry Bot`（名称必须全站唯一）。
- Homepage URL：`https://github.com/TouristH/UNuse`。
- Webhook：取消 **Active**；本项目完全由 Actions 调度。
- Repository permissions：
  - Contents: **Read and write**
  - Pull requests: **Read and write**
  - Metadata: **Read-only**（自动包含）
- Account permissions：全部 **No access**。
- Where can this GitHub App be installed：优先 **Only on this account**。

创建后记录页面显示的 **Client ID**。App slug 会形成 `<slug>[bot]` 登录名；将
`repository.json.registryBotLogin` 改成实际值。

在 App 页面点击 **Generate a private key** 下载 PEM。私钥只用于仓库 Actions Secret，不粘贴到
Issue、PR、日志或仓库文件。

## 2. 只安装到插件中心仓库

在 App 页面选择 **Install App**，对 `TouristH` 账号选择 **Only select repositories**，只勾选
`UNuse`。不要安装到作者插件仓库或启动器仓库；自动发现读取的是公开 API，不需要 App
访问作者仓库。

安装完成后，在插件中心仓库打开 **Settings → Secrets and variables → Actions**：

1. Variables 页新增工作流约定的 Client ID 变量。
2. Secrets 页新增工作流约定的 Private Key Secret，值为完整 PEM（包含 BEGIN/END 行）。

不要创建长期 writer token 或个人 PAT。工作流使用官方 Action 为当前仓库签发短期
installation token，job 结束后撤销。

## 3. 添加或移除人工审核员

审核授权不能只依赖可改名登录名。每个人必须在 `repository.json` 两处一一对应：

```json
{
  "trustedReviewers": ["TouristH", "SecondReviewer"],
  "trustedReviewerIds": {
    "TouristH": 143396778,
    "SecondReviewer": 123456789
  }
}
```

获取数字 ID：

```powershell
gh api users/SecondReviewer --jq '{login: .login, id: .id}'
```

也可在未登录 API `https://api.github.com/users/SecondReviewer` 查看 `id`。必须现场核对响应 `login`，
不要从聊天消息复制未知数字。新增审核员的 PR 必须由**当前已经受信任**的审核员创建；仓库 Owner
如果不在 base 分支的 `trustedReviewers` 中，也不会自动获得 policy 权限。合并后，新审核员重新打开
或重新评论 Review/Lifecycle Issue 才会按新配置授权。

移除审核员必须分两次落地，顺序不能反过来：

1. 由另一位**仍受信任**的审核员对该账号名下仍为 `verified` 的每个精确
   `generation + version + sha256` 执行 `/revoke-review ...`，等待所有 review PR 合并；
2. 确认仓库中已无 `stateBy=<待移除账号>` 的绿色记录，再由仍受信任审核员创建配置 PR，同时删除
   `trustedReviewers` 数组项和 `trustedReviewerIds` 映射项。

不能先删除配置再撤销：绿色记录要求 `stateBy + stateById` 仍对应当前审核员，顺序反转会让 validator
拒绝配置 PR。已经写成 `revoked` 的记录是永久历史墓碑，之后只要求合法登录名和正 numeric ID；即使
该账号已离任也必须保留，且不会恢复绿色标志。被移除账号不能创建后续审核或生命周期事务。

## 4. 首次让两个必需检查出现

ruleset 的 Required status checks 通常只能选择近期运行过的检查。先从普通功能分支创建一个配置
PR，让以下 workflow 至少运行一次：

- `Enforce trusted registry policy` 的 job `policy`；
- `Validate registry` 的 job `validate`。

检查页面显示的完整 context 通常是：

```text
Enforce trusted registry policy / policy
Validate registry / validate
```

如果下拉列表暂时没有它们，先不要输入一个相似但错误的名称；等待这次 PR workflow 完成，再刷新
ruleset 页面。

## 5. 保护 main

在 **Settings → Rules → Rulesets → New branch ruleset** 创建 `Protect main`：

- Enforcement status：Active。
- Target branches：只包含默认分支 `main`。
- Require a pull request before merging：开启。
- Required approvals：要保持“自动技术收录、人工安全审核”，设为 **0**。人工安全审核通过 `/verify`
  决定绿色标志，不是逐个批准收录 PR。若这里设为 1 或更多，每个 App 数据 PR 都必须人工批准，属于
  管理员自选的额外门槛，不再是全自动收录。
- Dismiss stale approvals / require approval of latest push：可按团队要求开启。
- Require status checks to pass：开启，并选择上节两个精确 context。
- Require branches to be up to date before merging：开启。
- Block force pushes、Restrict deletions：开启。
- Merge Queue：暂不开启；当前 workflows 未声明 `merge_group`。

不要给 GitHub App、GitHub Actions、管理员或审核员添加 `main` bypass。App 只能推机器人分支和创建
PR，不能直接写 `main`。

仓库 **Settings → General → Pull Requests** 中开启 **Allow auto-merge**，建议允许 Squash merging。

## 6. 保护机器人分支

再创建分支 ruleset `Protect registry bot branches`，Target branch pattern 为 `registry-bot/**`：

- Restrict creations、Restrict updates、Restrict deletions：开启。
- 将刚创建的 GitHub App 作为此 ruleset 的唯一 bypass actor（Always allow）。
- 不要把普通用户、通用 GitHub Actions 或团队加入 bypass。

这个 bypass 只适用于 `registry-bot/**`，绝不能复用到 `main` ruleset。否则有 Contents 写权限的协作者
可能接管已打开的机器人 PR head；base-owned `policy.yml` 还会二次核对 PR 作者、事件 sender、同仓来源
与分支用途。

## 7. 工作流分别做什么

| Workflow | 入口 | 可写范围 | 结果 |
| --- | --- | --- | --- |
| Refresh publisher manifests | 每 6 小时 / 手动 | active 指针、身份、插件/版本历史、v1/v2 | 自动收录、同步、同源恢复 |
| Review plugin release | Review Issue 精确命令 | review 墓碑、v1/v2 | 添加/撤销绿色标志 |
| Approve plugin operation | Yank Issue 精确命令 | yanked、revoked review、v1/v2 | 撤回版本/全部版本 |
| Apply plugin identity lifecycle | Lifecycle Issue 管理员命令 | identity、代际、tombstone、v1/v2 | retire/transfer/purge |

四类写工作流共享 `concurrency.group: registry-write` 且 `cancel-in-progress: false`，正在运行的事务
不会被新任务取消。但 GitHub 同一 concurrency group 最多保留一个 running 和一个 pending；第三个
任务到来时，较旧 pending 仍可能被替换取消。管理员看到 cancelled 后应在对应 Issue **重新发送原
精确命令**，或手动重跑定时 Refresh；命令带 generation/repositoryId/comment 顺序检查，重复已落地
事务会安全失败而不是覆盖历史。一般事务先用只读 `GITHUB_TOKEN` 完成不可信输入验证，直到要创建
PR 时才签发 App token。`purge` 是例外：一个不持有 `registry-write` 的前置 job 先严格绑定 open
`registry-bot/sync` staging PR 的管理员 numeric ID、同仓 numeric repository、App 作者、base、head
SHA 和 branch，再使用短期 App token 立即关闭该 PR，阻止正在持锁等待它合并的 Refresh 与
auto-merge 抢先落入 main；后续 lifecycle job 才获取写锁。分支会保留到受保护 lifecycle PR 真正合并。

## 8. 验证自动收录

1. 在测试插件仓库准备合规 `_manifest.json`、固定 Release ZIP 和历史流程约定的 discovery Topic。
2. 插件中心 **Actions → Refresh publisher manifests → Run workflow**。
3. 确认出现 `registry-bot/sync` 同仓 PR。
4. 确认 PR 作者与 event sender 都是配置的 `<slug>[bot]`。
5. 确认 `policy`、`validate` 成功后自动 squash 到 main；App 没有直接 main push。
6. 检查 `public/v2/index.json`：新插件有 UUID lineage、g1 numeric publisher、`listed`，版本无 review。
7. 启动器应显示“未审核”风险提示；不能因为技术收录而显示绿色。

随后创建 Review Issue，复制机器人给出的精确命令，例如：

```text
/verify io.github.example.toolbox@g1:1.0.0 sha256:<64 位中心哈希>
```

第二个 App PR 合并后，v1/v2 对该精确 generation/version/hash 才出现 verified review。

## 9. 验证隐藏与生命周期

- 在 Yank Issue 撤回某插件全部版本：v1 应省略该插件；v2 应保留历史且
  `visibility: hidden`；商店发现页隐藏，已安装详情仍显示撤回原因。
- 原 numeric repo/owner 发布一个真正新增且更高的未撤回版本：Refresh 应以原 lineage/generation 创建
  PR 并恢复 listed；新版本仍未审核。
- retire：Lifecycle Issue 中原 owner 先 `/confirm-retire ...`，管理员再 `/apply-lifecycle retire ...`。
- transfer：必须先 retire；target 必须是新 owner 的独立仓库且 repositoryId 与 source 不同，确认命令
  包含 expected generation、source 和 target repositoryId。不要用 GitHub 原生 Transfer ownership；
  已经执行时先转回原 owner，再建立独立 target 走 lifecycle。PR 合并后 generation +1，v1 不得出现
  新代，v2 在新 ZIP 验证前保持 hidden。
- purge：只用人为制造的从未公开 staging 测试；原 staging PR 会先关闭但保留分支，lifecycle PR
  真正合并后才删除分支。若后续 pending job 被 concurrency 替换取消，直接在 Lifecycle Issue 重发
  原精确命令；前置 job 会核对同一 closed PR 的 App 身份、未合并状态、保留分支与原 SHA 后幂等继续，
  不要重开带 auto-merge 的 staging PR。若 staging 内容必须修正，让 Refresh 生成新 PR 并绑定新编号。
  已合并插件必须稳定失败并提示曾公开，不能为了测试删除真实历史。

完整命令与边界见 [CONTRIBUTING.md](../CONTRIBUTING.md#7-插件身份生命周期)。

## 10. 想微调代码却被 policy 拒绝

这是预期的权限分层，而不是让所有文件永久不可改：

- 普通贡献者只允许修改根目录明确列出的社区文档。要修改 `tools/`、workflow、schema 或配置，请由
  当前受信审核员创建 PR，或先按第 3 节由现有审核员把你加入 numeric reviewer 映射。
- 即使是审核员，也不能人工新增 release、编辑 identity/generation/tombstone、删除 review 墓碑或改写
  不可变版本。请使用对应 Issue/workflow；这是供应链边界，不能通过调 ruleset 绕过。
- 修改 validator/generator 后，先运行 `py -3 tools/validate.py --write`，把 v1 和 v2 的确定性变化一起
  提交，再运行完整测试。只改 `public/v1/index.json` 或只改源码都会让 validate 失败。
- policy 使用 `pull_request_target` 上 base 分支的可信代码。PR 自己放宽 `check_pr.py` 不会让当前 PR
  获得权限；规则变更需要由当前可信管理员单独审核合并。

## 11. 两项检查不能通过时

### `Enforce trusted registry policy / policy` 失败

打开失败 step 的 `PR 规则失败：...`：

- “只能修改明确允许的根目录文档”：PR 作者不在 **base 分支** trustedReviewerIds；按第 3 节添加。
- “只能由受保护 lifecycle 工作流修改”：不要人工改 identity/generation/tombstone，改用 Lifecycle Issue。
- “历史版本不可修改/只能 yanked-only”：发布新 SemVer，或用 Yank Issue。
- “registry bot ... App 自身更新同仓分支”：机器人分支被人手推送、重开或编辑；关闭该 PR，重新运行
  workflow，让 App 自己重建分支。不要人工修机器人 PR。
- “一次 PR 只能涉及一个插件”：拆分人工 PR；intake/sync App 才能批量收录。

### `Validate registry / validate` 失败

本地依次运行：

```powershell
py -3 tools/validate.py --write
py -3 tools/validate.py --check
py -3 -m unittest discover -s tests -v
git diff --check
```

- `--check` 提示生成文件过期：提交 `plugin_details.json`、`public/v1/index.json` 和
  `public/v2/index.json` 的 `--write` 结果。
- numeric identity 不匹配：不要把 GitHub URL 当身份；用 API 核对 repository `id` 与 owner `id`。
- review generation/hash 不匹配：从 v2 和 Review Issue 重新复制 `id@gN:version + sha256`。
- 非当前 generation 有未撤回版本：先通过生命周期工具撤回旧代；不能手改绕过。
- v2 新代 minimum launcher 太低：作者仓库发布的新代版本必须至少为
  `repository.json.v2MinimumLauncherVersion`。
- YAML workflow 无法启动：确认 `concurrency` 只有合法的 `group` 与 `cancel-in-progress`；不要加入
  不存在的 `queue` 键。

## 12. 日常运维

- 监控 `Refresh publisher manifests` 最近成功时间。公开仓库长期无活动可能停用 scheduled workflow；
  重新启用或用外部受信 watchdog 触发 `workflow_dispatch`。
- 定期运行 `python tools/validate.py --check --verify-assets` 做全历史资产审计；常规定时任务只下载本轮
  新候选，避免无界流量。
- 轮换 App 私钥：先添加新 PEM Secret 并验证一次 workflow，再在 App 页面撤销旧 key。
- App 安装或权限变化后重新检查它仍只安装在中心仓库，且 main ruleset 没有 bypass。
- 审核员账号离职或被盗时，严格按第 3 节：由另一位仍受信任审核员先撤销其全部绿色记录并等待
  合并，再从两处 reviewer 配置移除；不要删除旧 numeric 审计记录。

GitHub App 的 Actions 认证原理可参考 GitHub 官方文档：
[Making authenticated API requests with a GitHub App in GitHub Actions](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/making-authenticated-api-requests-with-a-github-app-in-github-actions)。
