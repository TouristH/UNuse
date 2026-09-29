#!/usr/bin/env python3
"""Discover and technically list UNuse plugins without reviewing them.

Discovery has two inputs, in priority order:

* open ``plugin-submission`` Issues in this registry (the fallback path), and
* public, non-fork, non-archived GitHub repositories carrying the fixed
  legacy discovery topic.

Successful candidates are passed to one ``validate.refresh_details`` call
together with all active publishers.  This deliberately shares the existing
global Release ZIP count/byte budgets and keeps ordinary version refreshes in
the same transaction.  A successful technical listing never creates an
administrator review.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import issue_submission  # noqa: E402
from tools import validate as validator  # noqa: E402


DISCOVERY_TOPIC = "nyalauncher-plugin"
MAXIMUM_SEARCH_RESULTS = 100
MAXIMUM_DISCOVERY_ATTEMPTS = 32
MAXIMUM_NEW_PLUGINS = 8
RESERVED_TOPIC_ATTEMPTS = MAXIMUM_DISCOVERY_ATTEMPTS // 2
RESERVED_TOPIC_PLUGIN_SLOTS = MAXIMUM_NEW_PLUGINS // 2
MAXIMUM_RECONCILIATION_OLDER_PAGES = 32
MAXIMUM_API_RESPONSE_BYTES = 8 * 1024 * 1024
GITHUB_FULL_NAME = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}$"
)


class RegistryBotFailure(Exception):
    """A discovery input or GitHub API response is unusable."""


class RegistryBotRetryableFailure(RegistryBotFailure):
    """GitHub was temporarily unavailable; keep the candidate queued."""


@dataclass(frozen=True)
class Candidate:
    source: str
    repository_url: str = ""
    issue_number: int | None = None
    claimed_id: str | None = None
    issue: dict | None = None
    repository_id: int | None = None
    owner_id: int | None = None


ApiGetter = Callable[[str], object]


def _bounded_reason(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return text[:2000] or "未知错误"


def _result_item(candidate: Candidate, **extra: object) -> dict:
    value: dict[str, object] = {
        "source": candidate.source,
        "issueNumber": candidate.issue_number,
        "repositoryUrl": candidate.repository_url,
    }
    value.update(extra)
    return value


def write_results(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    validator.write_text_atomic(path, validator.canonical_json(result))


def github_get(path: str) -> object:
    """Make one bounded GitHub REST GET using the workflow token when present."""

    if not isinstance(path, str) or not path.startswith("/") or "\r" in path or "\n" in path:
        raise RegistryBotFailure("GitHub API 路径无效")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "UNuse-Registry-Bot/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read(MAXIMUM_API_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(1000).decode("utf-8", errors="replace")
        except (AttributeError, OSError, http.client.HTTPException):
            detail = str(exc.reason)
        failure = (
            RegistryBotRetryableFailure
            if validator.is_retryable_http_error(exc)
            else RegistryBotFailure
        )
        raise failure(
            f"GitHub API GET {path} 失败：{exc.code} {_bounded_reason(detail)}"
        ) from exc
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as exc:
        raise RegistryBotRetryableFailure(
            f"GitHub API GET {path} 失败：{exc}"
        ) from exc
    if len(payload) > MAXIMUM_API_RESPONSE_BYTES:
        raise RegistryBotFailure("GitHub API 响应超过 8 MiB")
    try:
        return json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RegistryBotFailure("GitHub API 返回无效 JSON") from exc


def registry_full_name() -> str:
    value = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if GITHUB_FULL_NAME.fullmatch(value) is None:
        raise RegistryBotFailure("GITHUB_REPOSITORY 必须是 owner/repository")
    return value


def refresh_offset() -> int:
    raw_offset = os.environ.get("NYA_REFRESH_OFFSET", "0")
    try:
        return int(raw_offset, 10)
    except ValueError as exc:
        raise RegistryBotFailure("NYA_REFRESH_OFFSET 必须是整数") from exc


def collect_issue_candidates(api_get: ApiGetter = github_get) -> list[Candidate]:
    full_name = registry_full_name()
    owner, repository = full_name.split("/", 1)
    path = (
        f"/repos/{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(repository, safe='')}/issues?"
        "state=open&labels=plugin-submission&sort=created&direction=asc&per_page=100"
    )
    value = api_get(path)
    if not isinstance(value, list):
        raise RegistryBotFailure("GitHub Issues API 返回的根必须是数组")
    candidates: list[Candidate] = []
    for issue in value[:MAXIMUM_SEARCH_RESULTS]:
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        number = issue.get("number")
        title = issue.get("title")
        state = issue.get("state")
        labels = issue.get("labels")
        label_names = {
            item.get("name", "").casefold()
            for item in labels
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        } if isinstance(labels, list) else set()
        if (
            type(number) is not int
            or number <= 0
            or not isinstance(title, str)
            or not title.startswith("[Plugin]")
            or state != "open"
            or "plugin-submission" not in label_names
        ):
            continue
        sections = issue_submission.parse_sections(str(issue.get("body") or ""))
        raw_repository = sections.get("仓库地址 / Repository URL", "").strip()
        candidates.append(
            Candidate(
                source="issue",
                repository_url=raw_repository,
                issue_number=number,
                issue=issue,
            )
        )
    return candidates


def collect_reconciled_issues(
    active_listings: list[dict],
    catalog: list[dict],
    api_get: ApiGetter = github_get,
) -> list[dict]:
    """Find fallback Issues whose App PR reached main after a workflow timeout."""

    full_name = registry_full_name()
    owner, repository = full_name.split("/", 1)
    prefix = (
        f"/repos/{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(repository, safe='')}/issues?"
    )
    queries = (
        "state=all&labels=pending-merge&sort=updated&direction=desc&per_page=100",
        "state=closed&labels=plugin-submission&sort=updated&direction=desc&per_page=100",
        "state=closed&labels=queued-for-intake&sort=updated&direction=desc&per_page=100",
    )
    issues_by_number: dict[int, dict] = {}
    for query in queries:
        first_page = api_get(prefix + query + "&page=1")
        if not isinstance(first_page, list):
            raise RegistryBotFailure("GitHub reconciliation Issues API 返回的根必须是数组")
        pages = [first_page]
        if len(first_page) >= MAXIMUM_SEARCH_RESULTS:
            older_page = 2 + (
                refresh_offset() % MAXIMUM_RECONCILIATION_OLDER_PAGES
            )
            older = api_get(prefix + query + f"&page={older_page}")
            if not isinstance(older, list):
                raise RegistryBotFailure(
                    "GitHub reconciliation Issues API 返回的根必须是数组"
                )
            pages.append(older)
        for page in pages:
            for issue in page[:MAXIMUM_SEARCH_RESULTS]:
                if not isinstance(issue, dict) or "pull_request" in issue:
                    continue
                number = issue.get("number")
                if type(number) is int and number > 0:
                    issues_by_number.setdefault(number, issue)

    active_by_id = {item["id"].casefold(): item for item in active_listings}
    history_by_id = {item["id"].casefold(): item for item in catalog}
    result: list[dict] = []
    for number, issue in sorted(issues_by_number.items()):
        labels = issue.get("labels")
        label_names = {
            item.get("name", "").casefold()
            for item in labels
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        } if isinstance(labels, list) else set()
        is_pending = "pending-merge" in label_names
        is_closed_fallback = issue.get("state") == "closed" and bool(
            {"plugin-submission", "queued-for-intake"} & label_names
        )
        if not is_pending and not is_closed_fallback:
            continue
        try:
            listing = issue_submission.parse_add_listing({"issue": issue})
        except (issue_submission.SubmissionFailure, validator.ValidationFailure):
            continue
        active = active_by_id.get(listing["id"].casefold())
        history = history_by_id.get(listing["id"].casefold())
        if active is None or history is None:
            continue
        if not (
            validator.same_github_repository(
                active["repositoryUrl"], listing["repositoryUrl"]
            )
            and validator.same_github_repository(
                history["repositoryUrl"], listing["repositoryUrl"]
            )
            and any(release.get("yanked") is False for release in history["releases"])
        ):
            continue
        result.append({"issueNumber": number, "id": active["id"]})
    return result


def collect_topic_candidates(api_get: ApiGetter = github_get) -> tuple[list[Candidate], list[str]]:
    query = f"topic:{DISCOVERY_TOPIC} is:public archived:false fork:false"
    path = "/search/repositories?" + urllib.parse.urlencode(
        {
            "q": query,
            "sort": "updated",
            "order": "desc",
            "per_page": str(MAXIMUM_SEARCH_RESULTS),
            "page": "1",
        }
    )
    value = api_get(path)
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise RegistryBotFailure("GitHub Repository Search 返回无效结构")
    warnings: list[str] = []
    if value.get("incomplete_results") is True:
        warnings.append("GitHub Topic 搜索结果不完整；本轮忽略 Topic 候选，Issue 兜底仍可用")
        return [], warnings
    total_count = value.get("total_count")
    if type(total_count) is int and total_count > MAXIMUM_SEARCH_RESULTS:
        warnings.append(
            f"Topic 搜索命中 {total_count} 个仓库，本轮只检查最近更新的 "
            f"{MAXIMUM_SEARCH_RESULTS} 个；其余请使用 Issue 兜底"
        )
    candidates: list[Candidate] = []
    for repository in value["items"][:MAXIMUM_SEARCH_RESULTS]:
        if not isinstance(repository, dict):
            continue
        topics = repository.get("topics")
        topic_names = {
            item.casefold() for item in topics if isinstance(item, str)
        } if isinstance(topics, list) else set()
        if (
            repository.get("private") is not False
            or repository.get("fork") is not False
            or repository.get("archived") is not False
            or repository.get("disabled") is True
            or (
                repository.get("visibility") is not None
                and repository.get("visibility") != "public"
            )
            or DISCOVERY_TOPIC not in topic_names
        ):
            continue
        html_url = repository.get("html_url")
        if not isinstance(html_url, str):
            continue
        try:
            repository_id, owner_id, canonical_url = (
                validator.validate_github_repository_identity(
                    repository, html_url, "GitHub Topic search"
                )
            )
        except validator.ValidationFailure:
            continue
        candidates.append(
            Candidate(
                source="topic",
                repository_url=canonical_url,
                repository_id=repository_id,
                owner_id=owner_id,
            )
        )
    return candidates, warnings


def _parse_issue_listing(candidate: Candidate) -> dict:
    if candidate.issue is None:
        raise RegistryBotFailure("Issue 候选缺少 Issue 内容")
    try:
        return issue_submission.parse_add_listing({"issue": candidate.issue})
    except (issue_submission.SubmissionFailure, validator.ValidationFailure) as exc:
        raise RegistryBotFailure(str(exc)) from exc


def _canonical_repository(value: str) -> tuple[str, str]:
    try:
        owner, _, repository_url = validator.github_repository_parts(
            "discovery candidate", "repositoryUrl", value
        )
    except validator.ValidationFailure as exc:
        raise RegistryBotFailure(str(exc)) from exc
    return owner, repository_url


def _namespace_prefix(owner: str) -> str:
    return f"io.github.{owner.casefold()}."


def resolve_repository_identity(
    candidate: Candidate,
    repository_url: str,
    api_get: ApiGetter,
) -> tuple[int, int, str]:
    if candidate.repository_id is not None or candidate.owner_id is not None:
        if (
            type(candidate.repository_id) is not int
            or not 1 <= candidate.repository_id <= 2**63 - 1
            or type(candidate.owner_id) is not int
            or not 1 <= candidate.owner_id <= 2**63 - 1
        ):
            raise RegistryBotFailure("GitHub numeric repository identity 无效")
        return candidate.repository_id, candidate.owner_id, repository_url
    owner, repository, _ = validator.github_repository_parts(
        "discovery candidate", "repositoryUrl", repository_url
    )
    value = api_get(
        f"/repos/{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(repository, safe='')}"
    )
    try:
        return validator.validate_github_repository_identity(
            value, repository_url, "discovery candidate"
        )
    except validator.ValidationFailure as exc:
        raise RegistryBotFailure(str(exc)) from exc


def _catalog_repository_map(catalog: list[dict]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for plugin in catalog:
        for binding in plugin.get("generations", []):
            for repository_url in binding.get(
                "repositoryUrlHistory", [binding.get("repositoryUrl")]
            ):
                if isinstance(repository_url, str):
                    result[repository_url.casefold()] = plugin
    return result


def _catalog_repository_id_map(catalog: list[dict]) -> dict[int, dict]:
    result: dict[int, dict] = {}
    for plugin in catalog:
        for binding in plugin.get("generations", []):
            repository_id = binding.get("repositoryId")
            if type(repository_id) is int:
                result[repository_id] = plugin
    return result


def _rotate_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Rotate bounded queues so unchanged invalid entries cannot starve peers."""

    if not candidates:
        return []
    run_offset = refresh_offset()
    # Advancing by one guarantees that every queue position eventually leads,
    # including queue lengths that share factors with the per-run attempt cap.
    offset = run_offset % len(candidates)
    return candidates[offset:] + candidates[:offset]


