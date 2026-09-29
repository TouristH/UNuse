# 向 UNuse 发布插件（历史文档）

> [!CAUTION]
> **废弃在线仓库：UNuse 已停止维护。** 本仓库不再接收新插件、版本、审核或生命周期请求。
> 以下内容仅为保留既有格式、安全边界和运维流程的历史说明。

插件中心采用“作者仓库发布、中心仓库索引”的方式。作者不把 DLL 或 ZIP 提交到这里，
也不需要为每个版本修改中心仓库；中心仓库从作者仓库根目录的 `_manifest.json` 同步有界完整
发行历史，并把通过验证的缺失版本追加到中心目录。

## 1. 准备插件包

本项目不再提供或指向外部插件开发规范；以下 API v1 要求仅用于解释存量发行包的历史合同。

发行 ZIP 必须满足：

- ZIP 根目录直接包含 `plugin.json`，不能再套一层目录。
- 包内包含 `entryAssembly` 指向的 DLL 和需要的私有依赖。
- 不得打包由宿主提供的 API 合同程序集。
- `plugin.json` 必须明确填写稳定的小写反向域名 ID、严格 SemVer 版本、`apiVersion: "1.0"`
  和 `minimumLauncherVersion`。
- SemVer 的 major/minor/patch 与纯数字预发布标识不能超过 `2147483647`，以匹配启动器解析范围。
- 必要与可选能力必须真实、完整，并与 `_manifest.json` 一致。
- 包内路径不得穿越、包含符号链接、Windows 保留名或大小写冲突。
- 设置项的 `pattern` 会交给与启动器一致的 .NET Regex 编译器验证；本地运行完整验证需要
  PowerShell 7（`pwsh`）。

## 2. 创建固定 GitHub Release

在插件自己的公开 GitHub 仓库创建版本 Release 并上传 ZIP。必须使用固定标签的资产 URL：

```text
https://github.com/<owner>/<repo>/releases/download/<tag>/<asset>.zip
```

不接受 `latest`、分支文件、Actions 临时产物或可以原地覆盖的下载地址。记录 ZIP 的精确大小和
小写 SHA-256：

```powershell
(Get-Item .\plugin.zip).Length
(Get-FileHash .\plugin.zip -Algorithm SHA256).Hash.ToLowerInvariant()
```

```bash
stat -c %s plugin.zip
sha256sum plugin.zip
```

## 3. 添加 `_manifest.json`

将 [`templates/_manifest.json`](templates/_manifest.json) 复制到插件仓库根目录。示例：

```json
{
  "$schema": "https://raw.githubusercontent.com/TouristH/UNuse/main/schemas/publisher-manifest-v1.schema.json",
  "manifest_version": 1,
  "id": "io.github.example.toolbox",
  "name": "Example Toolbox",
  "description": "示例工具插件。",
  "authors": ["Example Team"],
  "license": "MIT",
  "repository_url": "https://github.com/example/unuse-toolbox",
  "maintainers": ["example"],
  "categories": ["utilities"],
  "releases": [
    {
      "version": "1.2.0",
      "channel": "stable",
      "published_at": "2026-08-20T12:00:00Z",
      "release_notes_url": "https://github.com/example/unuse-toolbox/releases/tag/v1.2.0",
      "download": {
        "url": "https://github.com/example/unuse-toolbox/releases/download/v1.2.0/io.github.example.toolbox-1.2.0.zip",
        "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "size": 123456
      },
      "api_version": "1.0",
      "minimum_launcher_version": "0.1.1",
      "required_capabilities": ["ui.components"],
      "optional_capabilities": []
    }
  ]
}
```

顶层名称、描述、作者、许可证、仓库、维护者和分类是首次收录后不可修改的稳定元数据。
`releases` 必须按严格 SemVer 升序保留作者发布过的完整版本历史；每个版本分别声明兼容性、能力和
固定 ZIP。不得删除已被中心看到的版本，也不得修改同版本 URL、大小、哈希、兼容性或能力。
每个版本的元数据必须与其 ZIP 根目录中的 `plugin.json` 一致。`maintainers` 使用 GitHub 登录名；
`categories` 可用值为：

