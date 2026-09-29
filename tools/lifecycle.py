#!/usr/bin/env python3
"""Apply administrator-controlled plugin identity lifecycle transitions.

The workflow event is the transaction envelope.  Commands repeat the plugin
ID, expected generation, and immutable source/target repository IDs so an
edited Issue body or replay against a later generation fails closed.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import issue_submission  # noqa: E402
from tools import validate as validator  # noqa: E402


COMMAND = re.compile(
    r"^/apply-lifecycle (?P<operation>retire|transfer|purge) "
    r"(?P<id>[a-z][a-z0-9]*(?:\.[a-z0-9][a-z0-9-]*)+)@g(?P<generation>[1-9][0-9]*) "
    r"source:(?P<source>[1-9][0-9]*)"
    r"(?: target:(?P<target>[1-9][0-9]*))?"
    r"(?: staging-pr:(?P<staging>[1-9][0-9]*))?$"
)
MAXIMUM_API_BYTES = 8 * 1024 * 1024
MAXIMUM_COMMENT_PAGES = 10


class LifecycleFailure(Exception):
    pass


def load_event(path: Path) -> dict:
    try:
        event = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LifecycleFailure(f"无法读取 GitHub 事件：{exc}") from exc
    if (
        not isinstance(event, dict)
        or not isinstance(event.get("issue"), dict)
        or not isinstance(event.get("comment"), dict)
    ):
        raise LifecycleFailure("生命周期操作必须由 Issue 评论触发")
    if not str(event["issue"].get("title") or "").startswith("[Lifecycle]"):
        raise LifecycleFailure("生命周期命令只能用于 [Lifecycle] Issue")
    return event


def trusted_reviewer_ids() -> dict[str, int]:
    configuration, _ = validator.load_repository_configuration()
    values = configuration.get("trustedReviewerIds")
    if not isinstance(values, dict):
        raise LifecycleFailure("repository.json 缺少 trustedReviewerIds")
    return {login.casefold(): value for login, value in values.items()}


def actor_identity(event: dict) -> tuple[str, int, str, int]:
    comment = event["comment"]
    user = comment.get("user")
    login = user.get("login") if isinstance(user, dict) else None
    actor_id = user.get("id") if isinstance(user, dict) else None
    actor_type = user.get("type") if isinstance(user, dict) else None
    created_at = comment.get("created_at")
    comment_id = comment.get("id")
    reviewer_ids = trusted_reviewer_ids()
    if (
        not isinstance(login, str)
        or type(actor_id) is not int
        or actor_type != "User"
        or reviewer_ids.get(login.casefold()) != actor_id
    ):
        raise LifecycleFailure("只有 trustedReviewerIds 中数字身份匹配的管理员可以执行")
    try:
        timestamp = validator.validate_utc_timestamp(
            "lifecycle event", "comment.created_at", created_at
        )
    except validator.ValidationFailure as exc:
        raise LifecycleFailure(str(exc)) from exc
    if type(comment_id) is not int or not 1 <= comment_id <= 2**63 - 1:
        raise LifecycleFailure("comment.id 必须是正 Int64")
    return login, actor_id, timestamp, comment_id


def parse_request(event: dict) -> dict:
    match = COMMAND.fullmatch(str(event["comment"].get("body") or "").strip())
    if match is None:
        raise LifecycleFailure(
            "命令格式：/apply-lifecycle retire|transfer|purge "
            "插件ID@g代际 source:数字仓库ID [target:数字仓库ID] "
            "[staging-pr:未合并机器人PR编号]"
        )
    operation = match.group("operation")
    generation = int(match.group("generation"))
    source_repository_id = int(match.group("source"))
    target_text = match.group("target")
    target_repository_id = int(target_text) if target_text else None
    staging_text = match.group("staging")
    staging_pull_request = int(staging_text) if staging_text else None
    if generation > 2**31 - 1 or source_repository_id > 2**63 - 1:
        raise LifecycleFailure("代际或 source repositoryId 超出范围")
    if target_repository_id is not None and target_repository_id > 2**63 - 1:
        raise LifecycleFailure("target repositoryId 超出范围")
    if (operation == "transfer") != (target_repository_id is not None):
        raise LifecycleFailure("只有 transfer 命令必须且只能包含 target")
    if (operation == "purge") != (staging_pull_request is not None):
        raise LifecycleFailure("只有 purge 命令必须且只能包含 staging-pr")

    sections = issue_submission.parse_sections(str(event["issue"].get("body") or ""))
    body_operation = issue_submission.field(sections, "操作 / Operation").casefold()
    if body_operation != operation:
        raise LifecycleFailure("命令 operation 与 Issue 表单不一致")
    plugin_id = issue_submission.field(sections, "插件 ID / Plugin ID")
    if plugin_id != match.group("id"):
        raise LifecycleFailure("命令插件 ID 与 Issue 表单不一致")
    try:
        body_generation = int(
            issue_submission.field(sections, "当前代际 / Current generation")
        )
        body_source_repository_id = int(
            issue_submission.field(
                sections, "源数字仓库 ID / Source repository ID"
            )
        )
    except ValueError as exc:
        raise LifecycleFailure("Issue 代际和 source repositoryId 必须是十进制整数") from exc
    if body_generation != generation:
        raise LifecycleFailure("命令 generation 与 Issue 表单不一致")
    if body_source_repository_id != source_repository_id:
        raise LifecycleFailure("命令 source repositoryId 与 Issue 表单不一致")
    reason = issue_submission.field(sections, "原因 / Reason")
    try:
        reason_length = len(reason.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise LifecycleFailure("原因包含无效 Unicode") from exc
    if not 1 <= reason_length <= 1024:
        raise LifecycleFailure("原因必须为 1 到 1024 个 UTF-16 字符")
    target_url = sections.get("目标仓库 / Target repository", "").strip()
    if operation == "transfer" and not target_url:
        raise LifecycleFailure("transfer Issue 必须填写目标仓库")
    staging_issue_value = sections.get("Staging PR 编号 / Staging PR number", "").strip()
    if operation == "purge":
        try:
            body_staging_pull_request = int(staging_issue_value)
        except ValueError as exc:
            raise LifecycleFailure("purge Issue 必须填写十进制 Staging PR 编号") from exc
        if body_staging_pull_request != staging_pull_request:
            raise LifecycleFailure("命令 staging-pr 与 Issue 表单不一致")
    elif staging_issue_value and staging_issue_value.casefold() != "n/a":
        raise LifecycleFailure("只有 purge 可以填写 Staging PR 编号")
    return {
        "operation": operation,
        "pluginId": plugin_id,
        "generation": generation,
        "sourceRepositoryId": source_repository_id,
        "targetRepositoryId": target_repository_id,
        "targetRepositoryUrl": target_url,
        "stagingPullRequest": staging_pull_request,
        "reason": reason,
    }


def github_get(event: dict, path: str) -> object:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = event.get("repository")
    full_name = repository.get("full_name") if isinstance(repository, dict) else None
    if not token or not isinstance(full_name, str):
        raise LifecycleFailure("缺少 GITHUB_TOKEN 或 repository.full_name")
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "UNuse-Lifecycle/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read(MAXIMUM_API_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", errors="replace")
        raise LifecycleFailure(f"GitHub API {path} 失败：{exc.code} {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LifecycleFailure(f"GitHub API {path} 失败：{exc}") from exc
    if len(payload) > MAXIMUM_API_BYTES:
        raise LifecycleFailure("GitHub API 响应超过 8 MiB")
    try:
        return json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise LifecycleFailure("GitHub API 返回无效 JSON") from exc


def repository_by_id(
    event: dict, repository_id: int, *, allow_archived: bool = False
) -> dict:
    value = github_get(event, f"/repositories/{repository_id}")
    if not isinstance(value, dict):
        raise LifecycleFailure("GitHub repository API 返回无效对象")
    try:
        actual_id, owner_id, repository_url = validator.validate_github_repository_identity(
            value,
            value.get("html_url"),
            "lifecycle repository",
            allow_archived=allow_archived,
        )
    except validator.ValidationFailure as exc:
        raise LifecycleFailure(str(exc)) from exc
    if actual_id != repository_id:
        raise LifecycleFailure("GitHub repositoryId 响应不一致")
    owner = value.get("owner")
    owner_type = owner.get("type") if isinstance(owner, dict) else None
    owner_login = owner.get("login") if isinstance(owner, dict) else None
    return {
        "repositoryId": actual_id,
        "ownerId": owner_id,
        "repositoryUrl": repository_url,
        "ownerType": owner_type,
        "ownerLogin": owner_login,
    }


def expected_confirmation(request: dict) -> str:
    base = (
        f"/confirm-{request['operation']} {request['pluginId']}@g{request['generation']} "
        f"source:{request['sourceRepositoryId']}"
    )
    if request["operation"] == "transfer":
        base += f" target:{request['targetRepositoryId']}"
    return base


def fetch_issue_comments(event: dict) -> list[dict]:
    repository = event["repository"]["full_name"]
    issue_number = event["issue"].get("number")
    if type(issue_number) is not int or issue_number <= 0:
        raise LifecycleFailure("Issue number 无效")
    owner, name = repository.split("/", 1)
    result: list[dict] = []
    for page in range(1, MAXIMUM_COMMENT_PAGES + 1):
        path = (
            f"/repos/{urllib.parse.quote(owner, safe='')}/"
            f"{urllib.parse.quote(name, safe='')}/issues/{issue_number}/comments"
            f"?per_page=100&page={page}"
        )
        values = github_get(event, path)
        if not isinstance(values, list):
            raise LifecycleFailure("Issue comments API 返回无效数组")
        result.extend(item for item in values if isinstance(item, dict))
        if len(values) < 100:
            return result
    raise LifecycleFailure("Issue 评论超过 1000 条，拒绝无界查找作者确认")


def validate_confirmation_file(repository: dict, request: dict) -> str:
    owner, name, _ = validator.github_repository_parts(
        "source repository", "repositoryUrl", repository["repositoryUrl"]
    )
    url = (
        "https://raw.githubusercontent.com/"
        f"{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}/"
        "HEAD/_nyalauncher_lifecycle.json"
    )
    http_request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "UNuse-Lifecycle/1.0"},
    )
    try:
        with urllib.request.urlopen(http_request, timeout=30) as response:
            payload = response.read(1024 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LifecycleFailure(
            "组织仓库必须在默认分支根目录提供 _nyalauncher_lifecycle.json："
            f"{exc}"
        ) from exc
    if len(payload) > 1024 * 1024:
        raise LifecycleFailure("组织确认文件超过 1 MiB")
    try:
        value = validator.parse_json_object(payload.decode("utf-8"), url)
    except (UnicodeError, validator.ValidationFailure) as exc:
        raise LifecycleFailure(str(exc)) from exc
    required = {
        "schemaVersion": 1,
        "operation": request["operation"],
        "pluginId": request["pluginId"],
        "generation": request["generation"],
        "sourceRepositoryId": request["sourceRepositoryId"],
    }
    if request["operation"] == "transfer":
        required["targetRepositoryId"] = request["targetRepositoryId"]
    if value != required:
        raise LifecycleFailure("组织确认文件必须与本次生命周期事务字段完全一致")
    return hashlib.sha256(payload).hexdigest()


def require_author_confirmation(event: dict, request: dict, source_repository: dict) -> dict:
    if source_repository["ownerType"] == "Organization":
        digest = validate_confirmation_file(source_repository, request)
        return {"kind": "repository-file", "sha256": digest}
    confirmation = expected_confirmation(request)
    admin_comment_id = event["comment"]["id"]
    for comment in fetch_issue_comments(event):
        user = comment.get("user")
        if (
            comment.get("body") == confirmation
            and isinstance(user, dict)
            and user.get("id") == source_repository["ownerId"]
            and type(comment.get("id")) is int
            and comment["id"] < admin_comment_id
        ):
            return {
                "kind": "owner-comment",
                "ownerId": source_repository["ownerId"],
                "commentId": comment["id"],
            }
    raise LifecycleFailure(
        "缺少源仓库数字 owner 在同一 Issue 中、早于管理员操作的精确确认："
        f"{confirmation}"
    )


def source_release_path(plugin_id: str, generation: int, version: str) -> Path:
    root = ROOT / "plugins" / plugin_id
    if generation > 1:
        root = root / "generations" / f"g{generation}"
    return root / "releases" / f"{version}.json"


def source_review_path(plugin_id: str, generation: int, version: str) -> Path:
    root = ROOT / "reviews" / plugin_id
    if generation > 1:
        root = root / f"g{generation}"
    return root / f"{version}.json"


def yank_all_releases(
    plugin: dict,
    reason: str,
    actor: str,
    actor_id: int,
    timestamp: str,
    comment_id: int,
) -> None:
    for release in plugin["releases"]:
        generation = release.get("generation", 1)
        if generation != plugin["generation"] or release["yanked"]:
            # Older generations and existing yank/review tombstones are
            # permanent audit facts.  A later retire must never rewrite them.
            continue
        path = source_release_path(
            plugin["id"], generation, release["version"]
        )
        value = validator.load_object(path)
        if value.get("yanked") is not False:
            raise LifecycleFailure(
                f"{plugin['id']} g{generation}:{release['version']} source yank 状态与 catalog 不一致"
            )
        value["yanked"] = True
        value["yankReason"] = reason
        validator.write_text_atomic(path, validator.canonical_json(value))
        review_path = source_review_path(
            plugin["id"], generation, release["version"]
        )
        if review_path.exists():
            review = validator.load_object(review_path)
            if review.get("status") != "verified":
                continue
            review.update(
                {
                    "generation": generation,
                    "status": "revoked",
                    "stateBy": actor,
                    "stateById": actor_id,
                    "stateAt": timestamp,
                    "lastCommandAt": timestamp,
                    "lastCommentId": comment_id,
                    "notes": f"生命周期操作撤回：{reason}",
                }
            )
            validator.write_text_atomic(review_path, validator.canonical_json(review))


def git_output(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout


def public_history_ref() -> str:
    value = os.environ.get("NYA_LIFECYCLE_PUBLIC_REF", "main").strip()
    if (
        not value
        or value.startswith("-")
        or ".." in value
        or re.fullmatch(r"[A-Za-z0-9_./-]+", value) is None
    ):
        raise LifecycleFailure("NYA_LIFECYCLE_PUBLIC_REF 无效")
    return value


def validate_purge_staging(event: dict, request: dict) -> dict:
    """Bind purge to one App-owned, SHA-pinned, single-plugin staging PR."""

    pull_number = request["stagingPullRequest"]
    repository = event["repository"]["full_name"]
    value = github_get(event, f"/repos/{repository}/pulls/{pull_number}")
    if not isinstance(value, dict):
        raise LifecycleFailure("Staging PR API 返回无效对象")
    configuration, _ = validator.load_repository_configuration()
    base = value.get("base")
    head = value.get("head")
    author = value.get("user")
    head_repository = head.get("repo") if isinstance(head, dict) else None
    head_ref = head.get("ref") if isinstance(head, dict) else None
    head_sha = head.get("sha") if isinstance(head, dict) else None
    was_preclosed_by_workflow = (
        value.get("state") == "closed"
        and os.environ.get("NYA_PURGE_STAGING_CLOSED_PR") == str(pull_number)
        and os.environ.get("NYA_PURGE_STAGING_CLOSED_SHA") == head_sha
        and os.environ.get("NYA_PURGE_STAGING_CLOSED_HEAD_REF") == head_ref
        and isinstance(author, dict)
        and os.environ.get("NYA_PURGE_STAGING_AUTHOR_ID")
        == str(author.get("id"))
    )
    if (
        (value.get("state") != "open" and not was_preclosed_by_workflow)
        or not isinstance(base, dict)
        or base.get("ref") != "main"
        or not isinstance(head_repository, dict)
        or str(head_repository.get("full_name") or "").casefold()
        != repository.casefold()
        or head_ref != "registry-bot/sync"
        or not isinstance(author, dict)
        or str(author.get("login") or "").casefold()
        != configuration["registryBotLogin"].casefold()
        or author.get("type") != "Bot"
        or type(author.get("id")) is not int
        or not 1 <= author["id"] <= 2**63 - 1
        or not isinstance(head_sha, str)
        or re.fullmatch(r"[0-9a-f]{40}", head_sha) is None
    ):
        raise LifecycleFailure(
            "purge 只能绑定同仓 App 创建、base=main，且为 open 或已由受信工作流"
            "按 head SHA 预先关闭的 registry-bot/sync Staging PR"
        )
    if git_output("rev-parse", "HEAD").strip() != head_sha:
        raise LifecycleFailure("当前 staging worktree 与 Issue 绑定的 PR head SHA 不一致")

    public_ref = public_history_ref()
    identity_path = f"plugins/{request['pluginId']}/identity.json"
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{public_ref}:{identity_path}"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if exists.returncode == 0:
        raise LifecycleFailure("插件身份已经存在于 main，不是可 purge 的 staging 误收录")
    raw_paths = git_output("diff", "--name-only", "-z", f"{public_ref}...HEAD")
    changed_paths = [path for path in raw_paths.split("\0") if path]
    allowed_roots = {
        "plugins.json",
        "plugin_details.json",
        "public/v1/index.json",
        "public/v2/index.json",
    }
    plugin_prefix = f"plugins/{request['pluginId']}/"
    unexpected = [
        path
        for path in changed_paths
        if path not in allowed_roots and not path.startswith(plugin_prefix)
    ]
    if unexpected or identity_path not in changed_paths:
        raise LifecycleFailure(
            "purge Staging PR 必须只收录目标插件且包含新增 identity；"
            f"越界路径={unexpected[:8]}"
        )
    try:
        base_listings = json.loads(git_output("show", f"{public_ref}:plugins.json"))
    except (json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        raise LifecycleFailure("无法读取 main 的 active 指针基线") from exc
    current_listings = validator.load_plugin_list()
    if not isinstance(base_listings, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("id"), str)
        for item in base_listings
    ):
        raise LifecycleFailure("main 的 plugins.json 结构无效")
    base_by_id = {item["id"]: item for item in base_listings}
    current_by_id = {item["id"]: item for item in current_listings}
    expected_ids = {*base_by_id, request["pluginId"]}
    target_listing = current_by_id.get(request["pluginId"])
    if (
        set(current_by_id) != expected_ids
        or any(current_by_id.get(plugin_id) != listing for plugin_id, listing in base_by_id.items())
        or target_listing is None
        or target_listing.get("generation") != request["generation"]
        or target_listing.get("repositoryId") != request["sourceRepositoryId"]
    ):
        raise LifecycleFailure(
            "purge Staging PR 必须在 main 指针基线上只追加目标插件的精确 active 指针"
        )
    return {"number": pull_number, "headRef": head_ref, "headSha": head_sha}


def lineage_was_public(plugin_id: str, lineage_id: str) -> bool:
    paths = ("public/v1/index.json", "public/v2/index.json")
    revisions = git_output(
        "log", "--format=%H", public_history_ref(), "--", *paths
    ).splitlines()
    for revision in revisions:
        for path in paths:
            try:
                text = git_output("show", f"{revision}:{path}")
                index = validator.parse_json_object(text, f"{revision}:{path}")
            except (subprocess.CalledProcessError, validator.ValidationFailure):
                continue
            plugins = index.get("plugins")
            if not isinstance(plugins, list):
                continue
            for item in plugins:
                if not isinstance(item, dict) or item.get("id") != plugin_id:
                    continue
                recorded_lineage = item.get("lineageId")
                if recorded_lineage is None or recorded_lineage == lineage_id:
                    return True
    return False


def write_listings(listings: list[dict]) -> None:
    validator.write_text_atomic(
        ROOT / "plugins.json",
        validator.canonical_json(sorted(listings, key=lambda item: item["id"].casefold())),
    )


def append_audit_event(
    identity: dict,
    request: dict,
    actor: str,
    actor_id: int,
    timestamp: str,
    issue_number: int,
    comment_id: int,
    confirmation: dict,
    *,
    target_generation: int | None = None,
) -> None:
    event = {
        "operation": request["operation"],
        "generation": request["generation"],
        "sourceRepositoryId": request["sourceRepositoryId"],
        "actor": actor,
        "actorId": actor_id,
        "occurredAt": timestamp,
        "issueNumber": issue_number,
        "commentId": comment_id,
        "reason": request["reason"],
        "authorConfirmation": confirmation,
    }
    if request["operation"] == "transfer":
        event["targetGeneration"] = target_generation
        event["targetRepositoryId"] = request["targetRepositoryId"]
    identity.setdefault("events", []).append(event)


def apply(event: dict) -> str:
    request = parse_request(event)
    actor, actor_id, timestamp, comment_id = actor_identity(event)
    staging = (
        validate_purge_staging(event, request)
        if request["operation"] == "purge"
        else None
    )
    catalog = validator.load_catalog()
    plugin = next((item for item in catalog if item["id"] == request["pluginId"]), None)
    if plugin is None:
        raise LifecycleFailure("插件 ID 没有中心身份历史")
    if (
        plugin["generation"] != request["generation"]
        or plugin["publisher"]["repositoryId"] != request["sourceRepositoryId"]
    ):
        raise LifecycleFailure("expected generation/source repositoryId 已过期，拒绝重放")
    source_repository = repository_by_id(
        event, request["sourceRepositoryId"], allow_archived=True
    )
    if source_repository["ownerId"] != plugin["publisher"]["ownerId"]:
        raise LifecycleFailure("source repository ownerId 与永久绑定不一致")
    operation = request["operation"]
    issue_number = event["issue"].get("number")
    if type(issue_number) is not int or issue_number <= 0:
        raise LifecycleFailure("Issue number 无效")

    listings = validator.load_plugin_list()
    listing = next((item for item in listings if item["id"] == plugin["id"]), None)
    identity_path = ROOT / "plugins" / plugin["id"] / "identity.json"
    identity = validator.load_object(identity_path)

    if operation == "retire":
        confirmation = require_author_confirmation(event, request, source_repository)
        if plugin["lifecycleStatus"] == "retired":
            raise LifecycleFailure("插件已经 retired")
        yank_all_releases(
            plugin, request["reason"], actor, actor_id, timestamp, comment_id
        )
        identity["lifecycleStatus"] = "retired"
        identity["generations"][-1]["status"] = "retired"
        append_audit_event(
            identity,
            request,
            actor,
            actor_id,
            timestamp,
            issue_number,
            comment_id,
            confirmation,
        )
        validator.write_text_atomic(identity_path, validator.canonical_json(identity))
        write_listings([item for item in listings if item["id"] != plugin["id"]])
        return (
            f"retired {plugin['id']} g{plugin['generation']}；全部版本已撤回；"
            f"作者确认={confirmation['kind']}"
        )

    if operation == "transfer":
        if plugin["lifecycleStatus"] != "retired" or listing is not None:
            raise LifecycleFailure("转让前必须先完成 retired，并从 active 指针移除")
        if any(not release["yanked"] for release in plugin["releases"]):
            raise LifecycleFailure("转让前全部历史版本必须已撤回")
        target_repository = repository_by_id(event, request["targetRepositoryId"])
        try:
            _, _, target_url = validator.github_repository_parts(
                "transfer Issue", "targetRepositoryUrl", request["targetRepositoryUrl"]
            )
        except validator.ValidationFailure as exc:
            raise LifecycleFailure(str(exc)) from exc
        if not validator.same_github_repository(
            target_repository["repositoryUrl"], target_url
        ):
            raise LifecycleFailure("target URL 与命令 target repositoryId 不一致")
        for historical in catalog:
            for binding in historical["generations"]:
                if binding["repositoryId"] == request["targetRepositoryId"]:
                    raise LifecycleFailure("target repositoryId 已绑定其他代际")
        confirmation = require_author_confirmation(event, request, source_repository)
        next_generation = plugin["generation"] + 1
        temporary_listing = {
            "id": plugin["id"],
            "lineageId": plugin["lineageId"],
            "generation": next_generation,
            "repositoryUrl": target_repository["repositoryUrl"],
            "repositoryId": target_repository["repositoryId"],
            "ownerId": target_repository["ownerId"],
        }
        try:
            manifest = validator.fetch_repository_manifest(
                target_repository["repositoryUrl"], plugin["id"]
            )
            target_plugin, target_releases = validator.validate_publisher_manifest_releases(
                manifest, temporary_listing
            )
        except validator.ValidationFailure as exc:
            raise LifecycleFailure(str(exc)) from exc
        repository, _ = validator.load_repository_configuration()
        minimum_v2 = repository["v2MinimumLauncherVersion"]
        if any(
            validator.semver_key(release["compatibility"]["minimumLauncherVersion"])
            < validator.semver_key(minimum_v2)
            for release in target_releases
        ):
            raise LifecycleFailure(
                f"目标仓库所有 generation {next_generation} 版本必须要求启动器至少 {minimum_v2}"
            )
        generation_root = (
            ROOT / "plugins" / plugin["id"] / "generations" / f"g{next_generation}"
        )
        validator.write_text_atomic(
            generation_root / "plugin.json",
            validator.canonical_json(
                validator.public_plugin_to_source(target_plugin, next_generation)
            ),
        )
        (generation_root / "releases").mkdir(parents=True, exist_ok=True)
        identity["generation"] = next_generation
        identity["lifecycleStatus"] = "transferred"
        identity["generations"][-1]["status"] = "transferred"
        identity["generations"].append(
            {
                "generation": next_generation,
                "repositoryUrl": target_repository["repositoryUrl"],
                "repositoryUrlHistory": [target_repository["repositoryUrl"]],
                "repositoryId": target_repository["repositoryId"],
                "ownerId": target_repository["ownerId"],
                "status": "active",
            }
        )
        append_audit_event(
            identity,
            request,
            actor,
            actor_id,
            timestamp,
            issue_number,
            comment_id,
            confirmation,
            target_generation=next_generation,
        )
        validator.write_text_atomic(identity_path, validator.canonical_json(identity))
        write_listings([*listings, temporary_listing])
        return (
            f"transferred {plugin['id']} g{plugin['generation']} -> g{next_generation}；"
            f"作者确认={confirmation['kind']}；等待机器人验证新代 ZIP"
        )

    if staging is None:
        raise LifecycleFailure("purge 必须绑定尚未合并的 staging PR")
    reviews_root = ROOT / "reviews" / plugin["id"]
    if reviews_root.exists() and any(path.is_file() for path in reviews_root.rglob("*")):
        raise LifecycleFailure("存在 verified/revoked 审核墓碑，禁止 purge")
    if lineage_was_public(plugin["id"], plugin["lineageId"]):
        raise LifecycleFailure("该 lineage 曾进入公开索引，禁止 purge")
    tombstone = {
        "$schema": "../../schemas/purge-tombstone-v1.schema.json",
        "schemaVersion": 1,
        "id": plugin["id"],
        "lineageId": plugin["lineageId"],
        "generation": plugin["generation"],
        "repositoryId": plugin["publisher"]["repositoryId"],
        "ownerId": plugin["publisher"]["ownerId"],
        "purgedBy": actor,
        "purgedById": actor_id,
        "purgedAt": timestamp,
        "issueNumber": issue_number,
        "commentId": comment_id,
        "reason": request["reason"],
    }
    tombstone_path = (
        ROOT / "tombstones" / plugin["id"] / f"{plugin['lineageId']}.json"
    )
    validator.write_text_atomic(tombstone_path, validator.canonical_json(tombstone))
    plugin_root = ROOT / "plugins" / plugin["id"]
    resolved_plugin_root = plugin_root.resolve()
    resolved_plugins = (ROOT / "plugins").resolve()
    if resolved_plugin_root.parent != resolved_plugins:
        raise LifecycleFailure("purge 路径未通过边界检查")
    shutil.rmtree(resolved_plugin_root)
    write_listings([item for item in listings if item["id"] != plugin["id"]])
    return (
        f"purged 未公开误收录 {plugin['id']} from staging PR #{staging['number']}；"
        "永久 lineage tombstone 已保留"
    )


def main() -> int:
    global ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("apply", "describe"))
    parser.add_argument("--event", required=True, type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--registry-root", type=Path)
    args = parser.parse_args()
    try:
        if args.registry_root is not None:
            ROOT = args.registry_root.resolve()
            if not (ROOT / "repository.json").is_file():
                raise LifecycleFailure("--registry-root 不是插件中心 worktree")
            validator.ROOT = ROOT
        event = load_event(args.event)
        if args.action == "describe":
            request = parse_request(event)
            actor_identity(event)
            if args.github_output is None:
                raise LifecycleFailure("describe 必须提供 --github-output")
            with args.github_output.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(f"operation={request['operation']}\n")
                stream.write(
                    f"staging_pr={request['stagingPullRequest'] or ''}\n"
                )
            print(f"authorized lifecycle {request['operation']}")
            return 0
        summary = apply(event)
        if args.summary:
            args.summary.write_text(summary + "\n", encoding="utf-8", newline="\n")
        print(summary)
        return 0
    except (
        LifecycleFailure,
        validator.ValidationFailure,
        subprocess.CalledProcessError,
        OSError,
        UnicodeError,
    ) as exc:
        print(f"生命周期操作失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