def _has_new_usable_history(before: dict | None, after: dict | None) -> bool:
    if after is None:
        return False
    before_versions = (
        {
            (release.get("generation", 1), release["version"])
            for release in before["releases"]
        }
        if before is not None
        else set()
    )
    new_releases = [
        release
        for release in after["releases"]
        if (release.get("generation", 1), release["version"]) not in before_versions
    ]
    return bool(new_releases and any(not release["yanked"] for release in new_releases))


def _has_verified_repository_rename(before: dict | None, after: dict | None) -> bool:
    if before is None or after is None:
        return False
    return bool(
        before.get("lineageId") == after.get("lineageId")
        and before.get("generation") == after.get("generation")
        and before.get("publisher") == after.get("publisher")
        and before.get("repositoryUrl") != after.get("repositoryUrl")
    )


def _write_generated_views(listings: list[dict]) -> None:
    validator.write_text_atomic(
        validator.ROOT / "plugins.json",
        validator.canonical_json(listings),
    )
    details = validator.build_details()
    index = validator.build_index(details)
    index_v2 = validator.build_index_v2(details)
    validator.write_text_atomic(
        validator.ROOT / "plugin_details.json",
        validator.render(details),
    )
    validator.write_text_atomic(
        validator.ROOT / "public" / "v1" / "index.json",
        validator.render(index),
    )
    validator.write_text_atomic(
        validator.ROOT / "public" / "v2" / "index.json",
        validator.render(index_v2),
    )


