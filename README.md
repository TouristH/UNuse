# UNuse（废弃在线仓库）

> [!CAUTION]
> **废弃在线仓库：本项目已停止维护。** 不再接收新插件、审核或生命周期请求。下方内容仅作为
> 历史设计与安全说明保留，请勿将索引、机器人或工作流视为仍受维护的在线服务。

UNuse 是已停用的社区插件索引。插件代码和安装包仍由作者自己的 GitHub 仓库托管，
本仓库只负责收录来源、保存不可变版本历史、验证发行包并生成启动器读取的静态索引。

本项目不再提供或指向外部插件开发规范；下述 API v1 流程仅用于说明存量索引的历史合同。

> 收录不等于代码安全审核。插件是与启动器同进程运行的 .NET 代码，能力授权不是操作系统沙箱。

## 历史工作方式

本仓库停用前结合了 GitHub Topic 自动发现、Issue 兜底与固定 Release 包三种机制：

1. `plugins.json` 登记插件 ID、作者仓库地址及 GitHub 不可复用的仓库/所有者数字 ID。
2. 插件作者在自己仓库根目录维护 `_manifest.json`，以升序 `releases[]` 保留完整发行历史。
3. 每个发行版必须是不可变的 GitHub Release ZIP，并声明精确 URL、字节数和 SHA-256。
4. 收录机器人曾通过约定的 discovery Topic 搜索候选，并自动下载 ZIP，检查包根 `plugin.json`、
   入口 DLL、兼容性、能力声明和安全路径。
5. 验证通过的新版本只会追加到该插件自己的历史目录，不会覆盖旧版本。
6. 生成两个互不混用的静态合同：严格兼容旧启动器的 `public/v1/index.json`，以及携带
   永久身份、代际和隐藏状态的 `public/v2/index.json`。

```text
plugins/<plugin-id>/
├─ identity.json                 # 永久 lineage、数字仓库绑定和生命周期审计
├─ plugin.json                   # generation 1 的冻结元数据
├─ releases/                     # generation 1 的不可变历史
│  ├─ 1.0.0.json
│  └─ 1.1.0.json
└─ generations/
   └─ g2/                        # 仅管理员定向转让后出现
      ├─ plugin.json             # generation 2 的冻结元数据
      └─ releases/
         └─ 1.0.0.json           # 跨代可以重复 SemVer，不会被当作自动升级
```

因此，一个插件始终对应一个完整目录；启动器可以展示并选择仍兼容、未撤回的历史版本。
首次收录会生成不可复用的 UUID `lineageId`，并永久绑定 GitHub numeric `repositoryId + ownerId`。
仓库改名不会改变数字身份；仓库被删除后由其他人占用相同 URL 也不能继承插件 ID。
同一 numeric 仓库改名时，中心会在当前代 `repositoryUrlHistory` 后追加 canonical URL，并保留旧
Release 链接可验证；旧项不可删除、重排或回滚，最后一项始终是当前地址。history 多于一项的代际
只进入 v2，避免旧 v1 客户端把改名误判为另一个发布者。

## 历史发布流程

停用前的正常发布不需要 Fork 中心仓库，也不需要等待管理员批准：

1. 在作者仓库创建固定 GitHub Release，上传 ZIP。
2. 在作者仓库根目录添加 `_manifest.json`（示例见
   [`templates/_manifest.json`](templates/_manifest.json)）。
3. 插件 ID 使用 `io.github.<仓库所有者小写>.<名称>`，并给仓库添加约定的 discovery Topic。
4. 机器人自动发现、严格验证并通过机器人 PR 收录；收录时默认没有绿色审核标志。

停用前若 GitHub Topic 索引短暂延迟，可使用 Plugin Queue Issue 进入同一处理队列；当前仓库已不再
接收或处理此类请求。

首次收录后，作者发布新版本只需创建新的 Release，并把新项追加到 `_manifest.json` 的完整
`releases[]`。同步任务会以有界批次将缺失版本追加到 `plugins/<id>/releases/`，不会因两次采样间
连续发布而漏版。同一个版本号的 URL、大小、哈希或兼容信息一旦进入中心仓库便不可更改；需要修复时
必须发布新版本号。

完整要求见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 历史收录与审核模型

- **已收录（listed）**：版本通过格式、哈希、ZIP 与运行时清单验证，可以出现在仓库中；不代表管理员读过代码。
- **管理员已审核（verified）**：可信审核者审查了精确的插件 ID、版本和 ZIP SHA-256，启动器显示绿色标志。
- **未经审核**：启动器显示醒目警告，并在下载前要求用户再次确认风险。

审核记录位于 `reviews/<插件 ID>/<版本>.json`。管理员完成代码与行为检查后，在 Review Issue
中执行机器人给出的完整命令：