```text
appearance automation gameplay integration launch management utilities
```

需要声明最大兼容版本时，在对应 `releases[]` 项添加 `maximum_launcher_version_exclusive`，其值为严格 SemVer。

单份清单最多包含 128 个版本，所有 `download.size` 合计最多 4 GiB。同步器每轮从尚未收录的
版本中优先选择最新版本，再按 SemVer 降序选取；单轮最多验证 16 个、声明大小合计最多 512 MiB。
批内所有 ZIP 全部通过后才会一起写入；其余较旧版本由后续轮次继续回填。

## 4. 自动首次收录

自动收录的插件 ID 必须使用仓库所有者可验证的 GitHub 名空间：

```text
io.github.<仓库 owner 小写>.<插件名>
```

例如 `https://github.com/example/unuse-toolbox` 使用 `io.github.example.toolbox`。这条规则防止公开
Topic 仓库抢占别人的插件 ID；需要使用自有域名 ID 时，应先由管理员核验域名所有权。
首次收录还会生成 UUID `lineageId`，并把 GitHub 的 `repositoryId` 与 `ownerId` 固定到
`plugins/<id>/identity.json` 和 active 指针。仓库或账号改名、转移以及旧路径被重新占用都不会自动
继承发布权；publisher 变化只能走下文的定向生命周期流程。

同一个 numeric `repositoryId + ownerId` 只改 GitHub 仓库路径属于安全改名，不是转让。机器人会在
当前 generation 的 `repositoryUrlHistory` 尾部追加新的 canonical
`https://github.com/<owner>/<repo>`（无尾斜杠、无 `.git`），并同步更新 active 指针和该代
`plugin.json.repositoryUrl`。每代 history 为 1–64 个大小写不重复的有序地址，最后一项永远是当前
canonical URL；已记录前缀不可删除、重排、插入或回滚。新清单中历史版本的 Release ZIP/notes URL
可以继续匹配本代任一旧 alias。URL 只是发布事实的历史别名：旧路径后来被全新 numeric repository
复用，不会继承旧插件身份，也不会因别名本身永久阻止新仓库收录。若 `repositoryId` 或 `ownerId`
变化，必须走管理员 transfer；不能伪装成改名。为防止不理解别名的旧客户端误判，history 多于一项
的 generation 不进入严格 v1 索引，只出现在 v2。

完成 Release 与 `_manifest.json` 后，历史流程会通过约定的 discovery Topic 发现作者仓库。定时机器人会：

1. 搜索公开、非 Fork、未归档仓库；
2. 下载并静态校验 `_manifest.json`；
3. 在全局资源预算内验证固定 ZIP、SHA-256 与运行时清单；
4. 创建同仓 `registry-bot/sync` PR；
5. 在可信策略与完整测试通过后自动合并。

成功收录的版本立即出现在索引中，但默认是“未审核”。不需要管理员输入 `/validate` 或
`/approve`。GitHub Topic 索引延迟时，可以创建
[Plugin Queue Issue](../../issues/new?template=add-plugin.yml)；Issue 候选在同一有界机器人任务中优先
处理，不会因为打开 Issue 就单独触发大文件下载。

状态含义：

| 状态 | 含义 |
| --- | --- |
| `queued-for-intake` | 已进入自动收录队列 |
| `validation-failed` | 清单或固定包不合规；修复后关闭并重新打开 Issue 以重新排队 |
| `pending-merge` | 机器人 PR 正在等待必需检查与自动合并 |
| `listed-unreviewed` | 已收录，但尚无管理员绿色审核标志 |

## 5. 发布后续版本

1. 增加版本号并重新构建插件。
2. 创建新的固定 GitHub Release 和 ZIP 资产。
3. 将新版本项按 SemVer 升序追加到 `_manifest.json` 的 `releases[]`；不得替换或删除旧项。
4. 等待定时同步；仓库维护者也可手动运行 `Refresh publisher manifests` workflow_dispatch。