def collect(*, write: bool, api_get: ApiGetter = github_get) -> dict:
    """Collect candidates and run one globally-budgeted publisher refresh."""

    result: dict[str, list] = {
        "accepted": [],
        "rejected": [],
        "deferred": [],
        "reconciled": [],
        "warnings": [],
    }
    if validator.repository_schema_version() < 2:
        result["warnings"].append(
            "identity-aware registry migration 尚未完成；本轮安全跳过所有发现与写入"
        )
        return result
    try:
        issue_candidates = collect_issue_candidates(api_get)
    except RegistryBotFailure as exc:
        issue_candidates = []
        result["warnings"].append(_bounded_reason(exc))
    try:
        topic_candidates, topic_warnings = collect_topic_candidates(api_get)
        result["warnings"].extend(topic_warnings)
    except RegistryBotFailure as exc:
        topic_candidates = []
        result["warnings"].append(_bounded_reason(exc))

    issue_candidates = _rotate_candidates(issue_candidates)
    topic_candidates = _rotate_candidates(topic_candidates)

    active_listings = validator.load_plugin_list()
    catalog = validator.load_catalog()
    try:
        result["reconciled"] = collect_reconciled_issues(
            active_listings, catalog, api_get
        )
    except RegistryBotFailure as exc:
        result["warnings"].append(_bounded_reason(exc))
    active_by_id = {item["id"].casefold(): item for item in active_listings}
    active_by_repository = {
        item["repositoryUrl"].casefold(): item for item in active_listings
    }
    active_by_repository_id = {
        item["repositoryId"]: item
        for item in active_listings
        if type(item.get("repositoryId")) is int
    }
    history_by_id = {item["id"].casefold(): item for item in catalog}
    history_by_repository_id = _catalog_repository_id_map(catalog)
    seen_repositories: set[str] = set()
    prepared: list[tuple[Candidate, dict]] = []
    prepared_by_id: dict[str, tuple[Candidate, dict]] = {}
    conflicted_ids: set[str] = set()
    attempts = 0
    issue_attempts = 0
    issue_prepared = 0
    reserve_for_topics = bool(topic_candidates)
    issue_attempt_limit = (
        MAXIMUM_DISCOVERY_ATTEMPTS - RESERVED_TOPIC_ATTEMPTS
        if reserve_for_topics
        else MAXIMUM_DISCOVERY_ATTEMPTS
    )
    issue_plugin_limit = (
        MAXIMUM_NEW_PLUGINS - RESERVED_TOPIC_PLUGIN_SLOTS
        if reserve_for_topics
        else MAXIMUM_NEW_PLUGINS
    )
    issue_budget_warning_emitted = False

    # Issue fallback is intentionally first.  No API event directly downloads
    # a package; only this bounded collector performs the heavy work.
    for raw_candidate in [*issue_candidates, *topic_candidates]:
        if attempts >= MAXIMUM_DISCOVERY_ATTEMPTS or len(prepared) >= MAXIMUM_NEW_PLUGINS:
            break
        if raw_candidate.source == "issue" and (
            issue_attempts >= issue_attempt_limit
            or issue_prepared >= issue_plugin_limit
        ):
            if not issue_budget_warning_emitted:
                result["warnings"].append(
                    "Issue 候选已使用本轮保留份额；为防止 Issue 洪泛饿死 Topic 发现，"
                    "其余 Issue 延后"
                )
                issue_budget_warning_emitted = True
            continue
        attempts += 1
        if raw_candidate.source == "issue":
            issue_attempts += 1
        candidate = raw_candidate
        try:
            if candidate.source == "issue":
                issue_listing = _parse_issue_listing(candidate)
                owner, repository_url = _canonical_repository(issue_listing["repositoryUrl"])
                claimed_id = issue_listing["id"]
            else:
                owner, repository_url = _canonical_repository(candidate.repository_url)
                claimed_id = None
            repository_id, owner_id, repository_url = resolve_repository_identity(
                candidate, repository_url, api_get
            )
            owner, repository_url = _canonical_repository(repository_url)
            candidate = Candidate(
                source=candidate.source,
                repository_url=repository_url,
                issue_number=candidate.issue_number,
                claimed_id=claimed_id,
                issue=candidate.issue,
                repository_id=repository_id,
                owner_id=owner_id,
            )
            repository_key = repository_url.casefold()
            existing_numeric_repository = active_by_repository_id.get(repository_id)
            renamed_active_repository = None
            if (
                existing_numeric_repository is not None
                and existing_numeric_repository["repositoryUrl"].casefold()
                != repository_key
            ):
                if existing_numeric_repository.get("ownerId") != owner_id:
                    raise RegistryBotFailure(
                        "检测到 GitHub 原生 ownership transfer（repositoryId 未变但 ownerId 已变）；"
                        "注册表不接受同代静默换主。请先把仓库转回已绑定 owner，另建一个"
                        "不同 repositoryId 的目标仓库，再执行管理员 lifecycle transfer"
                    )
                renamed_active_repository = existing_numeric_repository
            if repository_key in seen_repositories:
                if candidate.source == "issue":
                    result["rejected"].append(
                        _result_item(candidate, id=claimed_id, reason="同一仓库在本轮重复提交")
                    )
                continue
            seen_repositories.add(repository_key)

            existing_active_repository = active_by_repository.get(repository_key)
            if existing_active_repository is not None:
                if (
                    existing_active_repository.get("repositoryId", repository_id)
                    != repository_id
                    or existing_active_repository.get("ownerId", owner_id) != owner_id
                ):
                    raise RegistryBotFailure(
                        "GitHub numeric repository identity 与中心记录不一致"
                    )
                if claimed_id is not None and claimed_id.casefold() != existing_active_repository["id"].casefold():
                    raise RegistryBotFailure(
                        f"仓库已作为 {existing_active_repository['id']} 收录"
                    )
                # Idempotent discovery must never remove or duplicate an active pointer.
                # It is not a failed candidate and must not let a large active
                # registry exhaust the separate discovery-attempt budget.
                if candidate.source == "issue":
                    result["accepted"].append(
                        _result_item(
                            candidate,
                            id=existing_active_repository["id"],
                            alreadyListed=True,
                        )
                    )
                attempts -= 1
                if candidate.source == "issue":
                    issue_attempts -= 1
                continue

            try:
                manifest = validator.fetch_repository_manifest(repository_url, repository_url)
            except validator.AvailabilityFailure as exc:
                raise RegistryBotRetryableFailure(str(exc)) from exc
            except validator.ValidationFailure as exc:
                raise RegistryBotFailure(str(exc)) from exc
            manifest_id = manifest.get("id")
            if not isinstance(manifest_id, str):
                raise RegistryBotFailure("_manifest.json 缺少字符串 id")
            if claimed_id is not None and manifest_id != claimed_id:
                raise RegistryBotFailure("Issue 插件 ID 与 _manifest.json id 不一致")
            candidate = Candidate(
                source=candidate.source,
                repository_url=repository_url,
                issue_number=candidate.issue_number,
                claimed_id=manifest_id,
                issue=candidate.issue,
                repository_id=repository_id,
                owner_id=owner_id,
            )
            id_key = manifest_id.casefold()
            historical_id = history_by_id.get(id_key)
            # Namespace ownership is a first-listing anti-squat rule.  A
            # previously bound numeric repository/owner may have been renamed;
            # its durable identity is checked below and must not be replaced by
            # a mutable login/path prefix comparison.
            if historical_id is None and not manifest_id.startswith(
                _namespace_prefix(owner)
            ):
                raise RegistryBotFailure(
                    f"自动收录 ID 必须以 {_namespace_prefix(owner)} 开头"
                )
            listing = {
                "id": manifest_id,
                "lineageId": str(uuid.uuid4()),
                "generation": 1,
                "repositoryUrl": repository_url,
                "repositoryId": repository_id,
                "ownerId": owner_id,
            }
            historical_generations = (
                historical_id.get("generations")
                if historical_id is not None
                else None
            )
            historical_binding = (
                historical_generations[-1]
                if isinstance(historical_generations, list)
                and historical_generations
                else None
            )
            same_historical_numeric_publisher = bool(
                historical_binding is not None
                and historical_binding.get("repositoryId") == repository_id
                and historical_binding.get("ownerId") == owner_id
            )
            if renamed_active_repository is not None:
                if (
                    manifest_id.casefold()
                    != renamed_active_repository["id"].casefold()
                ):
                    raise RegistryBotFailure(
                        f"重命名仓库必须继续声明原插件 ID {renamed_active_repository['id']}"
                    )
                existing_history = history_by_id.get(manifest_id.casefold())
                if existing_history is None:
                    raise RegistryBotFailure("重命名 active 指针缺少中心身份历史")
                listing["lineageId"] = existing_history["lineageId"]
                listing["generation"] = existing_history["generation"]
                current_binding = existing_history["generations"][-1]
            elif same_historical_numeric_publisher:
                listing["lineageId"] = historical_id["lineageId"]
                listing["generation"] = historical_id["generation"]
                current_binding = historical_binding
            else:
                current_binding = None
            if current_binding is not None:
                repository_history = list(
                    current_binding.get(
                        "repositoryUrlHistory", [current_binding["repositoryUrl"]]
                    )
                )
                if repository_url != current_binding["repositoryUrl"]:
                    if repository_url.casefold() in {
                        item.casefold() for item in repository_history
                    }:
                        if repository_url.casefold() != current_binding[
                            "repositoryUrl"
                        ].casefold():
                            raise RegistryBotFailure(
                                "仓库不能把 canonical URL 回滚到本代旧 alias"
                            )
                        repository_url = current_binding["repositoryUrl"]
                        listing["repositoryUrl"] = repository_url
                    else:
                        repository_history.append(repository_url)
                validation_listing = {
                    **listing,
                    "repositoryUrlHistory": repository_history,
                }
            else:
                validation_listing = listing
            try:
                validator.validate_publisher_manifest_releases(
                    manifest, validation_listing
                )
            except validator.ValidationFailure as exc:
                raise RegistryBotFailure(str(exc)) from exc

            existing_active_id = active_by_id.get(id_key)
            if (
                existing_active_id is not None
                and renamed_active_repository is None
            ):
                raise RegistryBotFailure(
                    f"插件 ID 已由 {existing_active_id['repositoryUrl']} 收录"
                )
            if historical_id is not None and id_key not in active_by_id:
                historical_publisher = historical_id.get("publisher", {})
                if (
                    historical_id.get("lifecycleStatus") != "retired"
                    or historical_publisher.get("repositoryId") != repository_id
                    or historical_publisher.get("ownerId") != owner_id
                ):
                    raise RegistryBotFailure(
                        "归档 ID 只能由同一 repositoryId 与 ownerId 安全重新激活；"
                        "旧记录缺少 GitHub numeric identity 时必须先由管理员迁移；"
                        "转让必须使用不同 repositoryId 的目标仓库走管理员 lifecycle 流程；"
                        "已做 GitHub 原生 ownership transfer 时请先转回原 owner"
                    )
                listing["lineageId"] = historical_id["lineageId"]
                listing["generation"] = historical_id["generation"]
            historical_numeric_repository = history_by_repository_id.get(repository_id)
            if (
                historical_numeric_repository is not None
                and historical_numeric_repository["id"].casefold() != id_key
            ):
                raise RegistryBotFailure(
                    f"数字仓库已有其他插件历史：{historical_numeric_repository['id']}"
                )
            if id_key in conflicted_ids:
                raise RegistryBotFailure("本轮发现多个不同仓库声明同一插件 ID，已全部拒绝")
            previous = prepared_by_id.get(id_key)
            if previous is not None:
                previous_candidate, previous_listing = previous
                prepared.remove(previous)
                del prepared_by_id[id_key]
                conflicted_ids.add(id_key)
                if previous_candidate.source == "issue":
                    issue_prepared -= 1
                conflict_reason = "本轮发现多个不同仓库声明同一插件 ID，已全部拒绝"
                result["rejected"].append(
                    _result_item(
                        previous_candidate,
                        id=previous_listing["id"],
                        reason=conflict_reason,
                    )
                )
                raise RegistryBotFailure(conflict_reason)
            prepared.append((candidate, listing))
            prepared_by_id[id_key] = (candidate, listing)
            if candidate.source == "issue":
                issue_prepared += 1
        except (RegistryBotRetryableFailure, validator.AvailabilityFailure) as exc:
            result["deferred"].append(
                _result_item(
                    candidate,
                    id=candidate.claimed_id,
                    reason=_bounded_reason(exc),
                )
            )
        except (RegistryBotFailure, validator.ValidationFailure) as exc:
            result["rejected"].append(
                _result_item(
                    candidate,
                    id=candidate.claimed_id,
                    reason=_bounded_reason(exc),
                )
            )

    if attempts >= MAXIMUM_DISCOVERY_ATTEMPTS and len(prepared) < MAXIMUM_NEW_PLUGINS:
        result["warnings"].append(
            f"发现候选尝试达到每轮上限 {MAXIMUM_DISCOVERY_ATTEMPTS}；其余候选延后"
        )
    if len(prepared) >= MAXIMUM_NEW_PLUGINS and len(issue_candidates) + len(topic_candidates) > attempts:
        result["warnings"].append(
            f"待验证新插件达到每轮上限 {MAXIMUM_NEW_PLUGINS}；其余候选延后"
        )

    prepared_ids = {listing["id"].casefold() for _, listing in prepared}
    proposed_listings = [
        *(item for item in active_listings if item["id"].casefold() not in prepared_ids),
        *(listing for _, listing in prepared),
    ]
    proposed_listings.sort(key=lambda item: item["id"].casefold())
    refresh_warnings: list[str] = []
    retryable_refresh_failures: set[str] = set()
    refreshed = validator.refresh_details(
        proposed_listings,
        validator.build_details(),
        write=write,
        best_effort=True,
        warnings=refresh_warnings,
        priority_ids=[listing["id"] for _, listing in prepared],
        retryable_failures=retryable_refresh_failures,
    )
    result["warnings"].extend(refresh_warnings)
    before_by_id = {plugin["id"].casefold(): plugin for plugin in catalog}
    after_by_id = {plugin["id"].casefold(): plugin for plugin in refreshed}
    accepted_listings: list[dict] = []
    for candidate, listing in prepared:
        key = listing["id"].casefold()
        if (
            _has_new_usable_history(before_by_id.get(key), after_by_id.get(key))
            or _has_verified_repository_rename(
                before_by_id.get(key), after_by_id.get(key)
            )
        ):
            accepted_listings.append(listing)
            result["accepted"].append(
                _result_item(candidate, id=listing["id"])
            )
            continue
        matching_warning = next(
            (
                warning
                for warning in refresh_warnings
                if warning.startswith(f"{listing['id']}：")
            ),
            None,
        )
        item = _result_item(
            candidate,
            id=listing["id"],
            reason=_bounded_reason(
                matching_warning or "本轮全局 ZIP 或发布者预算已用尽，候选保留到后续轮次"
            ),
        )
        if key in retryable_refresh_failures:
            result["deferred"].append(item)
        elif matching_warning:
            result["rejected"].append(item)
        else:
            result["deferred"].append(item)

    if write:
        accepted_ids = {item["id"].casefold() for item in accepted_listings}
        final_listings = [
            *(item for item in active_listings if item["id"].casefold() not in accepted_ids),
            *accepted_listings,
        ]
        final_listings.sort(key=lambda item: item["id"].casefold())
        _write_generated_views(final_listings)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--write", action="store_true")
    collect_parser.add_argument("--results", required=True, type=Path)
    args = parser.parse_args()

    result: dict[str, list] = {
        "accepted": [],
        "rejected": [],
        "deferred": [],
        "reconciled": [],
        "warnings": [],
    }
    try:
        if args.command == "collect":
            result = collect(write=args.write)
        write_results(args.results, result)
        print(
            f"机器人收录完成：{len(result['accepted'])} 个成功，"
            f"{len(result['rejected'])} 个拒绝，{len(result['deferred'])} 个延后，"
            f"{len(result['warnings'])} 个警告"
        )
        return 0
    except (RegistryBotFailure, validator.ValidationFailure, OSError, UnicodeError) as exc:
        result["warnings"].append(_bounded_reason(exc))
        try:
            write_results(args.results, result)
        except (OSError, UnicodeError):
            pass
        print(f"机器人收录失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
