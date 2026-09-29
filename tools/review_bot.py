#!/usr/bin/env python3
"""Apply hash-bound administrator reviews from trusted GitHub comments.

This helper deliberately does not rebuild generated views, commit, or push.  A
base-owned workflow is expected to run it from ``main``, then run the registry
validator and publish the resulting changes with the isolated writer identity.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import validate as validator  # noqa: E402


COMMAND = re.compile(
    r"\A/(?P<action>verify|revoke-review) "
    r"(?P<target>\S+) sha256:(?P<sha256>[0-9a-f]{64})"
    r"(?: (?P<note>[^\r\n]+))?\Z"
)
MAXIMUM_COMMAND_UTF16 = 8192
MAXIMUM_NOTE_UTF16 = 4096
MAXIMUM_GITHUB_PAGE_BYTES = 8 * 1024 * 1024
MAXIMUM_GITHUB_COMMENT_PAGES = 10
GITHUB_REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/"
    r"[A-Za-z0-9_.-]{1,100}$"
)


class ReviewFailure(Exception):
    pass


class ReviewPermissionFailure(ReviewFailure):
    pass


@dataclasses.dataclass(frozen=True)
class ReviewCommand:
    action: str
    plugin_id: str
    generation: int
    version: str
    sha256: str
    note: str | None


@dataclasses.dataclass(frozen=True)
class ApplyResult:
    command: ReviewCommand
    actor: str
    changed: bool
    review_path: Path
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    note: str | None = None


def utf16_length(value: str) -> int:
    try:
        return len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise ReviewFailure("审核命令包含无效 Unicode") from exc


def load_event(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReviewFailure(f"无法读取 GitHub 事件：{exc}") from exc
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("issue"), dict)
        or not isinstance(value.get("comment"), dict)
    ):
        raise ReviewFailure("事件中没有有效的 Issue 评论")
    return value


def parse_command(body: object) -> ReviewCommand:
    if (
        not isinstance(body, str)
        or not body
        or utf16_length(body) > MAXIMUM_COMMAND_UTF16
    ):
        raise ReviewPermissionFailure("审核命令无效或过长")
    match = COMMAND.fullmatch(body)
    if match is None:
        raise ReviewPermissionFailure(
            "命令格式必须为 /verify id@generation:version sha256:<lower64> [说明]，"
            "或 /revoke-review id@generation:version sha256:<lower64> [原因]"
        )

    target = match.group("target")
    if target.count("@") != 1:
        raise ReviewPermissionFailure("审核目标必须为 id@version")
    plugin_id, generation_version = target.split("@", 1)
    if (
        len(plugin_id) > 128
        or validator.PLUGIN_ID.fullmatch(plugin_id) is None
    ):
        raise ReviewPermissionFailure("审核命令中的插件 ID 无效")
    generation = 1
    version = generation_version
    generation_match = re.fullmatch(r"g([1-9][0-9]*):(.+)", generation_version)
    if generation_match is not None:
        generation = int(generation_match.group(1))
        version = generation_match.group(2)
    if generation > 2**31 - 1:
        raise ReviewPermissionFailure("审核命令中的 generation 超过 Int32")
    if len(version) > 64 or validator.match_semver(version) is None:
        raise ReviewPermissionFailure("审核命令中的版本必须是严格 SemVer")

    note = match.group("note")
    if note is not None:
        if (
            note != note.strip()
            or utf16_length(note) > MAXIMUM_NOTE_UTF16
            or any(
                unicodedata.category(character) in {"Cc", "Cf", "Cs"}
                for character in note
            )
        ):
            raise ReviewPermissionFailure("审核说明包含无效字符或超过 4096 个字符")

    return ReviewCommand(
        action=match.group("action"),
        plugin_id=plugin_id,
        generation=generation,
        version=version,
        sha256=match.group("sha256"),
        note=note,
    )


def trusted_reviewers() -> set[str]:
    path = ROOT / "repository.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        reviewers = value["trustedReviewers"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ReviewFailure("无法读取 trustedReviewers") from exc
    if (
        not isinstance(reviewers, list)
        or not reviewers
        or any(
            not isinstance(item, str)
            or validator.GITHUB_LOGIN.fullmatch(item) is None
            for item in reviewers
        )
    ):
        raise ReviewFailure("trustedReviewers 配置无效")
    return {item.casefold() for item in reviewers}


def trusted_reviewer_ids() -> dict[str, int]:
    try:
        value = json.loads((ROOT / "repository.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ReviewFailure("无法读取 repository.json") from exc
    if not isinstance(value, dict):
        raise ReviewFailure("repository.json 配置无效")
    schema_version = value.get("schemaVersion")
    if type(schema_version) is not int or schema_version not in {1, 2}:
        raise ReviewFailure("repository.json schemaVersion 必须精确为整数 1 或 2")
    reviewer_ids = value.get("trustedReviewerIds")
    if reviewer_ids is None and schema_version == 1:
        anchor_path = ROOT / "migrations" / "v2-bootstrap.json"
        if not anchor_path.exists():
            return {}
        try:
            anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
            if not isinstance(anchor, dict):
                raise TypeError("bootstrap root must be an object")
            reviewer_ids = anchor["trustedReviewerIds"]
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ReviewFailure("无法读取 bootstrap trustedReviewerIds") from exc
    if not isinstance(reviewer_ids, dict):
        raise ReviewFailure("trustedReviewerIds 配置无效")
    result: dict[str, int] = {}
    for login, reviewer_id in reviewer_ids.items():
        if (
            not isinstance(login, str)
            or validator.GITHUB_LOGIN.fullmatch(login) is None
            or type(reviewer_id) is not int
            or not 1 <= reviewer_id <= 2**63 - 1
            or login.casefold() in result
        ):
            raise ReviewFailure("trustedReviewerIds 配置无效")
        result[login.casefold()] = reviewer_id
    return result


def numeric_reviewer_identity_required() -> bool:
    return validator.repository_schema_version() >= 2 or bool(
        trusted_reviewer_ids()
    )


def authorize_event(event: dict) -> tuple[ReviewCommand, str]:
    issue = event.get("issue")
    comment = event.get("comment")
    if not isinstance(issue, dict) or not str(issue.get("title") or "").startswith(
        "[Review]"
    ):
        raise ReviewPermissionFailure("人工审核命令只能用于 [Review] Issue")
    if not isinstance(comment, dict):
        raise ReviewPermissionFailure("事件中没有审核评论")

    command = parse_command(comment.get("body"))
    user = comment.get("user")
    actor = user.get("login") if isinstance(user, dict) else None
    actor_id = user.get("id") if isinstance(user, dict) else None
    actor_type = user.get("type") if isinstance(user, dict) else None
    reviewer_ids = trusted_reviewer_ids()
    requires_numeric_identity = numeric_reviewer_identity_required()
    if (
        not isinstance(actor, str)
        or validator.GITHUB_LOGIN.fullmatch(actor) is None
        or actor.casefold() not in trusted_reviewers()
        or (
            requires_numeric_identity
            and (
                type(actor_id) is not int
                or reviewer_ids.get(actor.casefold()) != actor_id
                or actor_type != "User"
            )
        )
    ):
        raise ReviewPermissionFailure("只有 trustedReviewers 可以执行人工审核命令")
    return command, actor


def review_position(event: dict) -> tuple[str, int]:
    comment = event.get("comment")
    created_at = comment.get("created_at") if isinstance(comment, dict) else None
    try:
        timestamp = validator.validate_utc_timestamp(
            "GitHub event", "comment.created_at", created_at
        )
    except validator.ValidationFailure as exc:
        raise ReviewFailure(str(exc)) from exc
    comment_id = comment.get("id") if isinstance(comment, dict) else None
    if type(comment_id) is not int or not (1 <= comment_id <= 2**63 - 1):
        raise ReviewFailure("GitHub event: comment.id 必须是正 Int64")
    return timestamp, comment_id


def fetch_issue_comments_since(event: dict, since: str) -> list[dict]:
    """Read a bounded suffix of all repository Issue comment streams.

    Review commands for one artifact may be issued from different Review
    Issues.  The repository-wide endpoint prevents an older verify in Issue A
    from overtaking a newer revoke in Issue B while that newer PR is pending.
    """

    repository = event.get("repository")
    full_name = repository.get("full_name") if isinstance(repository, dict) else None
    # Unit tests and intentional local dry-runs use compact event fixtures.
    # Real Actions events always carry repository.full_name; fail closed there.
    if GITHUB_REPOSITORY.fullmatch(str(full_name or "")) is None:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise ReviewFailure("GitHub 事件缺少可信仓库或 Issue 上下文")
        return []

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token and os.environ.get("GITHUB_ACTIONS") == "true":
        raise ReviewFailure("审核工作流缺少 GITHUB_TOKEN，无法检查更新的管理员命令")
    owner, repository_name = full_name.split("/", 1)
    comments: list[dict] = []
    for page in range(1, MAXIMUM_GITHUB_COMMENT_PAGES + 2):
        query = urllib.parse.urlencode(
            {
                "sort": "created",
                "direction": "asc",
                "since": since,
                "per_page": "100",
                "page": str(page),
            }
        )
        url = (
            "https://api.github.com/repos/"
            f"{urllib.parse.quote(owner, safe='')}/"
            f"{urllib.parse.quote(repository_name, safe='')}/issues/comments?{query}"
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "UNuse-Registry-Review/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read(MAXIMUM_GITHUB_PAGE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ReviewFailure(f"无法检查更新的管理员审核命令：{exc}") from exc
        if len(payload) > MAXIMUM_GITHUB_PAGE_BYTES:
            raise ReviewFailure("GitHub Issue 评论页超过 8 MiB")
        try:
            values = validator.parse_json_array(
                payload.decode("utf-8"), "GitHub Issue comments"
            )
        except (UnicodeError, validator.ValidationFailure) as exc:
            raise ReviewFailure(f"GitHub Issue 评论响应无效：{exc}") from exc
        if page > MAXIMUM_GITHUB_COMMENT_PAGES:
            if values:
                raise ReviewFailure("当前审核命令之后的 Issue 评论超过 1000 条，请管理员清理后重试")
            break
        comments.extend(value for value in values if isinstance(value, dict))
        if len(values) < 100:
            break
    return comments


def reject_superseded_verify(
    event: dict,
    command: ReviewCommand,
    current_position: tuple[str, int],
) -> None:
    """Never let an older green-mark command overtake a newer admin intent.

    Revocation itself remains safety-monotonic: if a newer verification fails,
    an older revoke may still remove a green mark.  Successfully merged newer
    commands are additionally protected by the durable review tombstone.
    """

    if command.action != "verify":
        return
    reviewers = trusted_reviewers()
    reviewer_ids = trusted_reviewer_ids()
    requires_numeric_identity = numeric_reviewer_identity_required()
    for comment in fetch_issue_comments_since(event, current_position[0]):
        user = comment.get("user")
        actor = user.get("login") if isinstance(user, dict) else None
        if not isinstance(actor, str) or actor.casefold() not in reviewers:
            continue
        if requires_numeric_identity and (
            user.get("type") != "User"
            or type(user.get("id")) is not int
            or reviewer_ids.get(actor.casefold()) != user.get("id")
        ):
            continue
        try:
            later_command = parse_command(comment.get("body"))
        except ReviewPermissionFailure:
            continue
        if (
            later_command.plugin_id != command.plugin_id
            or later_command.generation != command.generation
            or later_command.version != command.version
            or later_command.sha256 != command.sha256
        ):
            continue
        later_position = review_position({"comment": comment})
        if later_position > current_position:
            raise ReviewFailure(
                "该 verify 评论之后存在更新的可信管理员命令；拒绝写入陈旧绿色审核标志"
            )


def resolve_canonical_release(command: ReviewCommand) -> tuple[dict, dict]:
    plugin = next(
        (
            item
            for item in validator.load_catalog()
            if item["id"] == command.plugin_id
        ),
        None,
    )
    if plugin is None:
        raise ReviewFailure(f"插件尚未收录：{command.plugin_id}")
    release = next(
        (
            item
            for item in plugin["releases"]
            if item.get("generation", 1) == command.generation
            and item["version"] == command.version
        ),
        None,
    )
    if release is None:
        raise ReviewFailure(
            f"版本尚未收录：{command.plugin_id} g{command.generation}:{command.version}"
        )
    canonical_sha256 = release["download"]["sha256"]
    if command.sha256 != canonical_sha256:
        raise ReviewFailure(
            "命令 SHA-256 与中心固定 Release ZIP 的 canonical SHA-256 不一致"
        )
    return plugin, release


def checked_review_path(command: ReviewCommand) -> Path:
    reviews_root = ROOT / "reviews"
    directory = reviews_root / command.plugin_id
    generation_directory = (
        directory
        if command.generation == 1
        else directory / f"g{command.generation}"
    )
    path = generation_directory / f"{command.version}.json"
    if reviews_root.exists() and (
        not reviews_root.is_dir() or reviews_root.is_symlink()
    ):
        raise ReviewFailure("reviews/ 必须是普通目录")
    if directory.exists() and (not directory.is_dir() or directory.is_symlink()):
        raise ReviewFailure(f"reviews/{command.plugin_id} 必须是普通目录")
    if generation_directory.exists() and (
        not generation_directory.is_dir() or generation_directory.is_symlink()
    ):
        raise ReviewFailure("审核 generation 路径必须是普通目录")
    if path.exists() and (not path.is_file() or path.is_symlink()):
        raise ReviewFailure("审核记录必须是普通 JSON 文件")
    return path


def review_source_value(
    command: ReviewCommand,
    *,
    status: str,
    state_by: str,
    state_by_id: int | None,
    state_at: str,
    last_command_at: str,
    last_comment_id: int,
    note: str | None,
) -> dict:
    value = {
        "$schema": (
            "../../schemas/review-v1.schema.json"
            if command.generation == 1
            else "../../../schemas/review-v1.schema.json"
        ),
        "schemaVersion": 1,
        "pluginId": command.plugin_id,
        "version": command.version,
        "sha256": command.sha256,
        "status": status,
        "stateBy": state_by,
        "stateAt": state_at,
        "lastCommandAt": last_command_at,
        "lastCommentId": last_comment_id,
    }
    if validator.repository_schema_version() >= 2 or command.generation > 1:
        value["generation"] = command.generation
    if state_by_id is not None:
        value["stateById"] = state_by_id
    if note is not None:
        value["notes"] = note
    return value


def apply_review(event: dict) -> ApplyResult:
    command, actor = authorize_event(event)
    actor_id = trusted_reviewer_ids().get(actor.casefold())
    if type(actor_id) is not int and numeric_reviewer_identity_required():
        raise ReviewFailure("审核者缺少受保护数字身份")
    plugin, release = resolve_canonical_release(command)
    path = checked_review_path(command)
    reviewers = trusted_reviewers()
    command_at, comment_id = review_position(event)
    reject_superseded_verify(event, command, (command_at, comment_id))
    desired_status = "verified" if command.action == "verify" else "revoked"
    existing = None
    if path.exists():
        existing = validator.validate_review(
            path,
            command.plugin_id,
            command.version,
            command.sha256,
            reviewers,
            expected_generation=command.generation,
            trusted_reviewer_ids=trusted_reviewer_ids() or None,
        )
        current_position = (command_at, comment_id)
        stored_position = (
            existing["lastCommandAt"],
            existing["lastCommentId"],
        )
        if current_position < stored_position:
            raise ReviewFailure("该审核命令早于中心已处理的管理员命令，拒绝乱序覆盖")
        if current_position == stored_position and existing["status"] != desired_status:
            raise ReviewFailure("同一 GitHub 评论 ID 对应冲突的审核动作")

    if command.action == "verify":
        if release["yanked"]:
            raise ReviewFailure("已撤回版本不能获得管理员已审核标志")

        # Re-fetch and validate even on an idempotent retry.  A green marker
        # must never survive a replaced or otherwise unavailable Release ZIP.
        payload = validator.download_release_asset(plugin, release)
        validator.validate_runtime_package(plugin, release, payload)

    if existing is not None and existing["status"] == desired_status:
        state_by = existing["stateBy"]
        state_by_id = existing.get("stateById")
        state_at = existing["stateAt"]
        note = existing.get("notes")
    else:
        state_by = actor
        state_by_id = actor_id
        state_at = command_at
        note = command.note

    unchanged = bool(
        existing is not None
        and existing["status"] == desired_status
        and existing["lastCommandAt"] == command_at
        and existing["lastCommentId"] == comment_id
    )
    if not unchanged:
        value = review_source_value(
            command,
            status=desired_status,
            state_by=state_by,
            state_by_id=state_by_id,
            state_at=state_at,
            last_command_at=command_at,
            last_comment_id=comment_id,
            note=note,
        )
        validator.write_text_atomic(path, validator.canonical_json(value))
    return ApplyResult(
        command=command,
        actor=actor,
        changed=not unchanged,
        review_path=path,
        reviewed_by=state_by,
        reviewed_at=state_at,
        note=note,
    )


def summary_for(result: ApplyResult) -> str:
    command = result.command
    target = f"`{command.plugin_id}` `g{command.generation}:{command.version}`"
    if command.action == "verify":
        state = "已写入审核状态" if result.changed else "同一命令已处理，幂等完成"
        body = (
            "## 人工审核已确认\n\n"
            f"{target} 的固定 Release ZIP 已重新下载并通过完整包校验。\n\n"
            f"- SHA-256：`{command.sha256}`\n"
            f"- 审核者：@{result.reviewed_by}\n"
            f"- 审核时间：`{result.reviewed_at}`\n"
            f"- 结果：{state}"
        )
        if result.note is not None:
            body += f"\n- 说明：{result.note}"
        return body + "\n"

    state = "已记录撤销状态" if result.changed else "同一撤销命令已处理，幂等完成"
    body = (
        "## 管理员审核标志已撤销\n\n"
        f"{target}\n\n"
        f"- SHA-256：`{command.sha256}`\n"
        f"- 操作者：@{result.actor}\n"
        f"- 结果：{state}"
    )
    if result.note is not None:
        body += f"\n- 原因：{result.note}"
    return body + "\n"


def write_apply_artifacts(
    result: ApplyResult, summary_path: Path, github_output_path: Path
) -> None:
    summary_path.write_text(summary_for(result), encoding="utf-8", newline="\n")
    relative_path = result.review_path.relative_to(ROOT).as_posix()
    with github_output_path.open("a", encoding="utf-8", newline="\n") as output:
        output.write(f"action={result.command.action}\n")
        output.write(f"plugin_id={result.command.plugin_id}\n")
        if validator.repository_schema_version() >= 2:
            output.write(f"generation={result.command.generation}\n")
        output.write(f"version={result.command.version}\n")
        output.write(f"sha256={result.command.sha256}\n")
        output.write(f"review_path={relative_path}\n")
        output.write(f"changed={'true' if result.changed else 'false'}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    authorize_parser = subparsers.add_parser("authorize")
    authorize_parser.add_argument("--event", required=True, type=Path)
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--event", required=True, type=Path)
    apply_parser.add_argument("--summary", required=True, type=Path)
    apply_parser.add_argument("--github-output", required=True, type=Path)
    args = parser.parse_args()

    try:
        event = load_event(args.event)
        if args.command == "authorize":
            command, actor = authorize_event(event)
            print(
                f"可信审核者 {actor} 已获授权：{command.action} "
                f"{command.plugin_id}@g{command.generation}:{command.version}"
            )
            return 0

        result = apply_review(event)
        write_apply_artifacts(result, args.summary, args.github_output)
        print(summary_for(result), end="")
        return 0
    except (
        ReviewFailure,
        validator.ValidationFailure,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"人工审核处理失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