```text
/verify io.github.example.toolbox@g1:1.2.0 sha256:<中心记录的 64 位哈希>
```

命令中的 ID、版本和 SHA-256 会再次与中心历史核对，固定 ZIP 也会重新下载验证。撤销审核使用
`/revoke-review ...`；它只移除绿色标志，不删除历史。若同版本资产被替换，审核不会迁移，
启动器与审核机器人都会拒绝不匹配的字节。

审核记录同时绑定 `generation`。转让后的 g2 版本必须重新审核，g1 的绿色标志绝不会跨代继承。

## 隐藏、退役、恢复、转让与清除

- **全版本撤回**：历史、撤回原因和数字身份全部保留；v2 中为 `visibility: hidden`，商店发现页
  不展示，但已安装用户仍能看到撤回警告。v1 中直接省略，避免旧启动器继续展示不可安装插件。
- **同源恢复**：原 `repositoryId + ownerId` 发布一个高于该代历史最高版本的全新、未撤回固定 ZIP
  后，发现机器人会用原 `lineageId`、原 `generation` 恢复 active。仍需中心机器人 PR 和人工审核；
  不会自动恢复绿色标志。仅补传旧版本或复用版本号会被拒绝。
- **退役（retire）**：原作者精确确认后，管理员通过 Lifecycle Issue 撤回全部版本并移除 active
  指针；历史继续占用 ID，防止公开抢注。
- **定向转让（transfer）**：必须先退役，并使用新维护者创建的独立目标仓库（不同 repositoryId）。
  原作者确认具体 source/target 数字仓库 ID 后，管理员把身份推进到下一 `generation`。不要先使用
  GitHub 原生 Transfer ownership；它保留 repositoryId 却改变 ownerId，会被 fail-closed 拒绝。若已
  转移，先临时转回原 owner，再按独立目标仓库流程处理。旧代全部保持撤回，新代最低要求支持 v2
  的启动器，启动器不得跨代自动升级。
- **清除（purge）**：只用于从未进入任何公开索引、没有审核记录且 main 没有 active 指针的误收录
  staging 数据。工作流先关闭待清除 PR 但保留分支，只有受保护 lifecycle PR 真正合并后才删除该
  分支。曾合并、曾公开或曾审核的 lineage 永远不能 purge，只能退役；成功 purge 仍保留永久
  tombstone，未来复用相同文本 ID 时必须生成全新 lineage。

Lifecycle Issue、作者确认和管理员命令的逐步操作见
[CONTRIBUTING.md](CONTRIBUTING.md#7-插件身份生命周期)；管理员部署与审核员添加流程见
[docs/REGISTRY_BOT.md](docs/REGISTRY_BOT.md)。

## 历史收录机器人

停用前，自动写入由仅安装在本仓库的 GitHub App 完成。工作流在所有不可信清单和 ZIP 验证结束后才申请
一小时短期安装令牌，创建同仓机器人 PR，并交由 `policy` 与 `validate` 两项检查后自动合并；
机器人没有绕过 `main` 规则的权限。部署步骤见 [机器人配置文档](docs/REGISTRY_BOT.md)。

历史部署需要完成以下远端配置：

1. 创建 GitHub App，并且只安装到本插件中心仓库；
2. 配置 GitHub App Client ID 变量和 Private Key Secret；
3. 开启仓库 Auto-merge；
4. 保护 `main`，将 `policy` 与 `validate` 设为必须且要求分支保持最新；
5. 限制 `registry-bot/**` 只能由该 GitHub App 创建和更新。
6. 在 `repository.json` 同时登记每位审核员的登录名与不可复用 numeric user ID。

不要配置长期个人 PAT，也不要给予机器人直接推送 `main` 的 bypass。缺少任一项时，工作流会
安全失败，不会退化为跳过验证直接写入。

## 存档文件

- [`plugins.json`](plugins.json)：受监控作者仓库及其 GitHub numeric identity 列表。
- [`plugin_details.json`](plugin_details.json)：从完整历史目录生成的展示数据。
- [`public/v1/index.json`](public/v1/index.json)：严格旧合同；不包含任何身份新字段、g2 或隐藏插件。
- [`public/v2/index.json`](public/v2/index.json)：身份感知合同；保留完整代际、撤回历史与隐藏状态。

存档索引地址（不再更新）：

```text
https://raw.githubusercontent.com/TouristH/UNuse/main/public/v2/index.json
```

仅支持旧合同的启动器可继续读取 `public/v1/index.json`。身份感知启动器只可在 v2 URL 明确返回
HTTP 404 时回退 v1；一旦收到 v2，若合同无效或 `minimumLauncherVersion` 过高必须拒绝加载，不能
降级到 v1，也不能忽略未知字段自行解析。

## 许可证

仓库工具与元数据格式使用 [MIT](LICENSE)。插件及其二进制包仍按各自声明的许可证发布。