同一代的同步器只会新增 `plugins/<id>/releases/<version>.json`（g2 以后为
`plugins/<id>/generations/gN/releases/<version>.json`）。已有版本的 URL、大小、SHA-256、
兼容性或能力声明不能被覆盖。常规定时同步不会反复下载既有 ZIP；若作者在同一 URL 重传资产，
启动器会因中心固定 SHA-256 不匹配而拒绝安装，维护者也可运行
`python tools/validate.py --check --verify-assets` 做全量历史审计，再撤回版本或审核标志。
单个作者仓库暂时不可访问或清单无效时，同步器会保留其原历史并发出告警，同时继续处理其他插件；
任何新候选仍必须先通过固定 ZIP、哈希和完整包校验，失败的候选不会写入历史。

## 6. 撤回与安全问题

不要删除历史版本。创建 Remove/Yank Issue，维护者会将相关版本设为 `yanked: true` 并写明原因。
启动器不会提供已撤回版本的安装入口。若一个插件的所有版本都已撤回，v2 会保留完整条目并标记
`visibility: hidden`，商店发现页自动隐藏它；已安装用户仍可收到撤回原因。严格旧版 v1 索引则完全
省略该插件。中心仍保留历史和 numeric identity，并继续监控 active publisher 的真正新版本。

管理员审核是独立流程。作者可创建 Review Issue，机器人会显示中心记录的 canonical SHA-256。
管理员完成源码、依赖、能力和行为检查后执行完整命令：

```text
/verify io.github.example.toolbox@g1:1.2.0 sha256:<64 位小写哈希> 可选审核说明
```

只有 `repository.json.trustedReviewers` 中的账号可执行。审核机器人不会信任可编辑的 Issue 正文，
而是再次用命令中的 ID、版本和 SHA-256 查询中心历史、重新下载固定 ZIP 并完整验证，然后通过
`registry-bot/review/*` PR 写入 `reviews/<id>/<version>.json`。撤销绿色标志使用：

```text
/revoke-review io.github.example.toolbox@g1:1.2.0 sha256:<同一哈希> 撤销原因
```

`gN` 是审核目标代际，不能省略（旧 g1 命令仅作为迁移兼容输入）。审核文件永久保留
`generation + version + sha256 + stateById`；撤回或撤销时写成 `status: revoked`，不删除顺序墓碑。

## 7. 插件身份生命周期

所有入口都是 [Plugin lifecycle Issue](../../issues/new?template=plugin-lifecycle.yml)。普通贡献者不能
编辑 `identity.json`、代际目录或 tombstone。Lifecycle workflow 只接受数字身份与命令完全匹配的
管理员评论，并由 GitHub App 创建 `registry-bot/lifecycle/issue-N` PR；该 PR 仍必须通过 policy、
validator 与全部测试。

### 7.1 退役（retire）

1. 作者或管理员创建 Lifecycle Issue，选择 `retire`，填写当前 v2 中的插件 ID、`generation`、
   source `repositoryId` 和公开原因；目标仓库填写 `N/A`。
2. 用户账号拥有的源仓库，由源仓库 **numeric ownerId 对应账号** 在同一 Issue 先评论：

   ```text
   /confirm-retire io.github.example.toolbox@g1 source:123456789
   ```

3. 组织仓库不能用组织账号评论，改用 7.4 节的固定确认文件。
4. 管理员核对数字身份后评论：

   ```text
   /apply-lifecycle retire io.github.example.toolbox@g1 source:123456789
   ```

5. 工作流只撤回**当前代仍未撤回**的版本，并把对应仍为 `verified` 的审核写成 `revoked`；旧代以及
   已 yanked/revoked 的原因、actor 和时间永久原样保留。随后从 `plugins.json` 移除 active 指针，并在
   identity ledger 记录管理员 numeric ID、Issue、comment、原因和作者确认来源。

