import copy
import io
import json
import shutil
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

import tools.validate as validator
from tools.validate import ValidationFailure


PLUGIN = {
    "id": "dev.example.test",
}
RELEASE = {
    "version": "1.0.0",
    "compatibility": {
        "manifestVersion": 1,
        "apiVersion": "1.0",
        "minimumLauncherVersion": "0.1.0",
    },
    "requiredCapabilities": [],
    "optionalCapabilities": [],
}
MANIFEST = {
    "manifestVersion": 1,
    "id": "dev.example.test",
    "name": "Test",
    "version": "1.0.0",
    "apiVersion": "1.0",
    "minimumLauncherVersion": "0.1.0",
    "entryAssembly": "Test.dll",
    "entryType": "Test.Plugin",
    "authors": ["Example"],
    "requiredCapabilities": [],
    "optionalCapabilities": [],
}

PUBLISHER_MANIFEST = {
    "$schema": "https://raw.githubusercontent.com/TouristH/UNuse/main/schemas/publisher-manifest-v1.schema.json",
    "manifest_version": 1,
    "id": "dev.example.test",
    "name": "Test",
    "description": "Fixture",
    "authors": ["Example"],
    "license": "MIT",
    "repository_url": "https://github.com/example/test",
    "maintainers": ["example"],
    "categories": ["utilities"],
    "releases": [
        {
            "version": "1.0.0",
            "channel": "stable",
            "published_at": "2026-08-20T00:00:00Z",
            "release_notes_url": "https://github.com/example/test/releases/tag/v1.0.0",
            "download": {
                "url": "https://github.com/example/test/releases/download/v1.0.0/dev.example.test-1.0.0.zip",
                "sha256": "a" * 64,
                "size": 123,
            },
            "api_version": "1.0",
            "minimum_launcher_version": "0.1.0",
            "required_capabilities": [],
            "optional_capabilities": [],
        },
    ],
}


def publisher_release(
    version: str,
    *,
    sha256: str = "b" * 64,
    size: int = 456,
    hour: int = 1,
) -> dict:
    return {
        "version": version,
        "channel": "stable",
        "published_at": f"2026-08-20T{hour:02d}:00:00Z",
        "release_notes_url": f"https://github.com/example/test/releases/tag/v{version}",
        "download": {
            "url": (
                "https://github.com/example/test/releases/download/"
                f"v{version}/dev.example.test-{version}.zip"
            ),
            "sha256": sha256,
            "size": size,
        },
        "api_version": "1.0",
        "minimum_launcher_version": "0.1.0",
        "required_capabilities": [],
        "optional_capabilities": [],
    }


def publisher_with(*additional_releases: dict) -> dict:
    manifest = copy.deepcopy(PUBLISHER_MANIFEST)
    manifest["releases"].extend(copy.deepcopy(list(additional_releases)))
    manifest["releases"].sort(key=lambda item: validator.semver_key(item["version"]))
    return manifest


def create_zip(
    extra_name=None,
    manifest=None,
    directory_payload=False,
    manifest_text: str | None = None,
    manifest_name: str = "plugin.json",
):
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            manifest_name,
            (
                manifest_text
                if manifest_text is not None
                else json.dumps(manifest or MANIFEST, ensure_ascii=False)
            ).encode("utf-8"),
        )
        archive.writestr("Test.dll", b"MZtest assembly metadata")
        if extra_name is not None:
            archive.writestr(extra_name, b"unsafe")
        if directory_payload:
            directory = zipfile.ZipInfo("nonempty/")
            archive.writestr(directory, b"not a directory payload")
    return memory.getvalue()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


class RegistryFixture:
    plugin_id = "dev.example.test"
    version = "1.0.0"
    sha256 = "a" * 64

    def __init__(self, root: Path):
        self.root = root
        self.plugin_path = root / "plugins" / self.plugin_id / "plugin.json"
        self.release_path = (
            root / "plugins" / self.plugin_id / "releases" / f"{self.version}.json"
        )
        self.review_path = root / "reviews" / self.plugin_id / f"{self.version}.json"

        write_json(
            root / "repository.json",
            {
                "schemaVersion": 1,
                "name": "Test registry",
                "sourceUrl": "https://github.com/TouristH/UNuse",
                "launcherUrl": "https://github.com/redstore-noob/NyaLauncher",
                "indexPath": "public/v1/index.json",
                "registryBotLogin": "nyalauncher-registry-bot[bot]",
                "trustedReviewers": ["TouristH"],
            },
        )
        write_json(
            root / "plugins.json",
            [
                {
                    "id": self.plugin_id,
                    "repositoryUrl": "https://github.com/example/test",
                    "repositoryId": 1001,
                    "ownerId": 101,
                }
            ],
        )
        write_json(
            self.plugin_path,
            {
                "schemaVersion": 1,
                "id": self.plugin_id,
                "name": "Test",
                "description": "Fixture",
                "authors": ["Example"],
                "repositoryUrl": "https://github.com/example/test",
                "maintainers": ["example"],
                "categories": ["utilities"],
                "license": "MIT",
            },
        )
        write_json(
            self.release_path,
            {
                "schemaVersion": 1,
                "version": self.version,
                "channel": "stable",
                "publishedAt": "2026-08-20T00:00:00Z",
                "releaseNotesUrl": "https://github.com/example/test/releases/tag/v1.0.0",
                "download": {
                    "url": "https://github.com/example/test/releases/download/v1.0.0/dev.example.test-1.0.0.zip",
                    "sha256": self.sha256,
                    "size": 123,
                },
                "compatibility": {
                    "manifestVersion": 1,
                    "apiVersion": "1.0",
                    "minimumLauncherVersion": "0.1.0",
                },
                "requiredCapabilities": [],
                "optionalCapabilities": [],
                "yanked": False,
            },
        )
        write_json(
            self.review_path,
            {
                "schemaVersion": 1,
                "pluginId": self.plugin_id,
                "version": self.version,
                "sha256": self.sha256,
                "status": "verified",
                "stateBy": "TouristH",
                "stateAt": "2026-08-13T00:00:00Z",
                "lastCommandAt": "2026-08-13T00:00:00Z",
                "lastCommentId": 123456789,
                "notes": "Exact artifact reviewed.",
            },
        )