源仓库可以已经是 **public archived**：归档不会改变 numeric identity，retire/transfer 会允许它作为
source，但 target 仍必须公开、非 Fork、未归档且可用。若源仓库已删除、disabled 或改为 private，
GitHub API/确认文件无法建立可信控制链，生命周期事务会安全失败；请先由原 owner 恢复仓库并公开，
再重试。管理员不能用可复用的 URL 或聊天声明绕过 numeric source 校验。

退役不会释放 ID。原数字仓库仍可安全恢复：发布一个高于本代历史最高 SemVer 的全新固定 ZIP，保留
完整 `_manifest.json`，并重新添加 Topic（或重开 Queue Issue）。机器人验证后用同一 lineage、同一
generation 恢复 active。这个恢复仍需机器人 PR；新版本仍是未审核状态。repositoryId、ownerId 或
仓库绑定任一不一致，都会拒绝自动恢复并要求管理员处理。

### 7.2 定向转让（transfer）

不要先使用 GitHub Settings 里的 **Transfer ownership**。注册表把
`repositoryId + ownerId` 作为已发布身份；GitHub 原生转移会保留 repositoryId、直接改变 ownerId，因而
会被同步机器人 fail-closed 拒绝，不能作为同代改名自动接受。安全流程要求目标是一个由新维护者创建的
**独立仓库（不同 repositoryId）**，再按下列 lifecycle transfer 生成新 generation。如果已经做了
GitHub 原生转移，请先把源仓库临时转回原 owner（恢复原 numeric owner 绑定并保持 public），然后让新
维护者建立独立目标仓库，再执行下面的流程；源仓库已删除或变 private 时也必须先恢复，管理员不能仅凭
URL 绕过数字身份。

1. 先按 7.1 完成 retire；不能在插件仍 active 或任何旧版本仍可用时转让。
2. 新仓库准备相同插件 ID 的 `_manifest.json` 与至少一个固定 Release ZIP。所有新代版本的
   `minimum_launcher_version` 必须至少为 `repository.json.v2MinimumLauncherVersion`。
3. 新建/更新 Lifecycle Issue，选择 `transfer`，填写源 ID、当前 generation 和完整目标仓库 URL。
4. 原作者精确确认目标 numeric repositoryId：

   ```text
   /confirm-transfer io.github.example.toolbox@g1 source:123456789 target:987654321
   ```

5. 管理员使用完全相同的事务字段执行：

   ```text
   /apply-lifecycle transfer io.github.example.toolbox@g1 source:123456789 target:987654321
   ```

工作流现场从 GitHub numeric API 解析 source/target，拒绝 URL 冒名、旧 generation、重复 target repo
或 owner 变化。成功后 lineage 不变，generation 从 g1 变为 g2，旧代绑定标记 `transferred`，目标代
暂时 `hidden/transferred`。下一次同步只有在固定 ZIP 完整验证通过后才变为 `active/listed`。跨代允许
重复 SemVer，但唯一键是 `(generation, version)`；启动器必须把转让视为新发布者选择，禁止自动把
g1 安装升级为 g2，旧审核也不继承。

### 7.3 清除误收录（purge）

purge 不是“释放已用 ID”的常规下架功能。管理员只能对仍在受保护生命周期 PR 工作树、**从未进入
任何提交过的 v1/v2 公开索引** 的误收录 staging 记录使用：

```text
/apply-lifecycle purge io.github.example.toolbox@g1 source:123456789 staging-pr:456
```