class RuntimePackageTests(unittest.TestCase):
    def test_valid_package(self):
        validator.validate_runtime_package(PLUGIN, RELEASE, create_zip())

    def test_parent_traversal_is_rejected(self):
        with self.assertRaises(ValidationFailure):
            validator.validate_runtime_package(PLUGIN, RELEASE, create_zip("../escape.txt"))

    def test_backslash_path_is_rejected(self):
        with self.assertRaises(ValidationFailure):
            validator.validate_zip_path(
                PLUGIN["id"], RELEASE["version"], "folder\\escape.txt", False
            )

    def test_windows_superscript_device_names_are_rejected(self):
        for name in ("COM¹/payload.dll", "COM².txt", "COM³", "LPT¹/file"):
            with self.subTest(name=name), self.assertRaises(ValidationFailure):
                validator.validate_zip_path(
                    PLUGIN["id"], RELEASE["version"], name, False
                )

    def test_zip_path_limit_uses_launcher_utf16_length(self):
        astral_path = "/".join(["😀" * 100] * 3)
        with self.assertRaises(ValidationFailure):
            validator.validate_zip_path(
                PLUGIN["id"], RELEASE["version"], astral_path, False
            )

    def test_directory_entry_with_payload_is_rejected(self):
        with self.assertRaises(ValidationFailure):
            validator.validate_runtime_package(
                PLUGIN,
                RELEASE,
                create_zip(directory_payload=True),
            )

    def test_manifest_author_length_is_bounded(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["authors"] = ["x" * 257]
        with self.assertRaises(ValidationFailure):
            validator.validate_runtime_package(
                PLUGIN,
                RELEASE,
                create_zip(manifest=manifest),
            )

    def test_manifest_entry_type_is_required(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest.pop("entryType")
        with self.assertRaises(ValidationFailure):
            validator.validate_runtime_package(
                PLUGIN,
                RELEASE,
                create_zip(manifest=manifest),
            )

    def test_root_manifest_name_and_referenced_paths_are_case_exact(self):
        with self.assertRaises(ValidationFailure):
            validator.validate_runtime_package(
                PLUGIN,
                RELEASE,
                create_zip(manifest_name="PLUGIN.JSON"),
            )

        manifest = copy.deepcopy(MANIFEST)
        manifest["entryAssembly"] = "test.dll"
        with self.assertRaises(ValidationFailure):
            validator.validate_runtime_package(
                PLUGIN,
                RELEASE,
                create_zip(manifest=manifest),
            )

    def test_runtime_manifest_depth_matches_system_text_json_limit(self):
        manifest = copy.deepcopy(MANIFEST)
        nested: object = 0
        for _ in range(65):
            nested = {"value": nested}
        manifest["unknownDeepValue"] = nested
        with self.assertRaises(ValidationFailure):
            validator.validate_runtime_package(
                PLUGIN,
                RELEASE,
                create_zip(manifest=manifest),
            )

    def test_runtime_field_names_use_ascii_case_insensitive_binding(self):
        manifest = copy.deepcopy(MANIFEST)
        entry_assembly = manifest.pop("entryAssembly")
        manifest["entryAſſembly"] = entry_assembly
        with self.assertRaises(ValidationFailure):
            validator.validate_runtime_package(
                PLUGIN,
                RELEASE,
                create_zip(manifest=manifest),
            )

    def test_settings_must_be_an_array(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["settings"] = "not-an-array"
        with self.assertRaises(ValidationFailure):
            validator.validate_runtime_package(
                PLUGIN,
                RELEASE,
                create_zip(manifest=manifest),
            )

    def test_optional_manifest_fields_are_strongly_typed_and_bounded(self):
        for value in (42, "x" * 2049, "😀" * 1025):
            with self.subTest(homepage=value):
                manifest = copy.deepcopy(MANIFEST)
                manifest["homepage"] = value
                with self.assertRaises(ValidationFailure):
                    validator.validate_runtime_package(
                        PLUGIN,
                        RELEASE,
                        create_zip(manifest=manifest),
                    )

    def test_complete_setting_definition_is_accepted(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["settings"] = [
            {
                "key": "profile.name",
                "title": "Profile name",
                "description": "Used by the fixture.",
                "kind": "Choice",
                "scope": "MinecraftInstance",
                "defaultValue": "safe",
                "required": True,
                "maximumLength": 16,
                "pattern": "^(?:😀|[a-z])+$",
                "placeholder": "name",
                "options": [
                    {"value": "safe", "label": "Safe", "description": "Default"}
                ],
                "fileExtensions": [],
            }
        ]
        validator.validate_runtime_package(
            PLUGIN,
            RELEASE,
            create_zip(manifest=manifest),
        )

    def test_default_value_raw_json_length_matches_launcher_contract(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["Settings"] = [
            {
                "key": "sample",
                "title": "Sample",
                "kind": "Text",
                "DefaultValue": "a" * 6000,
            }
        ]
        manifest_text = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
        canonical_value = json.dumps("a" * 6000, separators=(",", ":"))
        escaped_value = '"' + "\\u0061" * 6000 + '"'
        manifest_text = manifest_text.replace(
            f'"DefaultValue":{canonical_value}',
            f'"DefaultValue":{escaped_value}',
        )
        with self.assertRaises(ValidationFailure):
            validator.validate_runtime_package(
                PLUGIN,
                RELEASE,
                create_zip(manifest_text=manifest_text),
            )

    def test_pattern_must_match_default_value_with_dotnet_regex(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["settings"] = [
            {
                "key": "sample",
                "title": "Sample",
                "kind": "Text",
                "pattern": "^z$",
                "defaultValue": "abc",
            }
        ]
        with self.assertRaises(ValidationFailure):
            validator.validate_runtime_package(
                PLUGIN,
                RELEASE,
                create_zip(manifest=manifest),
            )

    def test_number_default_uses_original_json_token_as_display_text(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["settings"] = [
            {
                "key": "sample",
                "title": "Sample",
                "kind": "Number",
                "maximumLength": 3,
                "defaultValue": 1.0,
            }
        ]
        manifest_text = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
        manifest_text = manifest_text.replace('"defaultValue":1.0', '"defaultValue":1.00')
        with self.assertRaises(ValidationFailure):
            validator.validate_runtime_package(
                PLUGIN,
                RELEASE,
                create_zip(manifest_text=manifest_text),
            )

    def test_invalid_setting_definitions_and_defaults_are_rejected(self):
        base = {"key": "sample", "title": "Sample"}
        candidates = (
            {**base, "kind": 1},
            {**base, "kind": "ſecret"},
            {**base, "required": "yes"},
            {**base, "maximumLength": 0},
            {**base, "minimum": 2, "maximum": 1},
            {**base, "kind": "Choice", "options": []},
            {**base, "kind": "Integer", "defaultValue": "1"},
            {**base, "kind": "Number", "minimum": 2, "defaultValue": 1},
            {**base, "kind": "File", "defaultValue": "C:/unsafe.txt"},
            {**base, "pattern": 42},
            {**base, "pattern": "["},
            {**base, "minimum": 10**400},
            {**base, "kind": "Number", "defaultValue": 10**400},
        )
        for setting in candidates:
            with self.subTest(setting=setting):
                manifest = copy.deepcopy(MANIFEST)
                manifest["settings"] = [setting]
                with self.assertRaises(ValidationFailure):
                    validator.validate_runtime_package(
                        PLUGIN,
                        RELEASE,
                        create_zip(manifest=manifest),
                    )

    def test_runtime_capabilities_do_not_use_unicode_casefold_equivalences(self):
        manifest = copy.deepcopy(MANIFEST)
        manifest["requiredCapabilities"] = ["ſystem.info.read"]
        with self.assertRaises(ValidationFailure):
            validator.validate_runtime_package(
                PLUGIN,
                RELEASE,
                create_zip(manifest=manifest),
            )

    def test_package_metadata_must_match_publisher_catalog(self):
        plugin = {
            "id": PLUGIN["id"],
            "name": "Test",
            "description": "Published description",
            "authors": ["Example"],
            "license": "MIT",
        }
        manifest = copy.deepcopy(MANIFEST)
        manifest.update(
            {
                "description": "Published description",
                "license": "MIT",
            }
        )
        for field, changed in (
            ("name", "Different name"),
            ("description", "Different description"),
            ("authors", ["Different author"]),
            ("license", "Apache-2.0"),
        ):
            with self.subTest(field=field):
                candidate = copy.deepcopy(manifest)
                candidate[field] = changed
                with self.assertRaises(ValidationFailure):
                    validator.validate_runtime_package(
                        plugin,
                        RELEASE,
                        create_zip(manifest=candidate),
                    )

    def test_every_zip_entry_is_fully_crc_checked(self):
        memory = io.BytesIO()
        assembly = b"MZtest assembly metadata"
        with zipfile.ZipFile(memory, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr(
                "plugin.json",
                json.dumps(MANIFEST, ensure_ascii=False).encode("utf-8"),
            )
            archive.writestr("Test.dll", assembly)
            archive.writestr("unused.dat", b"payload that must be CRC checked")
        payload = bytearray(memory.getvalue())
        marker = b"payload that must be CRC checked"
        offset = payload.index(marker)
        payload[offset + len(marker) - 1] ^= 0x01

        with self.assertRaises(ValidationFailure):
            validator.validate_runtime_package(PLUGIN, RELEASE, bytes(payload))


class StrictContractTests(unittest.TestCase):
    def test_oversized_json_integer_becomes_isolatable_validation_failure(self):
        with self.assertRaises(ValidationFailure):
            validator.parse_json_object(
                '{"value":' + "9" * 5000 + "}",
                "publisher::_manifest.json",
            )

    def test_semver_rejects_numeric_prerelease_leading_zero(self):
        self.assertIsNone(validator.match_semver("1.2.3-01"))
        self.assertIsNotNone(validator.match_semver("1.2.3-0.alpha+build.7"))

    def test_semver_numbers_must_fit_launcher_int32_contract(self):
        self.assertIsNotNone(validator.match_semver("2147483647.0.0-2147483647"))
        self.assertIsNone(validator.match_semver("2147483648.0.0"))
        self.assertIsNone(validator.match_semver("1.0.0-2147483648"))

    def test_utc_timestamp_is_exact_and_calendar_valid(self):
        path = validator.ROOT / "repository.json"
        for value in (
            "2026-08-13T00:00:00+00:00",
            "2026-08-13T00:00:00.000Z",
            "2026-02-30T00:00:00Z",
        ):
            with self.subTest(value=value), self.assertRaises(ValidationFailure):
                validator.validate_utc_timestamp(path, "publishedAt", value)

    def test_https_rejects_credentials_and_non_443_ports(self):
        path = validator.ROOT / "repository.json"
        for value in (
            "https://example.com:444/file",
            "https://user@example.com/file",
            "https://example.com:not-a-port/file",
            "https://exa mple.com/file",
            "https://example.com/a b",
            "https://example.com/%zz",
            "https://xn--/file",
        ):
            with self.subTest(value=value), self.assertRaises(ValidationFailure):
                validator.require_https(path, "url", value)
        self.assertEqual(
            validator.require_https(path, "url", "https://example.com:443/file"),
            "https://example.com:443/file",
        )

    def test_api_version_uses_ascii_digits_like_launcher(self):
        self.assertIsNotNone(validator.API_VERSION.fullmatch("1.0"))
        self.assertIsNone(validator.API_VERSION.fullmatch("1.١"))

    def test_index_size_is_bounded_in_utf8_bytes(self):
        with patch.object(validator, "MAXIMUM_INDEX_BYTES", 16):
            with self.assertRaises(ValidationFailure):
                validator.render({"text": "猫" * 16})

    def test_launcher_facing_text_limits_use_utf16_code_units(self):
        source = validator.ROOT / "repository.json"
        with self.assertRaises(ValidationFailure):
            validator.require_text(source, "name", "😀" * 129, 256)
        with self.assertRaises(ValidationFailure):
            validator.require_list_of_text(
                source,
                "authors",
                ["😀" * 129],
                64,
                item_maximum=256,
            )

    def test_repository_source_url_must_match_launcher_github_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            configuration = {
                "schemaVersion": 1,
                "name": "Test registry",
                "sourceUrl": "https://gitlab.com/example/registry",
                "launcherUrl": "https://github.com/redstore-noob/NyaLauncher",
                "indexPath": "public/v1/index.json",
                "registryBotLogin": "nyalauncher-registry-bot[bot]",
                "trustedReviewers": ["TouristH"],
            }
            write_json(root / "repository.json", configuration)
            with patch.object(validator, "ROOT", root), self.assertRaises(ValidationFailure):
                validator.load_repository_configuration()


class PublisherManifestTests(unittest.TestCase):
    listing = {
        "id": "dev.example.test",
        "repositoryUrl": "https://github.com/example/test",
    }

    def test_snake_case_manifest_maps_to_launcher_catalog_contract(self):
        manifest = publisher_with(publisher_release("1.1.0"))
        plugin, releases = validator.validate_publisher_manifest_releases(
            manifest, self.listing
        )
        compatibility_plugin, latest = validator.validate_publisher_manifest(
            manifest, self.listing
        )

        self.assertEqual(plugin["id"], self.listing["id"])
        self.assertEqual(plugin["repositoryUrl"], self.listing["repositoryUrl"])
        self.assertEqual([release["version"] for release in releases], ["1.0.0", "1.1.0"])
        self.assertEqual(compatibility_plugin, plugin)
        self.assertEqual(latest["version"], "1.1.0")
        self.assertEqual(latest["compatibility"]["manifestVersion"], 1)
        self.assertEqual(latest["compatibility"]["apiVersion"], "1.0")
        self.assertFalse(latest["yanked"])
        self.assertNotIn("review", latest)

    def test_complete_releases_must_be_strictly_semver_ascending(self):
        manifest = publisher_with(publisher_release("1.1.0"))
        manifest["releases"].reverse()
        with self.assertRaises(ValidationFailure):
            validator.validate_publisher_manifest_releases(manifest, self.listing)

    def test_complete_history_has_a_total_declared_byte_budget(self):
        manifest = publisher_with(publisher_release("1.1.0", size=100))
        with patch.object(validator, "MAXIMUM_PUBLISHER_HISTORY_BYTES", 200):
            with self.assertRaises(ValidationFailure):
                validator.validate_publisher_manifest_releases(manifest, self.listing)

    def test_non_release_zip_is_rejected(self):
        for url in (
            "https://github.com/example/test/archive/refs/heads/main.zip",
            "https://github.com/example/test/releases/download/v1.0.0/plugin.zip?",
            "https://github.com/example/test/releases/download/v1.0.0/plugin.zip#",
        ):
            with self.subTest(url=url):
                manifest = copy.deepcopy(PUBLISHER_MANIFEST)
                manifest["releases"][0]["download"]["url"] = url
                with self.assertRaises(ValidationFailure):
                    validator.validate_publisher_manifest(manifest, self.listing)

    def test_release_zip_must_belong_to_declared_repository(self):
        manifest = copy.deepcopy(PUBLISHER_MANIFEST)
        manifest["releases"][0]["download"]["url"] = (
            "https://github.com/attacker/other/releases/download/v1.0.0/plugin.zip"
        )
        with self.assertRaises(ValidationFailure):
            validator.validate_publisher_manifest(manifest, self.listing)

    def test_release_notes_must_be_a_tag_in_the_declared_repository(self):
        for url in (
            "https://example.com/example/test/releases/tag/v1.0.0",
            "https://github.com/attacker/test/releases/tag/v1.0.0",
            "https://github.com/example/test/releases/tag/",
            "https://github.com/example/test/releases/tag/v1.0.0?download=1",
            "https://github.com/example/test/releases/tag/v1.0.0?",
            "https://github.com/example/test/releases/tag/v1.0.0#notes",
            "https://github.com/example/test/releases/tag/v1.0.0#",
            "https://github.com/example/test/releases/tag/foo/../bar",
            "https://github.com/example/test/releases/tag/foo%5Cbar",
        ):
            with self.subTest(url=url):
                manifest = copy.deepcopy(PUBLISHER_MANIFEST)
                manifest["releases"][0]["release_notes_url"] = url
                with self.assertRaises(ValidationFailure):
                    validator.validate_publisher_manifest(manifest, self.listing)

    def test_release_zip_repository_path_case_must_match_launcher_contract(self):
        listing = {
            "id": "dev.example.test",
            "repositoryUrl": "https://github.com/Example/Test",
        }
        manifest = copy.deepcopy(PUBLISHER_MANIFEST)
        manifest["repository_url"] = "https://github.com/Example/Test"
        manifest["releases"][0]["release_notes_url"] = (
            "https://github.com/Example/Test/releases/tag/v1.0.0"
        )
        manifest["releases"][0]["download"]["url"] = (
            "https://github.com/example/test/releases/download/v1.0.0/"
            "dev.example.test-1.0.0.zip"
        )
        with self.assertRaises(ValidationFailure):
            validator.validate_publisher_manifest(manifest, listing)

    def test_manifest_id_and_repository_must_match_active_listing(self):
        manifest = copy.deepcopy(PUBLISHER_MANIFEST)
        manifest["id"] = "dev.example.other"
        with self.assertRaises(ValidationFailure):
            validator.validate_publisher_manifest(manifest, self.listing)

    def test_github_numeric_identity_rejects_reclaimed_repository_path(self):
        metadata = {
            "id": 2002,
            "html_url": "https://github.com/example/test",
            "owner": {"id": 202},
            "private": False,
            "fork": False,
            "archived": False,
            "disabled": False,
        }
        repository_id, owner_id, repository_url = (
            validator.validate_github_repository_identity(
                metadata,
                "https://github.com/example/test",
                "fixture",
            )
        )
        self.assertEqual((repository_id, owner_id), (2002, 202))
        self.assertEqual(repository_url, "https://github.com/example/test")

        reclaimed = dict(metadata)
        reclaimed["html_url"] = "https://github.com/attacker/test"
        with self.assertRaises(ValidationFailure):
            validator.validate_github_repository_identity(
                reclaimed,
                "https://github.com/example/test",
                "fixture",
            )

    def test_http_availability_errors_are_retryable_but_not_found_is_not(self):
        for code, headers in (
            (429, {}),
            (503, {}),
            (403, {"X-RateLimit-Remaining": "0"}),
            (403, {"Retry-After": "60"}),
        ):
            with self.subTest(code=code, headers=headers):
                error = urllib.error.HTTPError(
                    "https://api.github.com/test", code, "failed", headers, None
                )
                try:
                    self.assertTrue(validator.is_retryable_http_error(error))
                finally:
                    error.close()
        not_found = urllib.error.HTTPError(
            "https://api.github.com/test", 404, "not found", {}, None
        )
        try:
            self.assertFalse(validator.is_retryable_http_error(not_found))
        finally:
            not_found.close()

    def test_manifest_timeout_is_availability_failure_but_404_is_hard_failure(self):
        with patch.object(validator.urllib.request, "build_opener") as build_opener:
            build_opener.return_value.open.side_effect = TimeoutError("timed out")
            with self.assertRaises(validator.AvailabilityFailure):
                validator.fetch_repository_manifest(
                    "https://github.com/example/test", "fixture"
                )

        not_found = urllib.error.HTTPError(
            "https://raw.githubusercontent.com/example/test/HEAD/_manifest.json",
            404,
            "not found",
            {},
            None,
        )
        with patch.object(validator.urllib.request, "build_opener") as build_opener:
            build_opener.return_value.open.side_effect = not_found
            with self.assertRaises(ValidationFailure) as raised:
                validator.fetch_repository_manifest(
                    "https://github.com/example/test", "fixture"
                )
        not_found.close()
        self.assertNotIsInstance(raised.exception, validator.AvailabilityFailure)


class PublisherRefreshTests(unittest.TestCase):
    def refresh(
        self, fixture: RegistryFixture, publisher: dict, *, write: bool = False
    ) -> list[dict]:
        with (
            patch.object(validator, "ROOT", fixture.root),
            patch.object(
                validator,
                "fetch_publisher_manifest",
                return_value=copy.deepcopy(publisher),
            ),
            patch.object(validator, "verify_new_release_candidate"),
        ):
            listings = validator.load_plugin_list()
            details = validator.build_details()
            return validator.refresh_details(listings, details, write=write)

    def upgrade_fixture_to_v2(self, fixture: RegistryFixture) -> dict:
        lineage_id = "22222222-2222-4222-8222-222222222222"
        repository = json.loads(
            (fixture.root / "repository.json").read_text(encoding="utf-8")
        )
        repository.update(
            {
                "schemaVersion": 2,
                "indexV2Path": "public/v2/index.json",
                "v2MinimumLauncherVersion": "1.0.0-preview1",
                "trustedReviewerIds": {"TouristH": 143396778},
            }
        )
        write_json(fixture.root / "repository.json", repository)
        listing = {
            "id": fixture.plugin_id,
            "lineageId": lineage_id,
            "generation": 1,
            "repositoryUrl": "https://github.com/example/test",
            "repositoryId": 1001,
            "ownerId": 101,
        }
        write_json(fixture.root / "plugins.json", [listing])
        write_json(
            fixture.root / "plugins" / fixture.plugin_id / "identity.json",
            {
                "schemaVersion": 1,
                "id": fixture.plugin_id,
                "lineageId": lineage_id,
                "generation": 1,
                "lifecycleStatus": "active",
                "generations": [
                    {
                        "generation": 1,
                        "repositoryUrl": listing["repositoryUrl"],
                        "repositoryUrlHistory": [listing["repositoryUrl"]],
                        "repositoryId": 1001,
                        "ownerId": 101,
                        "status": "active",
                    }
                ],
            },
        )
        for path in (fixture.release_path, fixture.review_path):
            value = json.loads(path.read_text(encoding="utf-8"))
            value["generation"] = 1
            if path == fixture.review_path:
                value["stateById"] = 143396778
            write_json(path, value)
        return listing

    def test_same_numeric_repository_rename_without_release_persists_history(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            listing = self.upgrade_fixture_to_v2(fixture)
            old_url = listing["repositoryUrl"]
            new_url = "https://github.com/example/renamed"
            renamed_listing = {**listing, "repositoryUrl": new_url}
            publisher = copy.deepcopy(PUBLISHER_MANIFEST)
            publisher["repository_url"] = new_url

            with (
                patch.object(validator, "ROOT", fixture.root),
                patch.object(
                    validator,
                    "fetch_publisher_manifest",
                    return_value=publisher,
                ),
                patch.object(validator, "verify_new_release_candidate") as verify,
            ):
                details = validator.refresh_details(
                    [renamed_listing], validator.build_details(), write=True
                )
                # The collector commits the accepted active pointer together
                # with refresh_details' durable metadata/history transaction.
                write_json(fixture.root / "plugins.json", [renamed_listing])
                index_v1 = validator.build_index(details)
                index_v2 = validator.build_index_v2(details)

            verify.assert_not_called()
            plugin = json.loads(fixture.plugin_path.read_text(encoding="utf-8"))
            identity = json.loads(
                (
                    fixture.root
                    / "plugins"
                    / fixture.plugin_id
                    / "identity.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(plugin["repositoryUrl"], new_url)
            self.assertEqual(
                identity["generations"][0]["repositoryUrlHistory"],
                [old_url, new_url],
            )
            self.assertEqual(index_v1["plugins"], [])
            self.assertEqual(
                index_v2["plugins"][0]["generations"][0][
                    "repositoryUrlHistory"
                ],
                [old_url, new_url],
            )

            with (
                patch.object(validator, "ROOT", fixture.root),
                patch.object(
                    validator,
                    "fetch_publisher_manifest",
                    return_value=publisher,
                ),
                self.assertRaisesRegex(ValidationFailure, "循环或重复"),
            ):
                validator.refresh_details(
                    [listing], validator.build_details(), write=False
                )

    def test_same_numeric_repository_rename_with_new_release_persists_both(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            listing = self.upgrade_fixture_to_v2(fixture)
            new_url = "https://github.com/example/renamed"
            newer = publisher_with(publisher_release("1.1.0"))
            newer["repository_url"] = new_url
            newer_release = newer["releases"][-1]
            newer_release["release_notes_url"] = newer_release[
                "release_notes_url"
            ].replace("https://github.com/example/test/", new_url + "/")
            newer_release["download"]["url"] = newer_release["download"][
                "url"
            ].replace("https://github.com/example/test/", new_url + "/")

            with (
                patch.object(validator, "ROOT", fixture.root),
                patch.object(
                    validator,
                    "fetch_publisher_manifest",
                    return_value=newer,
                ),
                patch.object(validator, "verify_new_release_candidate") as verify,
            ):
                details = validator.refresh_details(
                    [{**listing, "repositoryUrl": new_url}],
                    validator.build_details(),
                    write=True,
                )

            verify.assert_called_once()
            self.assertEqual(
                [release["version"] for release in details[0]["releases"]],
                ["1.1.0", "1.0.0"],
            )
            identity = json.loads(
                (
                    fixture.root
                    / "plugins"
                    / fixture.plugin_id
                    / "identity.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                identity["generations"][0]["repositoryUrlHistory"],
                [listing["repositoryUrl"], new_url],
            )

    def test_case_only_repository_path_change_keeps_existing_canonical_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            listing = self.upgrade_fixture_to_v2(fixture)
            publisher = copy.deepcopy(PUBLISHER_MANIFEST)
            publisher["repository_url"] = "https://github.com/Example/Test"
            with (
                patch.object(validator, "ROOT", fixture.root),
                patch.object(
                    validator,
                    "fetch_publisher_manifest",
                    return_value=publisher,
                ),
            ):
                result = validator.refresh_details(
                    [
                        {
                            **listing,
                            "repositoryUrl": "https://github.com/Example/Test",
                        }
                    ],
                    validator.build_details(),
                    write=True,
                )
            self.assertEqual(result[0]["repositoryUrl"], listing["repositoryUrl"])
            identity = json.loads(
                (
                    fixture.root
                    / "plugins"
                    / fixture.plugin_id
                    / "identity.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                identity["generations"][0]["repositoryUrlHistory"],
                [listing["repositoryUrl"]],
            )

    def test_repository_rename_requires_same_numeric_repo_and_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            listing = self.upgrade_fixture_to_v2(fixture)
            for field in ("repositoryId", "ownerId"):
                with patch.object(validator, "ROOT", fixture.root):
                    catalog = validator.load_catalog()
                with (
                    self.subTest(field=field),
                    patch.object(validator, "ROOT", fixture.root),
                    self.assertRaisesRegex(
                        ValidationFailure, "repositoryId 与 ownerId"
                    ),
                ):
                    validator.merge_publisher_snapshot(
                        {fixture.plugin_id: catalog[0]},
                        {
                            **listing,
                            "repositoryUrl": "https://github.com/example/renamed",
                            field: listing[field] + 1,
                        },
                    )

    def test_offboarded_reviewer_revocation_is_historical_but_not_green(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            self.upgrade_fixture_to_v2(fixture)
            review = json.loads(fixture.review_path.read_text(encoding="utf-8"))
            review.update(
                {
                    "status": "revoked",
                    "stateBy": "FormerAdmin",
                    "stateById": 999,
                    "stateAt": "2026-08-21T00:00:00Z",
                    "lastCommandAt": "2026-08-21T00:00:00Z",
                    "lastCommentId": 123456790,
                    "notes": "Revoked before reviewer offboarding.",
                }
            )
            write_json(fixture.review_path, review)
            with patch.object(validator, "ROOT", fixture.root):
                index = validator.build_index_v2()
            self.assertNotIn("review", index["plugins"][0]["releases"][0])

            review["status"] = "verified"
            write_json(fixture.review_path, review)
            with (
                patch.object(validator, "ROOT", fixture.root),
                self.assertRaisesRegex(ValidationFailure, "trustedReviewers"),
            ):
                validator.build_index_v2()

    def test_schema1_keeps_yanked_archive_but_schema2_v1_hides_it(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            archived_id = "dev.example.archived"
            archived_url = "https://github.com/example/archived"
            archived_root = fixture.root / "plugins" / archived_id
            archived_plugin = json.loads(
                fixture.plugin_path.read_text(encoding="utf-8")
            )
            archived_plugin.update(
                {"id": archived_id, "repositoryUrl": archived_url}
            )
            archived_release = json.loads(
                fixture.release_path.read_text(encoding="utf-8")
            )
            archived_release["releaseNotesUrl"] = archived_release[
                "releaseNotesUrl"
            ].replace("https://github.com/example/test/", archived_url + "/")
            archived_release["download"]["url"] = archived_release["download"][
                "url"
            ].replace("https://github.com/example/test/", archived_url + "/")
            archived_release["yanked"] = True
            archived_release["yankReason"] = "Legacy archive."
            write_json(archived_root / "plugin.json", archived_plugin)
            write_json(
                archived_root / "releases" / "1.0.0.json", archived_release
            )

            with patch.object(validator, "ROOT", fixture.root):
                schema1 = validator.build_index()
            self.assertEqual(
                {plugin["id"] for plugin in schema1["plugins"]},
                {fixture.plugin_id, archived_id},
            )

            self.upgrade_fixture_to_v2(fixture)
            archived_release["generation"] = 1
            write_json(
                archived_root / "releases" / "1.0.0.json", archived_release
            )
            write_json(
                archived_root / "identity.json",
                {
                    "schemaVersion": 1,
                    "id": archived_id,
                    "lineageId": "33333333-3333-4333-8333-333333333333",
                    "generation": 1,
                    "lifecycleStatus": "retired",
                    "generations": [
                        {
                            "generation": 1,
                            "repositoryUrl": archived_url,
                            "repositoryUrlHistory": [archived_url],
                            "repositoryId": 1002,
                            "ownerId": 101,
                            "status": "retired",
                        }
                    ],
                },
            )
            with patch.object(validator, "ROOT", fixture.root):
                schema2_v1 = validator.build_index()
                schema2 = validator.build_index_v2()
            self.assertEqual(
                [plugin["id"] for plugin in schema2_v1["plugins"]],
                [fixture.plugin_id],
            )
            archived = next(
                plugin for plugin in schema2["plugins"] if plugin["id"] == archived_id
            )
            self.assertEqual(archived["visibility"], "hidden")

    def test_schema2_bootstrap_anchor_allows_registry_growth_and_offboarding(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            listing = self.upgrade_fixture_to_v2(fixture)
            repository_path = fixture.root / "repository.json"
            repository = json.loads(repository_path.read_text(encoding="utf-8"))
            repository["trustedReviewers"] = ["TouristH", "NewAdmin"]
            repository["trustedReviewerIds"] = {
                "TouristH": 143396778,
                "NewAdmin": 777,
            }
            write_json(repository_path, repository)
            extra_id = "dev.example.extra"
            extra_url = "https://github.com/example/extra"
            extra_listing = {
                "id": extra_id,
                "lineageId": "33333333-3333-4333-8333-333333333333",
                "generation": 1,
                "repositoryUrl": extra_url,
                "repositoryId": 1002,
                "ownerId": 101,
            }
            write_json(fixture.root / "plugins.json", [listing, extra_listing])
            extra_plugin = json.loads(
                fixture.plugin_path.read_text(encoding="utf-8")
            )
            extra_plugin.update({"id": extra_id, "repositoryUrl": extra_url})
            write_json(
                fixture.root / "plugins" / extra_id / "plugin.json", extra_plugin
            )
            write_json(
                fixture.root / "migrations" / "v2-bootstrap.json",
                {
                    "$schema": "../schemas/registry-bootstrap-v1.schema.json",
                    "schemaVersion": 1,
                    "targetRepositorySchemaVersion": 2,
                    "trustedReviewerIds": {"TouristH": 143396778},
                    "targetTrustedReviewerIds": {"TouristH": 143396778},
                    "publisherBindings": {
                        fixture.plugin_id: {
                            "lineageId": listing["lineageId"],
                            "repositoryUrl": listing["repositoryUrl"],
                            "repositoryId": listing["repositoryId"],
                            "ownerId": listing["ownerId"],
                        }
                    },
                },
            )
            with patch.object(validator, "ROOT", fixture.root):
                validator.validate_bootstrap_anchor()

            repository["trustedReviewers"] = ["NewAdmin"]
            repository["trustedReviewerIds"] = {"NewAdmin": 777}
            write_json(repository_path, repository)
            with patch.object(validator, "ROOT", fixture.root):
                validator.validate_bootstrap_anchor()

            identity_path = (
                fixture.root
                / "plugins"
                / fixture.plugin_id
                / "identity.json"
            )
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            identity["generations"][0]["repositoryId"] += 1
            write_json(identity_path, identity)
            with (
                patch.object(validator, "ROOT", fixture.root),
                self.assertRaisesRegex(ValidationFailure, "g1 identity"),
            ):
                validator.validate_bootstrap_anchor()

    def test_existing_details_history_is_generation_aware(self):
        existing_details = [
            {
                "id": "dev.example.test",
                "releases": [
                    {"generation": 1, "version": "1.0.0"},
                ],
            }
        ]
        catalog = [
            {
                "id": "dev.example.test",
                "releases": [
                    {"generation": 2, "version": "1.0.0"},
                ],
            }
        ]

        with self.assertRaisesRegex(ValidationFailure, "g1"):
            validator.ensure_existing_details_history(existing_details, catalog)

    def test_same_version_snapshot_is_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            changed = copy.deepcopy(PUBLISHER_MANIFEST)
            changed["releases"][0]["download"]["sha256"] = "b" * 64

            with self.assertRaises(ValidationFailure):
                self.refresh(fixture, changed)

            source = json.loads(fixture.release_path.read_text(encoding="utf-8"))
            self.assertEqual(source["download"]["sha256"], fixture.sha256)

    def test_known_immutable_history_is_not_downloaded_again(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            listing = {
                "id": fixture.plugin_id,
                "repositoryUrl": "https://github.com/example/test",
            }
            with (
                patch.object(validator, "ROOT", fixture.root),
                patch.object(
                    validator,
                    "fetch_publisher_manifest",
                    return_value=copy.deepcopy(PUBLISHER_MANIFEST),
                ),
                patch.object(validator, "verify_new_release_candidate") as verify,
            ):
                by_id = {
                    plugin["id"]: plugin for plugin in validator.load_catalog()
                }
                merged = validator.merge_publisher_snapshot(by_id, listing)

            self.assertEqual(merged, (None, []))
            verify.assert_not_called()

    def test_new_candidate_is_verified_before_any_history_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            newer = publisher_with(publisher_release("1.1.0"))
            target = (
                fixture.root
                / "plugins"
                / fixture.plugin_id
                / "releases"
                / "1.1.0.json"
            )
            with (
                patch.object(validator, "ROOT", fixture.root),
                patch.object(
                    validator,
                    "fetch_publisher_manifest",
                    return_value=copy.deepcopy(newer),
                ),
                patch.object(
                    validator,
                    "verify_new_release_candidate",
                    side_effect=ValidationFailure("bad package"),
                ),
                self.assertRaises(ValidationFailure),
            ):
                validator.refresh_details(
                    validator.load_plugin_list(),
                    validator.build_details(),
                    write=True,
                )
            self.assertFalse(target.exists())
            plugin_source = json.loads(fixture.plugin_path.read_text(encoding="utf-8"))
            self.assertEqual(plugin_source["name"], "Test")

    def test_all_candidates_are_verified_before_a_multi_version_batch_is_written(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            publisher = publisher_with(
                publisher_release("1.1.0"),
                publisher_release("1.2.0", sha256="c" * 64, hour=2),
            )
            with (
                patch.object(validator, "ROOT", fixture.root),
                patch.object(
                    validator,
                    "fetch_publisher_manifest",
                    return_value=copy.deepcopy(publisher),
                ),
                patch.object(
                    validator,
                    "verify_new_release_candidate",
                    side_effect=[None, ValidationFailure("second package failed")],
                ) as verify,
                self.assertRaises(ValidationFailure),
            ):
                validator.refresh_details(
                    validator.load_plugin_list(),
                    validator.build_details(),
                    write=True,
                )
            self.assertEqual(verify.call_count, 2)
            for version in ("1.1.0", "1.2.0"):
                self.assertFalse(
                    (
                        fixture.root
                        / "plugins"
                        / fixture.plugin_id
                        / "releases"
                        / f"{version}.json"
                    ).exists()
                )

    def test_best_effort_refresh_isolates_one_publisher_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            listings = [
                {"id": "dev.example.offline", "repositoryUrl": "https://github.com/example/offline"},
                {"id": fixture.plugin_id, "repositoryUrl": "https://github.com/example/test"},
            ]
            warnings = []
            with (
                patch.object(validator, "ROOT", fixture.root),
                patch.object(
                    validator,
                    "merge_publisher_snapshot",
                    side_effect=[ValidationFailure("offline"), (None, [])],
                ) as merge,
                patch.object(validator, "emit_refresh_warning") as emit,
            ):
                details = validator.refresh_details(
                    listings,
                    validator.build_details(),
                    best_effort=True,
                    warnings=warnings,
                )
            self.assertEqual(merge.call_count, 2)
            self.assertEqual(details[0]["id"], fixture.plugin_id)
            self.assertEqual(len(warnings), 1)
            emit.assert_called_once()

    def test_global_refresh_budget_is_shared_across_publishers(self):
        catalog = [
            {"id": f"dev.example.{suffix}", "releases": []}
            for suffix in ("a", "b", "c")
        ]
        listings = [
            {
                "id": plugin["id"],
                "repositoryUrl": f"https://github.com/example/{plugin['id'][-1]}",
            }
            for plugin in catalog
        ]
        calls = []

        def merge(by_id, listing, **budget):
            calls.append((listing["id"], budget))
            if listing["id"].endswith(".a"):
                releases = [
                    {"version": "1.0.0", "download": {"size": 100}},
                    {"version": "1.1.0", "download": {"size": 100}},
                ]
            else:
                releases = [
                    {"version": "1.0.0", "download": {"size": 50}}
                ]
            return {"id": listing["id"]}, releases

        with (
            patch.object(validator, "load_catalog", return_value=catalog),
            patch.object(validator, "merge_publisher_snapshot", side_effect=merge),
            patch.object(validator, "MAXIMUM_NEW_RELEASE_COUNT", 3),
            patch.object(validator, "MAXIMUM_NEW_RELEASE_BYTES", 250),
            patch.dict(
                validator.os.environ,
                {"NYA_REFRESH_OFFSET": "0", "NYA_REFRESH_TARGET": ""},
            ),
        ):
            validator.refresh_details(listings, [])

        self.assertEqual([call[0] for call in calls], ["dev.example.a", "dev.example.b"])
        self.assertEqual(calls[0][1]["maximum_candidates"], 3)
        self.assertEqual(calls[0][1]["maximum_candidate_bytes"], 250)
        self.assertEqual(calls[1][1]["maximum_candidates"], 1)
        self.assertEqual(calls[1][1]["maximum_candidate_bytes"], 50)

    def test_invalid_manifest_before_download_does_not_consume_global_budget(self):
        catalog = [
            {"id": f"dev.example.{suffix}", "releases": []}
            for suffix in ("a", "b")
        ]
        listings = [
            {
                "id": plugin["id"],
                "repositoryUrl": f"https://github.com/example/{plugin['id'][-1]}",
            }
            for plugin in catalog
        ]
        budgets = []

        def merge(by_id, listing, **budget):
            budgets.append(budget)
            if listing["id"].endswith(".a"):
                raise ValidationFailure("invalid publisher")
            return None, []

        with (
            patch.object(validator, "load_catalog", return_value=catalog),
            patch.object(validator, "merge_publisher_snapshot", side_effect=merge),
            patch.object(validator, "emit_refresh_warning"),
            patch.dict(
                validator.os.environ,
                {"NYA_REFRESH_OFFSET": "0", "NYA_REFRESH_TARGET": ""},
            ),
        ):
            validator.refresh_details(listings, [], best_effort=True)

        self.assertEqual(len(budgets), 2)
        self.assertEqual(budgets[0], budgets[1])

    def test_failed_candidate_download_consumes_global_budget(self):
        catalog = [
            {"id": f"dev.example.{suffix}", "releases": []}
            for suffix in ("a", "b")
        ]
        listings = [
            {
                "id": plugin["id"],
                "repositoryUrl": f"https://github.com/example/{plugin['id'][-1]}",
            }
            for plugin in catalog
        ]
        budgets = []

        def merge(by_id, listing, **budget):
            budgets.append(budget)
            if listing["id"].endswith(".a"):
                raise validator.PublisherCandidateFailure(
                    "second ZIP failed", attempted_count=2, attempted_bytes=200
                )
            return None, []

        with (
            patch.object(validator, "load_catalog", return_value=catalog),
            patch.object(validator, "merge_publisher_snapshot", side_effect=merge),
            patch.object(validator, "emit_refresh_warning"),
            patch.object(validator, "MAXIMUM_NEW_RELEASE_COUNT", 3),
            patch.object(validator, "MAXIMUM_NEW_RELEASE_BYTES", 250),
            patch.dict(
                validator.os.environ,
                {"NYA_REFRESH_OFFSET": "0", "NYA_REFRESH_TARGET": ""},
            ),
        ):
            validator.refresh_details(listings, [], best_effort=True)

        self.assertEqual(len(budgets), 2)
        self.assertEqual(budgets[0]["maximum_candidates"], 3)
        self.assertEqual(budgets[0]["maximum_candidate_bytes"], 250)
        self.assertEqual(budgets[1]["maximum_candidates"], 1)
        self.assertEqual(budgets[1]["maximum_candidate_bytes"], 50)

    def test_failed_discovery_batch_cannot_consume_active_publisher_zip_reserve(self):
        new_id = "io.github.alice.new"
        active_id = "dev.example.active"
        catalog = [{"id": active_id, "releases": []}]
        listings = [
            {
                "id": new_id,
                "repositoryUrl": "https://github.com/alice/new",
            },
            {
                "id": active_id,
                "repositoryUrl": "https://github.com/example/active",
            },
        ]
        calls = []
        retryable_failures = set()

        def merge(_by_id, listing, **budget):
            calls.append((listing["id"], budget))
            if listing["id"] == new_id:
                raise validator.PublisherCandidateFailure(
                    "Topic ZIP download timed out",
                    attempted_count=8,
                    attempted_bytes=256,
                    retryable=True,
                )
            return None, []

        with (
            patch.object(validator, "load_catalog", return_value=catalog),
            patch.object(validator, "merge_publisher_snapshot", side_effect=merge),
            patch.object(validator, "emit_refresh_warning"),
            patch.object(validator, "MAXIMUM_NEW_RELEASE_COUNT", 16),
            patch.object(validator, "MAXIMUM_NEW_RELEASE_BYTES", 512),
            patch.dict(
                validator.os.environ,
                {"NYA_REFRESH_OFFSET": "0", "NYA_REFRESH_TARGET": ""},
            ),
        ):
            validator.refresh_details(
                listings,
                [],
                best_effort=True,
                priority_ids=[new_id],
                retryable_failures=retryable_failures,
            )

        self.assertEqual([item[0] for item in calls], [new_id, active_id])
        self.assertEqual(calls[0][1]["maximum_candidates"], 8)
        self.assertEqual(calls[0][1]["maximum_candidate_bytes"], 256)
        self.assertEqual(calls[1][1]["maximum_candidates"], 8)
        self.assertEqual(calls[1][1]["maximum_candidate_bytes"], 256)
        self.assertEqual(retryable_failures, {new_id})

    def test_refresh_order_rotates_known_publishers_after_new_active_plugins(self):
        listings = [
            {"id": "dev.example.a"},
            {"id": "dev.example.new"},
            {"id": "dev.example.b"},
        ]
        catalog = [
            {"id": "dev.example.a"},
            {"id": "dev.example.b"},
        ]
        with patch.dict(
            validator.os.environ,
            {"NYA_REFRESH_OFFSET": "1", "NYA_REFRESH_TARGET": ""},
        ):
            ordered = validator.order_refresh_listings(listings, catalog)
        self.assertEqual(
            [listing["id"] for listing in ordered],
            ["dev.example.new", "dev.example.b", "dev.example.a"],
        )

    def test_explicit_refresh_priority_precedes_lexical_new_plugin_order(self):
        listings = [
            {"id": "io.github.alice.topic"},
            {"id": "io.github.zed.issue"},
            {"id": "dev.example.known"},
        ]
        catalog = [{"id": "dev.example.known"}]
        with patch.dict(
            validator.os.environ,
            {"NYA_REFRESH_OFFSET": "0", "NYA_REFRESH_TARGET": ""},
        ):
            ordered = validator.order_refresh_listings(
                listings,
                catalog,
                ["io.github.zed.issue", "io.github.alice.topic"],
            )
        self.assertEqual(
            [listing["id"] for listing in ordered],
            ["io.github.zed.issue", "io.github.alice.topic", "dev.example.known"],
        )

    def test_refresh_target_is_first_even_when_it_is_known(self):
        listings = [
            {"id": "dev.example.a"},
            {"id": "dev.example.new"},
            {"id": "dev.example.b"},
        ]
        catalog = [
            {"id": "dev.example.a"},
            {"id": "dev.example.b"},
        ]
        with patch.dict(
            validator.os.environ,
            {
                "NYA_REFRESH_OFFSET": "1",
                "NYA_REFRESH_TARGET": "dev.example.a",
            },
        ):
            ordered = validator.order_refresh_listings(listings, catalog)
        self.assertEqual(
            [listing["id"] for listing in ordered],
            ["dev.example.a"],
        )

        with patch.dict(
            validator.os.environ,
            {"NYA_REFRESH_TARGET": "dev.example.missing"},
        ), self.assertRaises(ValidationFailure):
            validator.order_refresh_listings(listings, catalog)

    def test_refresh_stops_after_global_publisher_attempt_budget(self):
        catalog = [
            {"id": f"dev.example.p{index}", "releases": []}
            for index in range(5)
        ]
        listings = [
            {
                "id": plugin["id"],
                "repositoryUrl": f"https://github.com/example/p{index}",
            }
            for index, plugin in enumerate(catalog)
        ]
        attempted = []

        def merge(by_id, listing, **budget):
            attempted.append(listing["id"])
            return None, []

        with (
            patch.object(validator, "load_catalog", return_value=catalog),
            patch.object(validator, "merge_publisher_snapshot", side_effect=merge),
            patch.object(validator, "MAXIMUM_REFRESH_PUBLISHERS", 3),
            patch.dict(
                validator.os.environ,
                {"NYA_REFRESH_OFFSET": "0", "NYA_REFRESH_TARGET": ""},
            ),
        ):
            validator.refresh_details(listings, [])

        self.assertEqual(
            attempted,
            ["dev.example.p0", "dev.example.p1", "dev.example.p2"],
        )

    def test_new_version_is_appended_without_losing_history(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            newer = publisher_with(publisher_release("1.1.0"))

            details = self.refresh(fixture, newer, write=True)

            self.assertTrue(fixture.release_path.is_file())
            self.assertTrue(
                (
                    fixture.root
                    / "plugins"
                    / fixture.plugin_id
                    / "releases"
                    / "1.1.0.json"
                ).is_file()
            )
            self.assertEqual(
                [item["version"] for item in details[0]["releases"]],
                ["1.1.0", "1.0.0"],
            )
            with patch.object(validator, "ROOT", fixture.root):
                index = validator.build_index()
            self.assertEqual(
                [item["version"] for item in index["plugins"][0]["releases"]],
                ["1.1.0", "1.0.0"],
            )

    def test_missing_version_below_central_maximum_can_be_backfilled(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            self.refresh(
                fixture,
                publisher_with(
                    publisher_release("1.2.0", sha256="c" * 64, hour=2)
                ),
                write=True,
            )
            complete = publisher_with(
                publisher_release("1.1.0"),
                publisher_release("1.2.0", sha256="c" * 64, hour=2),
            )

            details = self.refresh(fixture, complete, write=True)

            self.assertEqual(
                [release["version"] for release in details[0]["releases"]],
                ["1.2.0", "1.1.0", "1.0.0"],
            )

    def test_catch_up_batch_selects_latest_missing_versions_with_size_budget(self):
        plugin, releases = validator.validate_publisher_manifest_releases(
            publisher_with(
                publisher_release("1.1.0", size=300),
                publisher_release("1.2.0", size=300, sha256="c" * 64, hour=2),
                publisher_release("1.3.0", size=100, sha256="d" * 64, hour=3),
            ),
            PublisherManifestTests.listing,
        )
        existing = {
            **plugin,
            "releases": [
                validator.validate_publisher_manifest_releases(
                    copy.deepcopy(PUBLISHER_MANIFEST),
                    PublisherManifestTests.listing,
                )[1][0]
            ],
        }
        with (
            patch.object(validator, "MAXIMUM_NEW_RELEASE_COUNT", 2),
            patch.object(validator, "MAXIMUM_NEW_RELEASE_BYTES", 399),
        ):
            candidates = validator.plan_publisher_candidates(plugin, releases, existing)
        self.assertEqual(
            [release["version"] for release in candidates],
            ["1.3.0"],
        )

    def test_more_than_one_batch_converges_without_truncating_publisher_history(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            complete = publisher_with(
                publisher_release("1.1.0", hour=1),
                publisher_release("1.2.0", sha256="c" * 64, hour=2),
                publisher_release("1.3.0", sha256="d" * 64, hour=3),
                publisher_release("1.4.0", sha256="e" * 64, hour=4),
            )
            with patch.object(validator, "MAXIMUM_NEW_RELEASE_COUNT", 2):
                first = self.refresh(fixture, complete, write=True)
                second = self.refresh(fixture, complete, write=True)

            self.assertEqual(
                [release["version"] for release in first[0]["releases"]],
                ["1.4.0", "1.3.0", "1.0.0"],
            )
            self.assertEqual(
                [release["version"] for release in second[0]["releases"]],
                ["1.4.0", "1.3.0", "1.2.0", "1.1.0", "1.0.0"],
            )

    def test_stable_top_level_metadata_cannot_change_with_a_new_release(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            changed = publisher_with(publisher_release("1.1.0"))
            changed["name"] = "Renamed"
            with self.assertRaises(ValidationFailure):
                self.refresh(fixture, changed)

    def test_archived_history_cannot_reactivate_from_only_an_older_backfill(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            historical = json.loads(fixture.release_path.read_text(encoding="utf-8"))
            historical["yanked"] = True
            historical["yankReason"] = "Archived."
            write_json(fixture.release_path, historical)
            complete = publisher_with(
                publisher_release("0.5.0", sha256="f" * 64, hour=0)
            )

            with self.assertRaises(ValidationFailure):
                self.refresh(fixture, complete, write=True)

            self.assertFalse(
                (
                    fixture.root
                    / "plugins"
                    / fixture.plugin_id
                    / "releases"
                    / "0.5.0.json"
                ).exists()
            )

    def test_retired_numeric_publisher_reactivates_same_lineage_with_new_version(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            lineage_id = "22222222-2222-4222-8222-222222222222"
            renamed_url = "https://github.com/example/renamed"
            repository = json.loads(
                (fixture.root / "repository.json").read_text(encoding="utf-8")
            )
            repository.update(
                {
                    "schemaVersion": 2,
                    "indexV2Path": "public/v2/index.json",
                    "v2MinimumLauncherVersion": "1.0.0-preview1",
                    "trustedReviewerIds": {"TouristH": 143396778},
                }
            )
            write_json(fixture.root / "repository.json", repository)
            write_json(
                fixture.root / "plugins.json",
                [
                    {
                        "id": fixture.plugin_id,
                        "lineageId": lineage_id,
                        "generation": 1,
                        "repositoryUrl": "https://github.com/example/test",
                        "repositoryId": 1001,
                        "ownerId": 101,
                    }
                ],
            )
            write_json(
                fixture.root / "plugins" / fixture.plugin_id / "identity.json",
                {
                    "schemaVersion": 1,
                    "id": fixture.plugin_id,
                    "lineageId": lineage_id,
                    "generation": 1,
                    "lifecycleStatus": "retired",
                    "generations": [
                        {
                            "generation": 1,
                            "repositoryUrl": "https://github.com/example/test",
                            "repositoryUrlHistory": [
                                "https://github.com/example/test"
                            ],
                            "repositoryId": 1001,
                            "ownerId": 101,
                            "status": "retired",
                        }
                    ],
                },
            )
            historical = json.loads(fixture.release_path.read_text(encoding="utf-8"))
            historical["generation"] = 1
            historical["yanked"] = True
            historical["yankReason"] = "Author retired the plugin."
            write_json(fixture.release_path, historical)
            review = json.loads(fixture.review_path.read_text(encoding="utf-8"))
            review["generation"] = 1
            review["stateById"] = 143396778
            review["status"] = "revoked"
            write_json(fixture.review_path, review)

            newer = publisher_with(publisher_release("1.1.0"))
            newer["repository_url"] = renamed_url
            newer_release = newer["releases"][-1]
            newer_release["release_notes_url"] = newer_release[
                "release_notes_url"
            ].replace("https://github.com/example/test/", renamed_url + "/")
            newer_release["download"]["url"] = newer_release["download"][
                "url"
            ].replace("https://github.com/example/test/", renamed_url + "/")
            listing = json.loads(
                (fixture.root / "plugins.json").read_text(encoding="utf-8")
            )[0]
            listing["repositoryUrl"] = renamed_url
            write_json(fixture.root / "plugins.json", [listing])
            details = self.refresh(fixture, newer, write=True)

            self.assertEqual(details[0]["lineageId"], lineage_id)
            self.assertEqual(details[0]["generation"], 1)
            self.assertEqual(details[0]["lifecycleStatus"], "active")
            self.assertEqual(details[0]["visibility"], "listed")
            self.assertEqual(
                [release["version"] for release in details[0]["releases"]],
                ["1.1.0", "1.0.0"],
            )
            identity = json.loads(
                (
                    fixture.root
                    / "plugins"
                    / fixture.plugin_id
                    / "identity.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(identity["lineageId"], lineage_id)
            self.assertEqual(identity["generation"], 1)
            self.assertEqual(identity["lifecycleStatus"], "active")
            self.assertEqual(identity["generations"][0]["status"], "active")
            self.assertEqual(
                identity["generations"][0]["repositoryUrlHistory"],
                ["https://github.com/example/test", renamed_url],
            )
            with patch.object(validator, "ROOT", fixture.root):
                index = validator.build_index()
                index_v2 = validator.build_index_v2()
            self.assertEqual(index["plugins"], [])
            self.assertEqual(index_v2["plugins"][0]["id"], fixture.plugin_id)

    def test_new_version_preserves_an_administrator_yank_override(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            release = json.loads(fixture.release_path.read_text(encoding="utf-8"))
            release["yanked"] = True
            release["yankReason"] = "Security incident."
            write_json(fixture.release_path, release)
            newer = publisher_with(publisher_release("1.1.0"))

            details = self.refresh(fixture, newer)

            self.assertFalse(details[0]["releases"][0]["yanked"])
            self.assertTrue(details[0]["releases"][1]["yanked"])
            self.assertEqual(
                details[0]["releases"][1]["yankReason"], "Security incident."
            )

    def test_publisher_cannot_roll_current_manifest_back_to_an_older_active_version(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            newer = publisher_with(publisher_release("1.1.0"))
            self.refresh(fixture, newer, write=True)

            with self.assertRaises(ValidationFailure):
                self.refresh(fixture, PUBLISHER_MANIFEST)

    def test_publisher_may_still_point_to_an_administrator_yanked_release(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            newer = publisher_with(publisher_release("1.1.0"))
            self.refresh(fixture, newer, write=True)
            release_path = (
                fixture.root
                / "plugins"
                / fixture.plugin_id
                / "releases"
                / "1.1.0.json"
            )
            release = json.loads(release_path.read_text(encoding="utf-8"))
            release["yanked"] = True
            release["yankReason"] = "Administrator override."
            write_json(release_path, release)

            details = self.refresh(fixture, newer)

            self.assertTrue(details[0]["releases"][0]["yanked"])
            self.assertFalse(details[0]["releases"][1]["yanked"])

    def test_publisher_cannot_roll_back_to_a_yanked_version_below_active_history(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            oldest = json.loads(fixture.release_path.read_text(encoding="utf-8"))
            oldest["yanked"] = True
            oldest["yankReason"] = "Old vulnerable release."
            write_json(fixture.release_path, oldest)
            newest = copy.deepcopy(oldest)
            newest["version"] = "4.0.0"
            newest["publishedAt"] = "2026-08-20T04:00:00Z"
            newest["releaseNotesUrl"] = (
                "https://github.com/example/test/releases/tag/v4.0.0"
            )
            newest["download"] = {
                "url": "https://github.com/example/test/releases/download/v4.0.0/dev.example.test-4.0.0.zip",
                "sha256": "d" * 64,
                "size": 789,
            }
            newest["yanked"] = False
            newest.pop("yankReason")
            write_json(
                fixture.root
                / "plugins"
                / fixture.plugin_id
                / "releases"
                / "4.0.0.json",
                newest,
            )

            with self.assertRaises(ValidationFailure):
                self.refresh(fixture, PUBLISHER_MANIFEST)

class RegistryGenerationTests(unittest.TestCase):
    def build_fixture(self, fixture: RegistryFixture):
        with patch.object(validator, "ROOT", fixture.root):
            return validator.build_index()

    def test_review_is_hash_bound_and_mapped_to_launcher_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            index = self.build_fixture(fixture)

            plugin = index["plugins"][0]
            release = plugin["releases"][0]
            self.assertNotIn("schemaVersion", plugin)
            self.assertNotIn("schemaVersion", release)
            self.assertEqual(
                release["review"],
                {
                    "status": "verified",
                    "sha256": fixture.sha256,
                    "reviewedBy": "TouristH",
                    "reviewedAt": "2026-08-13T00:00:00Z",
                    "notes": "Exact artifact reviewed.",
                },
            )
            self.assertNotIn("pluginId", release["review"])
            self.assertNotIn("version", release["review"])
            self.assertNotIn("reviewer", release["review"])
            self.assertNotIn("schemaVersion", release["review"])

    def test_plugin_details_view_is_complete_but_contains_no_review(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            with patch.object(validator, "ROOT", fixture.root):
                details = validator.build_details()
                index = validator.build_index(details)

            self.assertEqual(details[0]["id"], fixture.plugin_id)
            self.assertEqual(details[0]["releases"][0]["version"], fixture.version)
            self.assertNotIn("review", details[0]["releases"][0])
            self.assertEqual(
                index["plugins"][0]["releases"][0]["review"]["sha256"],
                fixture.sha256,
            )

    def test_revoked_review_record_is_kept_out_of_public_index(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            review = json.loads(fixture.review_path.read_text(encoding="utf-8"))
            review["status"] = "revoked"
            review["stateAt"] = "2026-08-14T00:00:00Z"
            review["lastCommandAt"] = "2026-08-14T00:00:00Z"
            review["lastCommentId"] = 123456790
            review["notes"] = "Administrator revoked the green marker."
            write_json(fixture.review_path, review)

            index = self.build_fixture(fixture)

            self.assertNotIn("review", index["plugins"][0]["releases"][0])

    def test_review_hash_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            review = json.loads(fixture.review_path.read_text(encoding="utf-8"))
            review["sha256"] = "b" * 64
            write_json(fixture.review_path, review)
            with self.assertRaises(ValidationFailure):
                self.build_fixture(fixture)

    def test_all_versions_remain_and_use_descending_semver_order(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            newer = json.loads(fixture.release_path.read_text(encoding="utf-8"))
            newer["version"] = "1.10.0"
            newer["publishedAt"] = "2026-08-20T00:00:00Z"
            newer["releaseNotesUrl"] = (
                "https://github.com/example/test/releases/tag/v1.10.0"
            )
            newer["download"] = {
                "url": "https://github.com/example/test/releases/download/v1.10.0/dev.example.test-1.10.0.zip",
                "sha256": "b" * 64,
                "size": 456,
            }
            write_json(
                fixture.root
                / "plugins"
                / fixture.plugin_id
                / "releases"
                / "1.10.0.json",
                newer,
            )

            index = self.build_fixture(fixture)

            self.assertEqual(
                [item["version"] for item in index["plugins"][0]["releases"]],
                ["1.10.0", "1.0.0"],
            )

    def test_active_plugin_missing_central_history_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            shutil.rmtree(fixture.root / "plugins" / fixture.plugin_id)
            shutil.rmtree(fixture.root / "reviews" / fixture.plugin_id)
            with self.assertRaises(ValidationFailure):
                self.build_fixture(fixture)

    def test_active_plugin_list_contains_only_publisher_identity_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            write_json(
                fixture.root / "plugins.json",
                [
                    {
                        "id": fixture.plugin_id,
                        "repositoryUrl": "https://github.com/example/test",
                        "repositoryId": 1001,
                        "ownerId": 101,
                        "name": "Metadata belongs in the publisher manifest",
                    }
                ],
            )
            with self.assertRaises(ValidationFailure):
                self.build_fixture(fixture)

    def test_active_plugin_list_requires_numeric_github_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            write_json(
                fixture.root / "plugins.json",
                [
                    {
                        "id": fixture.plugin_id,
                        "repositoryUrl": "https://github.com/example/test",
                    }
                ],
            )
            with (
                patch.object(validator, "ROOT", fixture.root),
                self.assertRaises(ValidationFailure),
            ):
                validator.load_plugin_list()

    def test_schema1_archived_plugin_stays_in_v1_when_every_release_is_yanked(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            write_json(fixture.root / "plugins.json", [])
            release = json.loads(fixture.release_path.read_text(encoding="utf-8"))
            release["yanked"] = True
            release["yankReason"] = "Archived by registry administrator."
            write_json(fixture.release_path, release)

            index = self.build_fixture(fixture)

            self.assertEqual(index["plugins"][0]["id"], fixture.plugin_id)
            self.assertTrue(index["plugins"][0]["releases"][0]["yanked"])
            self.assertNotIn("visibility", index["plugins"][0])
            self.assertNotIn("generation", index["plugins"][0]["releases"][0])

    def test_schema1_active_pointer_stays_in_v1_when_all_releases_are_yanked(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            release = json.loads(fixture.release_path.read_text(encoding="utf-8"))
            release["yanked"] = True
            release["yankReason"] = "Awaiting a fixed release."
            write_json(fixture.release_path, release)

            index = self.build_fixture(fixture)

            self.assertEqual(index["plugins"][0]["id"], fixture.plugin_id)
            self.assertTrue(index["plugins"][0]["releases"][0]["yanked"])
            self.assertNotIn("visibility", index["plugins"][0])
            self.assertNotIn("generation", index["plugins"][0]["releases"][0])

    def test_archived_plugin_with_an_active_release_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            write_json(fixture.root / "plugins.json", [])
            with self.assertRaises(ValidationFailure):
                self.build_fixture(fixture)

    def test_untrusted_reviewer_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            review = json.loads(fixture.review_path.read_text(encoding="utf-8"))
            review["stateBy"] = "NotTrusted"
            write_json(fixture.review_path, review)
            with self.assertRaises(ValidationFailure):
                self.build_fixture(fixture)

    def test_boolean_integer_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            release = json.loads(fixture.release_path.read_text(encoding="utf-8"))
            release["download"]["size"] = True
            write_json(fixture.release_path, release)
            with self.assertRaises(ValidationFailure):
                self.build_fixture(fixture)

    def test_schema_reference_must_be_json_string(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            plugin = json.loads(fixture.plugin_path.read_text(encoding="utf-8"))
            plugin["$schema"] = True
            write_json(fixture.plugin_path, plugin)
            with self.assertRaises(ValidationFailure):
                self.build_fixture(fixture)

    def test_catalog_author_length_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            plugin = json.loads(fixture.plugin_path.read_text(encoding="utf-8"))
            plugin["authors"] = ["x" * 257]
            write_json(fixture.plugin_path, plugin)
            with self.assertRaises(ValidationFailure):
                self.build_fixture(fixture)

    def test_central_description_matches_nonempty_publisher_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            plugin = json.loads(fixture.plugin_path.read_text(encoding="utf-8"))
            plugin["description"] = ""
            write_json(fixture.plugin_path, plugin)
            with self.assertRaises(ValidationFailure):
                self.build_fixture(fixture)

    def test_total_capability_count_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = RegistryFixture(Path(directory))
            release = json.loads(fixture.release_path.read_text(encoding="utf-8"))
            with patch.object(
                validator,
                "KNOWN_CAPABILITIES",
                {f"capability.{index}" for index in range(65)},
            ):
                release["requiredCapabilities"] = [
                    f"capability.{index}" for index in range(33)
                ]
                release["optionalCapabilities"] = [
                    f"capability.{index}" for index in range(33, 65)
                ]
                write_json(fixture.release_path, release)
                with self.assertRaises(ValidationFailure):
                    self.build_fixture(fixture)

    def test_schema_documents_are_valid_json_and_use_canonical_owner(self):
        schemas = validator.ROOT / "schemas"
        for path in schemas.glob("*.schema.json"):
            with self.subTest(path=path.name):
                value = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn("TouristH/UNuse", value["$id"])


if __name__ == "__main__":
    unittest.main()