`staging-pr` 首次触发时必须仍 open、由同仓 App 创建、head 为实际收录分支
`registry-bot/sync`、base 为 main 且只包含该一个插件。一个不占用 `registry-write` 的前置 job 会先
核对管理员 numeric ID、PR/App/同仓 numeric repository、head SHA 与保留分支，立刻关闭原 PR，以阻止
已经开启的 auto-merge 抢先落入 main；随后持写锁的 job 才在隔离 worktree 中做完整校验，且绝不执行
staging PR 自带的脚本。
工具会同时确认：main 没有该 identity/active 指针；没有 verified/revoked 审核文件；git 历史中从未
出现该 ID/lineage。任一条件不满足即失败，已关闭的 PR 与分支会保留。如果前置 job 已关闭 PR，但
后续 job 因 concurrency pending 被替换取消，管理员可直接重新发送同一精确命令；前置 job 会幂等接受
同一未合并、App-owned、分支仍存在且 SHA 未变的 closed PR，**不需要重开可能自动合并的 staging
PR**。若要修正 staging 内容，则让 Refresh 创建新的 PR，并在新 Issue/命令中绑定新编号。校验成功会
创建只留下 tombstone 的受保护 lifecycle PR；工作流必须等该 PR 真正
`MERGED` 后才删除原 staging 分支，超时或检查失败不会提前删除。tombstone 包含管理员 numeric ID、
Issue/comment、原因和原 publisher 绑定。以后文本 ID 可以重新收录，但必须获得全新随机 lineage，
旧 lineage 永远不能复活。已经合并或曾公开的插件只能 retire/transfer。

### 7.4 组织仓库确认文件

组织仓库在默认分支根目录提交历史流程约定的生命周期确认文件。文件必须与事务字段完全相等，不能有
额外字段。retire 示例：

```json
{
  "schemaVersion": 1,
  "operation": "retire",
  "pluginId": "io.github.example.toolbox",
  "generation": 1,
  "sourceRepositoryId": 123456789
}
```

transfer 还要增加 `"targetRepositoryId": 987654321`。管理员应通过组织仓库受保护分支和 CODEOWNERS
确认该文件由拥有管理员权限的人批准；中心工作流从默认分支的固定文件验证控制权。完成事务后删除
或更新文件，旧文件因 generation/source/target 不同而不能重放。

## Pull Request

普通插件发布使用 Topic 或 Issue 兜底，避免多人同时修改中心索引。自动收录、版本同步、撤回与审核
均由同仓机器人 PR 写入；普通 Pull Request 主要用于维护工具和文档。CI 会阻止普通贡献者直接更改
生成索引、历史版本或审核记录。

仓库管理员应保护 `main`：所有人工变更必须走 PR，并将
`Enforce trusted registry policy / policy` 与 `Validate registry / validate` 设为必需检查。
前者通过 `pull_request_target` 运行主分支上的可信策略代码，不会检出或执行 PR 中的脚本；
不要用普通 PR 自己修改后的校验脚本替代它。必需检查必须启用“合并前分支必须为最新”
（strict/up-to-date），避免旧 PR 沿用旧版可信名单或策略结果。当前工作流未声明 `merge_group`，
因此在补齐对应触发器前不要启用 Merge Queue。

写入身份是只安装在本仓库的 GitHub App。工作流完成所有不可信输入处理和回归测试后，才用
工作流通过 GitHub App Client ID 与 Private Key 申请一小时短期 installation token，
向 `registry-bot/*` 分支推送并创建 PR。App 不得获得 `main` bypass；合并必须经过 base-owned policy、
完整 validator 和 strict/up-to-date 规则。Issue 标签与评论继续使用权限受限的 `GITHUB_TOKEN`。
另需用 ruleset 将 `registry-bot/**` 分支的创建和更新限制为该 App；policy 还会核对每次 PR 事件的
sender，防止普通写权限协作者接管已有机器人 PR。

不要配置长期 writer token，也不要给普通维护者或通用 Actions 身份直接推送权限。
完整部署步骤见 [docs/REGISTRY_BOT.md](docs/REGISTRY_BOT.md)。

## 运维提醒

GitHub 会在公开仓库连续 60 天没有仓库活动后自动停用定时工作流。管理员应监控
`Refresh publisher manifests` 最近一次成功时间，并在停用后重新启用；需要无人值守保证时，使用
仓库外的受信 watchdog 定期触发 `workflow_dispatch`。参见
[GitHub 官方说明](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)。
