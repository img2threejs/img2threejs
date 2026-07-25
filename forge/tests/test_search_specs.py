#!/usr/bin/env python3
"""Spec-search contract and lifecycle tests. # noqa: SIZE_OK"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC_SEARCH_PATH = ROOT / "forge/_shared/spec_search.py"
SEARCH_SPECS_CLI = ROOT / "forge/stage1_intake/search_specs.py"
SPEC_SEARCH_SPEC = importlib.util.spec_from_file_location("spec_search", SPEC_SEARCH_PATH)
assert SPEC_SEARCH_SPEC is not None and SPEC_SEARCH_SPEC.loader is not None
SPEC_SEARCH_MODULE = importlib.util.module_from_spec(SPEC_SEARCH_SPEC)
sys.modules[SPEC_SEARCH_SPEC.name] = SPEC_SEARCH_MODULE
SPEC_SEARCH_SPEC.loader.exec_module(SPEC_SEARCH_MODULE)
SourceIngestionError = SPEC_SEARCH_MODULE.SourceIngestionError
LoaderRecordValidationError = SPEC_SEARCH_MODULE.SpecRecordValidationError
ProfileValidationError = SPEC_SEARCH_MODULE.ProfileValidationError
UnknownCollectionError = SPEC_SEARCH_MODULE.UnknownCollectionError
Bm25Config = SPEC_SEARCH_MODULE.Bm25Config
CacheWriteError = SPEC_SEARCH_MODULE.CacheWriteError
CacheValidationError = SPEC_SEARCH_MODULE.CacheValidationError
IndexRequest = SPEC_SEARCH_MODULE.IndexRequest
SearchOutputRequest = SPEC_SEARCH_MODULE.SearchOutputRequest
SourceDocument = SPEC_SEARCH_MODULE.SourceDocument
build_index = SPEC_SEARCH_MODULE.build_index
ingest_source_tree = SPEC_SEARCH_MODULE.ingest_source_tree
load_or_build_index = SPEC_SEARCH_MODULE.load_or_build_index
load_jsonl_records = SPEC_SEARCH_MODULE.load_jsonl_records
load_profile = SPEC_SEARCH_MODULE.load_profile
load_profiles = SPEC_SEARCH_MODULE.load_profiles
search_index = SPEC_SEARCH_MODULE.search_index
serialize_search_results = SPEC_SEARCH_MODULE.serialize_search_results
tokenize = SPEC_SEARCH_MODULE.tokenize


CONTRACT_README = ROOT / "docs/specs/vocabulary/README.md"
REQUIRED_STRING_FIELDS = (
    "record_id",
    "collection",
    "domain",
    "kind",
    "entity",
    "title",
    "content",
    "observation_status",
)
REQUIRED_LIST_FIELDS = ("aliases", "constraints", "measurements", "source_refs", "evidence_refs", "assumptions")
DISTILLED_RECORD_PATHS = (
    ROOT / "docs/specs/vocabulary/core_3d.jsonl",
    ROOT / "docs/specs/vocabulary/cs2.jsonl",
)
DISTILLED_RECORD_FIELDS = frozenset((*REQUIRED_STRING_FIELDS, *REQUIRED_LIST_FIELDS, "confidence"))


class SpecRecordValidationError(ValueError):
    def __init__(self, path, line_number, reason):
        self.path = path
        self.line_number = line_number
        self.reason = reason
        super().__init__(f"{path}:{line_number}: {reason}")


def make_record():
    return {
        "record_id": "cs2.karambit.safety-ring",
        "collection": "cs2",
        "domain": "weapon-anatomy",
        "kind": "component",
        "entity": "karambit safety ring",
        "title": "Karambit safety ring / Vòng ngón Karambit",
        "aliases": ["safety ring", "finger ring", "vòng ngón"],
        "content": "A retention ring at the Karambit's pommel.",
        "constraints": ["Preserve the opening as a distinct component."],
        "measurements": [{"name": "opening diameter", "value": "source-dependent", "unit": "mm"}],
        "source_refs": [
            {"path": "docs/cs2/3D_Technical_Mapping.json", "key_path": "karambit.components.safety_ring"},
            {"path": "docs/cs2-anatomy/karambit.md", "heading": "Safety ring"},
        ],
        "evidence_refs": [{"kind": "source", "ref": "docs/cs2/3D_Technical_Mapping.json"}],
        "observation_status": "observed",
        "confidence": 0.9,
        "assumptions": [],
    }


def write_jsonl(path, records):
    rows = (json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def make_profile(
    tokenizer_version="1",
    k1=1.5,
    *,
    casefold=True,
    cache=".cache/spec-search/fixture.json",
    aliases_enabled=True,
    term_aliases=None,
):
    return {
        "source_roots": ["specs"],
        "optional_source_roots": [],
        "distilled_records": [],
        "cache": cache,
        "documentation": "README.md",
        "encoding": "utf-8",
        "languages": ["en", "vi"],
        "source_extensions": [".md"],
        "tokenizer": {
            "version": tokenizer_version,
            "unicode_normalization": "NFKC",
            "casefold": casefold,
            "accent_fold": "vi",
            "preserve_identifiers": True,
            "preserve_numbers": True,
        },
        "aliases": {"enabled": aliases_enabled, "max_expansions": 1},
        "bm25": {"k1": k1, "b": 0.75},
        "term_aliases": {} if term_aliases is None else term_aliases,
    }


def write_fixture_source(root, content="# Safety ring\nKarambit retention component.\n"):
    source_root = root / "specs"
    source_root.mkdir()
    source = source_root / "guide.md"
    source.write_text(content, encoding="utf-8")
    return source


def write_cli_fixture(
    root,
    *,
    malformed_profile=False,
    include_raw_roots=True,
    cache_path=".cache/spec-search/cs2.json",
):
    shared = root / "forge/_shared"
    intake = root / "forge/stage1_intake"
    shared.mkdir(parents=True, exist_ok=True)
    intake.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SPEC_SEARCH_PATH, shared / "spec_search.py")
    if SEARCH_SPECS_CLI.is_file():
        shutil.copyfile(SEARCH_SPECS_CLI, intake / "search_specs.py")

    if include_raw_roots:
        raw_docs = root / "docs/raw"
        raw_docs.mkdir(parents=True, exist_ok=True)
        long_section = (
            " ".join(["alpha"] * 20)
            + " earliestneedle "
            + " ".join(["middle"] * 15)
            + " needle "
            + " ".join(["omega"] * 20)
        )
        (raw_docs / "guide.md").write_text(
            "# Knife Guide\n"
            "General geometry.\n\n"
            "## Safety Ring\n"
            f"{long_section}\n\n"
            "## Roughness\n"
            "Roughness and độ nhám control the finish response.\n",
            encoding="utf-8",
        )
    records = root / "docs/specs/vocabulary"
    records.mkdir(parents=True, exist_ok=True)
    duplicate_records = []
    for record_id in ("cs2.fixture.safety-a", "cs2.fixture.safety-b"):
        record = make_record()
        record["record_id"] = record_id
        record["content"] = "earliestneedle needle"
        record["source_refs"] = [{"path": "docs/raw/guide.md", "heading": "Safety Ring"}]
        duplicate_records.append(record)
    write_jsonl(records / "cs2.jsonl", duplicate_records)

    if malformed_profile:
        profile_text = '{"profile_schema_version":1,"defaults":{}}'
    else:
        profile_text = json.dumps(
            {
                "profile_schema_version": 1,
                "defaults": {
                    "encoding": "utf-8",
                    "languages": ["en", "vi"],
                    "source_extensions": [".md", ".json", ".jsonl"],
                    "tokenizer": {
                        "version": "1",
                        "unicode_normalization": "NFKC",
                        "casefold": True,
                        "accent_fold": "vi",
                        "preserve_identifiers": True,
                        "preserve_numbers": True,
                    },
                    "aliases": {"enabled": True, "max_expansions": 1},
                    "bm25": {"k1": 1.5, "b": 0.75},
                },
                "collections": {
                    "cs2": {
                        "source_roots": [],
                        "optional_source_roots": ["docs/raw"],
                        "distilled_records": ["docs/specs/vocabulary/cs2.jsonl"],
                        "documentation": "docs/README.md",
                        "cache": cache_path,
                    }
                },
            },
            ensure_ascii=False,
        )
    (shared / "spec_search_profiles.json").write_text(profile_text, encoding="utf-8")
    return intake / "search_specs.py"


def run_cli_fixture(
    root,
    *arguments,
    malformed_profile=False,
    include_raw_roots=True,
    cache_path=".cache/spec-search/cs2.json",
):
    cli = write_cli_fixture(
        root,
        malformed_profile=malformed_profile,
        include_raw_roots=include_raw_roots,
        cache_path=cache_path,
    )
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, str(cli), *arguments],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def load_contract_fixture(path):
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise SpecRecordValidationError(path, line_number, "invalid JSON object") from error
        validate_record(record, path, line_number)
        records.append(record)
    return records


def validate_record(record, path, line_number):
    if type(record) is not dict:
        raise SpecRecordValidationError(path, line_number, "record must be a JSON object")
    for field in REQUIRED_STRING_FIELDS:
        if type(record.get(field)) is not str or not record[field]:
            raise SpecRecordValidationError(path, line_number, f"{field} must be a non-empty string")
    for field in REQUIRED_LIST_FIELDS:
        if type(record.get(field)) is not list:
            raise SpecRecordValidationError(path, line_number, f"{field} must be an array")
    if record["observation_status"] not in {"observed", "inferred", "unverified"}:
        raise SpecRecordValidationError(path, line_number, "observation_status is invalid")
    confidence = record.get("confidence")
    if type(confidence) is bool or not isinstance(confidence, (int, float)):
        raise SpecRecordValidationError(path, line_number, "confidence must be a number from 0 through 1")
    if not 0 <= confidence <= 1:
        raise SpecRecordValidationError(path, line_number, "confidence must be a number from 0 through 1")
    validate_string_array(record["aliases"], "aliases", path, line_number)
    validate_string_array(record["constraints"], "constraints", path, line_number)
    validate_string_array(record["assumptions"], "assumptions", path, line_number)
    validate_measurements(record["measurements"], path, line_number)
    validate_source_refs(record["source_refs"], path, line_number)
    validate_evidence_refs(record["evidence_refs"], path, line_number)


def validate_string_array(values, field, path, line_number):
    if not all(type(value) is str for value in values):
        raise SpecRecordValidationError(path, line_number, f"{field} entries must be strings")


def validate_measurements(measurements, path, line_number):
    for measurement in measurements:
        if type(measurement) is not dict:
            raise SpecRecordValidationError(path, line_number, "measurements entries must be objects")
        for field in ("name", "value"):
            if type(measurement.get(field)) is not str or not measurement[field]:
                raise SpecRecordValidationError(path, line_number, f"measurements.{field} must be a non-empty string")
        for field in ("unit", "context"):
            if field in measurement and type(measurement[field]) is not str:
                raise SpecRecordValidationError(path, line_number, f"measurements.{field} must be a string")


def validate_source_refs(source_refs, path, line_number):
    if not source_refs:
        raise SpecRecordValidationError(path, line_number, "source_refs must not be empty")
    for source_ref in source_refs:
        if type(source_ref) is not dict or type(source_ref.get("path")) is not str or not source_ref["path"]:
            raise SpecRecordValidationError(path, line_number, "source_refs entries require a non-empty path")
        for field in ("heading", "key_path"):
            if field in source_ref and (type(source_ref[field]) is not str or not source_ref[field]):
                raise SpecRecordValidationError(path, line_number, f"source_refs.{field} must be a non-empty string")


def validate_evidence_refs(evidence_refs, path, line_number):
    for evidence_ref in evidence_refs:
        if type(evidence_ref) is not dict:
            raise SpecRecordValidationError(path, line_number, "evidence_refs entries must be objects")
        for field in ("kind", "ref"):
            if type(evidence_ref.get(field)) is not str or not evidence_ref[field]:
                raise SpecRecordValidationError(path, line_number, f"evidence_refs.{field} must be a non-empty string")
        if "note" in evidence_ref and type(evidence_ref["note"]) is not str:
            raise SpecRecordValidationError(path, line_number, "evidence_refs.note must be a string")


class RecordContractTest(unittest.TestCase):
    def test_future_jsonl_loader_api_is_documented(self):
        contract = CONTRACT_README.read_text(encoding="utf-8")

        self.assertIn("load_jsonl_records(path: Path)", contract)
        self.assertIn("SpecRecordValidationError", contract)

    def test_cs2_record_round_trips_with_bilingual_aliases_and_provenance(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture_path = Path(temporary_directory) / "records.jsonl"
            record = make_record()
            write_jsonl(fixture_path, [record])

            parsed_records = load_contract_fixture(fixture_path)

        self.assertEqual(parsed_records, [record])
        self.assertEqual(parsed_records[0]["aliases"], ["safety ring", "finger ring", "vòng ngón"])
        self.assertEqual(parsed_records[0]["source_refs"][0]["key_path"], "karambit.components.safety_ring")
        self.assertEqual(parsed_records[0]["source_refs"][1]["heading"], "Safety ring")

    def test_malformed_jsonl_raises_named_validation_error_instead_of_skipping(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            fixture_path = Path(temporary_directory) / "broken.jsonl"
            write_jsonl(fixture_path, [make_record()])
            fixture_path.write_text(fixture_path.read_text(encoding="utf-8") + '{"record_id":\n', encoding="utf-8")

            with self.assertRaisesRegex(SpecRecordValidationError, r"broken\.jsonl:2: invalid JSON object"):
                load_contract_fixture(fixture_path)


class DistilledRecordTest(unittest.TestCase):
    def test_reviewed_bilingual_records_are_complete_and_source_backed(self):
        records_by_path = {path: load_contract_fixture(path) for path in DISTILLED_RECORD_PATHS}
        core_records = records_by_path[DISTILLED_RECORD_PATHS[0]]
        cs2_records = records_by_path[DISTILLED_RECORD_PATHS[1]]
        all_records = [record for records in records_by_path.values() for record in records]

        self.assertGreaterEqual(len(core_records), 12)
        self.assertGreaterEqual(len(cs2_records), 8)
        self.assertEqual(len({record["record_id"] for record in all_records}), len(all_records))
        self.assertTrue(all(set(record) <= DISTILLED_RECORD_FIELDS for record in all_records))
        self.assertTrue(all(record["source_refs"] for record in all_records))

        aliases = {alias.casefold() for record in all_records for alias in record["aliases"]}
        self.assertTrue(
            {
                "socket",
                "ổ cắm",
                "pivot",
                "điểm xoay",
                "roughness",
                "độ thô",
                "wear",
                "hao mòn",
                "safety ring",
                "vòng ngón",
                "pommel",
                "chuôi cuối",
                "attachment",
                "gắn kết",
            }.issubset(aliases)
        )

        source_paths = {source_ref["path"] for record in all_records for source_ref in record["source_refs"]}
        self.assertTrue(
            {
                "grimoire/glossary/3d_vocabulary.md",
                "grimoire/readiness/action_rigging.md",
                "grimoire/readiness/joint_attachment.md",
                "grimoire/intake/image_analysis.md",
                "docs/cs2/3D_Technical_Mapping.json",
                "docs/cs2/3D_Vocabulary_CS2.json",
                "docs/cs2/distill.md",
                "docs/cs2-anatomy/knives.md",
            }.issubset(source_paths)
        )


class TokenizerTest(unittest.TestCase):
    def test_mixed_language_tokens_preserve_identifiers_numbers_and_accents(self):
        text = "Safety ring, vòng ngón, độ nhám/do nham; AK-47 MeshPhysicalMaterial 0.05-0.15 rings"

        tokens = tokenize(text)

        self.assertTrue(
            {
                "safety",
                "ring",
                "vòng",
                "vong",
                "ngón",
                "ngon",
                "độ",
                "do",
                "nhám",
                "nham",
                "ak-47",
                "meshphysicalmaterial",
                "0.05-0.15",
                "rings",
            }.issubset(tokens)
        )
        self.assertEqual(tokens.count("ring"), 1)

    def test_profile_alias_expansion_is_deterministic(self):
        aliases_a = {
            "roughness": ("độ nhám",),
            "safety ring": ("vòng ngón", "finger ring"),
        }
        aliases_b = dict(reversed(tuple(aliases_a.items())))

        tokens_a = tokenize("Safety ring roughness", aliases_a)
        tokens_b = tokenize("Safety ring roughness", aliases_b)

        self.assertEqual(tokens_a, tokens_b)
        self.assertTrue({"vòng", "vong", "ngón", "ngon", "finger", "độ", "do", "nhám", "nham"}.issubset(tokens_a))


class SourceIngestionTest(unittest.TestCase):
    def test_profile_loader_exposes_cs2_cache_and_documentation(self):
        profile = load_profiles()["cs2"]

        self.assertEqual(profile["cache"], ".cache/spec-search/cs2.json")
        self.assertEqual(profile["documentation"], "docs/specs/vocabulary/README.md")
        self.assertEqual(profile["encoding"], "utf-8")
        self.assertEqual(profile["source_roots"], [])
        self.assertEqual(profile["optional_source_roots"], ["docs/cs2/", "docs/cs2-anatomy/"])

    def test_profile_loader_rejects_malformed_and_unknown_collections(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            malformed_profile = Path(temporary_directory) / "profiles.json"
            malformed_profile.write_text('{"profile_schema_version":1,"defaults":{}}', encoding="utf-8")

            with self.assertRaisesRegex(ProfileValidationError, r"defaults\.tokenizer must be an object"):
                load_profiles(malformed_profile)
            with self.assertRaisesRegex(UnknownCollectionError, r"unknown spec-search collection: missing"):
                load_profile("missing")

    def test_markdown_sections_retain_heading_and_source_path(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "guide.md").write_text(
                "# Karambit\nCurved blade.\n\n## Safety Ring\nRetains the grip.\n",
                encoding="utf-8",
            )

            documents = ingest_source_tree(root, "fixture", (".md",))

        safety_ring = next(document for document in documents if document.heading == "Safety Ring")
        self.assertEqual(safety_ring.file_path, "guide.md")
        self.assertEqual(safety_ring.content, "Retains the grip.")

    def test_nested_json_values_retain_key_path_and_source_path(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "mapping.json").write_text(
                '{"karambit":{"components":{"safety_ring":{"diameter_mm":22}}}}',
                encoding="utf-8",
            )

            documents = ingest_source_tree(root, "fixture", (".json",))

        diameter = next(document for document in documents if document.key_path.endswith("diameter_mm"))
        self.assertEqual(diameter.file_path, "mapping.json")
        self.assertEqual(diameter.key_path, "karambit.components.safety_ring.diameter_mm")
        self.assertEqual(diameter.content, "22")

    def test_hidden_directories_and_generated_caches_are_excluded(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / ".hidden").mkdir()
            (root / ".hidden" / "broken.json").write_text("{", encoding="utf-8")
            (root / ".hidden.md").write_text("# Hidden\nMust not be indexed.\n", encoding="utf-8")
            (root / ".cache" / "spec-search").mkdir(parents=True)
            (root / ".cache" / "spec-search" / "broken.json").write_text("{", encoding="utf-8")
            (root / "visible.md").write_text("# Visible\nSearchable.\n", encoding="utf-8")

            documents = ingest_source_tree(root, "fixture", (".md", ".json"))

        self.assertEqual([document.file_path for document in documents], ["visible.md"])

    def test_symlink_source_file_is_rejected_without_indexing_target(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_root = root / "specs"
            source_root.mkdir()
            secret = root / "secret.md"
            secret.write_text("# Secret\nmust-not-be-indexed\n", encoding="utf-8")
            (source_root / "linked.md").symlink_to(secret)
            profile = make_profile()

            with self.assertRaisesRegex(SourceIngestionError, r"linked\.md: symbolic links are not allowed"):
                load_or_build_index(IndexRequest(root, "fixture", profile))

            cache_exists = (root / ".cache/spec-search/fixture.json").exists()

        self.assertFalse(cache_exists)

    def test_symlink_source_root_is_rejected_without_indexing_target(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            secret_root = root / "secret"
            secret_root.mkdir()
            (secret_root / "secret.md").write_text(
                "# Secret\nmust-not-be-indexed\n",
                encoding="utf-8",
            )
            (root / "specs").symlink_to(secret_root, target_is_directory=True)

            with self.assertRaisesRegex(
                SourceIngestionError,
                r"specs: symbolic links are not allowed",
            ):
                load_or_build_index(
                    IndexRequest(root, "fixture", make_profile())
                )

            cache_exists = (root / ".cache/spec-search/fixture.json").exists()

        self.assertFalse(cache_exists)

    def test_missing_optional_root_is_skipped_but_required_root_is_not(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            records = root / "records"
            records.mkdir()
            write_jsonl(records / "fixture.jsonl", [make_record()])
            optional_profile = make_profile()
            optional_profile["source_roots"] = []
            optional_profile["optional_source_roots"] = ["missing-raw"]
            optional_profile["distilled_records"] = ["records/fixture.jsonl"]

            loaded = load_or_build_index(IndexRequest(root, "fixture", optional_profile))

            required_profile = make_profile(cache=".cache/spec-search/required.json")
            with self.assertRaisesRegex(SourceIngestionError, r"specs: configured source root is not a directory"):
                load_or_build_index(IndexRequest(root, "fixture", required_profile))

        self.assertTrue(loaded.index.records)

    def test_existing_unreadable_optional_root_raises_typed_source_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_root = root / "raw"
            raw_root.mkdir()
            profile = make_profile()
            profile["source_roots"] = []
            profile["optional_source_roots"] = ["raw"]

            original_iterdir = Path.iterdir

            def unreadable_iterdir(path):
                if path == raw_root:
                    raise PermissionError("denied")
                return original_iterdir(path)

            with mock.patch.object(Path, "iterdir", unreadable_iterdir):
                with self.assertRaisesRegex(SourceIngestionError, r"raw: unable to read configured source root"):
                    load_or_build_index(IndexRequest(root, "fixture", profile))

    def test_malformed_configured_json_and_jsonl_raise_named_errors(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            valid_jsonl = root / "records.jsonl"
            write_jsonl(valid_jsonl, [make_record()])
            malformed_json = root / "broken.json"
            malformed_json.write_text('{"blade":', encoding="utf-8")
            malformed_jsonl = root / "broken.jsonl"
            malformed_jsonl.write_text('{"record_id":\n', encoding="utf-8")

            self.assertEqual(load_jsonl_records(valid_jsonl), [make_record()])
            with self.assertRaisesRegex(SourceIngestionError, r"broken\.json: invalid JSON source"):
                ingest_source_tree(root, "fixture", (".json",))
            with self.assertRaisesRegex(LoaderRecordValidationError, r"broken\.jsonl:1: invalid JSON object"):
                load_jsonl_records(malformed_jsonl)


class BM25Test(unittest.TestCase):
    def test_rare_exact_terms_beat_longer_generic_document(self):
        documents = [
            SourceDocument(
                record_id="fixture.exact",
                collection="fixture",
                title="Karambit safety ring",
                content="Retention opening.",
                file_path="exact.md",
            ),
            SourceDocument(
                record_id="fixture.generic",
                collection="fixture",
                title="Ring overview",
                content=" ".join(["generic component material geometry"] * 30),
                file_path="generic.md",
            ),
        ]

        matches = search_index(build_index(documents, Bm25Config(collection="fixture")), "karambit safety ring")

        self.assertEqual([match.record.record_id for match in matches], ["fixture.exact", "fixture.generic"])
        self.assertGreater(matches[0].score, matches[1].score)

    def test_equal_scores_tie_by_record_id_ascending(self):
        documents = [
            SourceDocument(
                record_id=record_id,
                collection="fixture",
                title="Shared title",
                content="identical searchable terms",
                file_path=f"{record_id}.md",
            )
            for record_id in ("fixture.z", "fixture.a")
        ]

        matches = search_index(build_index(documents, Bm25Config(collection="fixture")), "searchable")

        self.assertEqual([match.record.record_id for match in matches], ["fixture.a", "fixture.z"])
        self.assertEqual(matches[0].score, matches[1].score)

    def test_profile_casefold_setting_changes_index_and_query_tokens(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write_fixture_source(root, "# MixedCase\nCamelToken\n")

            folded = load_or_build_index(IndexRequest(root, "fixture", make_profile()))
            exact_case = load_or_build_index(
                IndexRequest(
                    root,
                    "fixture",
                    make_profile(casefold=False, cache=".cache/spec-search/exact.json"),
                )
            )

        self.assertTrue(search_index(folded.index, "cameltoken"))
        self.assertEqual(search_index(exact_case.index, "cameltoken"), [])
        self.assertTrue(search_index(exact_case.index, "CamelToken"))

    def test_enabled_term_alias_query_matches_expansion_and_disabled_does_not(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write_fixture_source(root, "# Hook blade\nhookblade profile\n")
            aliases = {"karambit": ["hookblade"]}

            enabled = load_or_build_index(
                IndexRequest(root, "fixture", make_profile(term_aliases=aliases))
            )
            disabled = load_or_build_index(
                IndexRequest(
                    root,
                    "fixture",
                    make_profile(
                        cache=".cache/spec-search/no-alias.json",
                        aliases_enabled=False,
                        term_aliases=aliases,
                    ),
                )
            )

        self.assertTrue(search_index(enabled.index, "karambit"))
        self.assertEqual(search_index(disabled.index, "karambit"), [])


class CacheLifecycleTest(unittest.TestCase):
    def test_notebooklm_evidence_refs_survive_cache_hit_and_cli_serialization(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            records = root / "records"
            records.mkdir()
            record = make_record()
            record["evidence_refs"] = [
                {
                    "kind": "notebooklm",
                    "ref": "notebook://cs2/source-7",
                    "note": "distilled observation",
                }
            ]
            write_jsonl(records / "fixture.jsonl", [record])
            profile = make_profile()
            profile["source_roots"] = []
            profile["distilled_records"] = ["records/fixture.jsonl"]

            load_or_build_index(IndexRequest(root, "fixture", profile))
            cached = load_or_build_index(IndexRequest(root, "fixture", profile))
            serialized = serialize_search_results(
                cached.index,
                SearchOutputRequest("retention ring", 1, 120),
            )

        self.assertEqual(cached.status, "hit")
        self.assertEqual(
            serialized[0]["evidence_refs"],
            [
                {
                    "kind": "notebooklm",
                    "ref": "notebook://cs2/source-7",
                    "note": "distilled observation",
                }
            ],
        )

    def test_cache_path_rejects_traversal_and_absolute_values_before_write(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            outer = Path(temporary_directory)
            root = outer / "project"
            root.mkdir()
            write_fixture_source(root)
            scenarios = (
                ("../escape.json", outer / "escape.json"),
                (str(outer / "absolute.json"), outer / "absolute.json"),
            )

            for configured_cache, escaped_path in scenarios:
                with self.subTest(configured_cache=configured_cache):
                    with self.assertRaisesRegex(
                        CacheValidationError,
                        r"cache path must be relative and stay within project_root",
                    ):
                        load_or_build_index(
                            IndexRequest(
                                root,
                                "fixture",
                                make_profile(cache=configured_cache),
                            )
                        )
                    self.assertFalse(escaped_path.exists())

    def test_cache_hit_avoids_source_reparse_and_preserves_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write_fixture_source(root)
            profile = make_profile()

            initial = load_or_build_index(IndexRequest(root, "fixture", profile))
            cache_path = root / ".cache/spec-search/fixture.json"
            initial_cache = json.loads(cache_path.read_text(encoding="utf-8"))
            with mock.patch.object(
                SPEC_SEARCH_MODULE,
                "ingest_markdown",
                side_effect=AssertionError("cache hit reparsed source"),
            ):
                cached = load_or_build_index(IndexRequest(root, "fixture", profile))

        self.assertEqual(initial.status, "rebuilt")
        self.assertEqual(initial.reason, "missing")
        self.assertEqual(cached.status, "hit")
        self.assertEqual(cached.fingerprint, initial.fingerprint)
        self.assertEqual(
            {
                "schema_fingerprint",
                "tokenizer_fingerprint",
                "config_fingerprint",
                "source_fingerprint",
                "document_lengths",
                "avgdl",
                "document_frequencies",
                "postings",
                "records",
            }
            - initial_cache.keys(),
            set(),
        )

    def test_source_mutation_rebuilds_stale_cache(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = write_fixture_source(root, "# Safety ring\nalpha beta gamma.\n")
            original_stat = source.stat()
            profile = make_profile()
            initial = load_or_build_index(IndexRequest(root, "fixture", profile))

            source.write_text("# Safety ring\nomega beta gamma.\n", encoding="utf-8")
            os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            rebuilt = load_or_build_index(IndexRequest(root, "fixture", profile))

        self.assertEqual(rebuilt.status, "rebuilt")
        self.assertEqual(rebuilt.reason, "stale")
        self.assertNotEqual(rebuilt.index.fingerprints.source, initial.index.fingerprints.source)

    def test_config_tokenizer_and_schema_changes_each_rebuild(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write_fixture_source(root)
            initial = load_or_build_index(IndexRequest(root, "fixture", make_profile()))

            config_changed = load_or_build_index(IndexRequest(root, "fixture", make_profile(k1=1.2)))
            tokenizer_changed = load_or_build_index(
                IndexRequest(root, "fixture", make_profile(tokenizer_version="2", k1=1.2))
            )
            with mock.patch.object(
                SPEC_SEARCH_MODULE,
                "_CACHE_SCHEMA_VERSION",
                SPEC_SEARCH_MODULE._CACHE_SCHEMA_VERSION + 1,
            ):
                schema_changed = load_or_build_index(
                    IndexRequest(root, "fixture", make_profile(tokenizer_version="2", k1=1.2))
                )

        self.assertEqual(config_changed.reason, "stale")
        self.assertNotEqual(config_changed.index.fingerprints.config, initial.index.fingerprints.config)
        self.assertEqual(tokenizer_changed.reason, "stale")
        self.assertNotEqual(
            tokenizer_changed.index.fingerprints.tokenizer,
            config_changed.index.fingerprints.tokenizer,
        )
        self.assertEqual(schema_changed.reason, "stale")
        self.assertNotEqual(
            schema_changed.index.fingerprints.schema,
            tokenizer_changed.index.fingerprints.schema,
        )

    def test_force_reindex_rebuilds_unchanged_cache(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write_fixture_source(root)
            profile = make_profile()
            initial = load_or_build_index(IndexRequest(root, "fixture", profile))

            rebuilt = load_or_build_index(IndexRequest(root, "fixture", profile, force_reindex=True))

        self.assertEqual(rebuilt.status, "rebuilt")
        self.assertEqual(rebuilt.reason, "forced")
        self.assertEqual(rebuilt.fingerprint, initial.fingerprint)

    def test_corrupt_json_rebuilds_when_sources_are_available(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            write_fixture_source(root)
            profile = make_profile()
            load_or_build_index(IndexRequest(root, "fixture", profile))
            cache_path = root / ".cache/spec-search/fixture.json"
            cache_path.write_text('{"schema_version":', encoding="utf-8")

            rebuilt = load_or_build_index(IndexRequest(root, "fixture", profile))
            parsed = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertEqual(rebuilt.status, "rebuilt")
        self.assertEqual(rebuilt.reason, "corrupt")
        self.assertEqual(parsed["fingerprint"], rebuilt.fingerprint)

    def test_atomic_write_failure_cleans_temp_and_preserves_old_cache(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = write_fixture_source(root)
            profile = make_profile()
            load_or_build_index(IndexRequest(root, "fixture", profile))
            cache_path = root / ".cache/spec-search/fixture.json"
            old_cache = cache_path.read_text(encoding="utf-8")
            source.write_text("# Changed\nTrigger atomic replacement.\n", encoding="utf-8")

            with mock.patch.object(SPEC_SEARCH_MODULE.os, "replace", side_effect=OSError("interrupted")):
                with self.assertRaises(CacheWriteError):
                    load_or_build_index(IndexRequest(root, "fixture", profile))

            temporary_artifacts = list(cache_path.parent.glob(f".{cache_path.name}.*.tmp"))
            preserved_cache = cache_path.read_text(encoding="utf-8")

        self.assertEqual(preserved_cache, old_cache)
        self.assertEqual(temporary_artifacts, [])


class CliOutputTest(unittest.TestCase):
    def _assert_unsafe_profile_cache_path(self, configured_cache, outside_name):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            outside = root.parent / f"{root.name}-{outside_name}"
            result = run_cli_fixture(
                root,
                "needle",
                "--json",
                cache_path=configured_cache(root, outside),
            )

            self.assertEqual(result.returncode, 3, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["error"]["code"], "cache_failure")
            self.assertNotIn(str(root), result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse(outside.exists())

    def test_traversal_profile_cache_path_returns_cache_failure_without_disclosure_or_write(self):
        self._assert_unsafe_profile_cache_path(
            lambda _root, outside: "../" + outside.name,
            "escape.json",
        )

    def test_absolute_profile_cache_path_returns_cache_failure_without_disclosure_or_write(self):
        self._assert_unsafe_profile_cache_path(
            lambda root, _outside: str(root / "absolute.json"),
            "absolute.json",
        )

    def test_symlinked_cache_parent_returns_structured_cache_failure(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            cli = write_cli_fixture(root)
            outside = root.parent / f"{root.name}-outside"
            outside.mkdir()
            (root / ".cache").mkdir()
            (root / ".cache" / "spec-search").symlink_to(outside, target_is_directory=True)

            result = subprocess.run(
                [sys.executable, str(cli), "needle", "--json"],
                cwd=root,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True,
                text=True,
                check=False,
            )

            payload = json.loads(result.stdout)
            self.assertEqual(result.returncode, 3)
            self.assertEqual(payload["error"]["code"], "cache_failure")
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse((outside / "cs2.json").exists())

    def test_clean_style_project_searches_committed_distilled_records_without_raw_roots(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            result = run_cli_fixture(
                root,
                "retention",
                "ring",
                "--json",
                include_raw_roots=False,
            )
            raw_root_exists = (root / "docs/raw").exists()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(raw_root_exists)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["matches"])
        self.assertEqual(payload["matches"][0]["record_id"], "cs2.fixture.safety-a")

    def test_json_output_has_stable_machine_consumed_shape(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = run_cli_fixture(
                Path(temporary_directory),
                "--collection",
                "cs2",
                "safety",
                "ring",
                "vòng",
                "ngón",
                "--limit",
                "2",
                "--json",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(set(payload), {"query", "collection", "index", "matches"})
        self.assertEqual(payload["query"], "safety ring vòng ngón")
        self.assertEqual(payload["collection"], "cs2")
        self.assertEqual(set(payload["index"]), {"cache_path", "fingerprint", "reason", "status"})
        self.assertLessEqual(len(payload["matches"]), 2)
        self.assertTrue(payload["matches"])
        for match in payload["matches"]:
            self.assertTrue(
                {
                    "record_id",
                    "file_path",
                    "heading",
                    "key_path",
                    "score",
                    "snippets",
                    "source_refs",
                    "evidence_refs",
                }.issubset(match)
            )
            self.assertIsInstance(match["score"], float)
            self.assertIsInstance(match["snippets"], list)
            self.assertIsInstance(match["source_refs"], list)
            self.assertIsInstance(match["evidence_refs"], list)
        self.assertEqual(result.stderr, "")

    def test_human_output_is_nonempty_and_stable_on_cache_hits(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_cli_fixture(root, "roughness", "độ", "nhám")
            cached = run_cli_fixture(root, "roughness", "độ", "nhám")
            repeated = run_cli_fixture(root, "roughness", "độ", "nhám")

        self.assertEqual(cached.returncode, 0, cached.stderr)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertTrue(cached.stdout.strip())
        self.assertEqual(cached.stdout, repeated.stdout)

    def test_validation_and_profile_errors_have_documented_codes(self):
        scenarios = (
            (("--json",), 2, "empty_query", False),
            (("--collection", "missing", "term", "--json"), 2, "unknown_collection", False),
            (("term", "--limit", "0", "--json"), 2, "invalid_limit", False),
            (("term", "--json"), 3, "profile_failure", True),
        )
        for arguments, expected_exit, expected_code, malformed_profile in scenarios:
            with self.subTest(arguments=arguments):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    result = run_cli_fixture(
                        Path(temporary_directory),
                        *arguments,
                        malformed_profile=malformed_profile,
                    )

                self.assertEqual(result.returncode, expected_exit, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(
                    set(payload),
                    {"query", "collection", "index", "matches", "error"},
                )
                self.assertEqual(payload["error"]["code"], expected_code)
                self.assertIsInstance(payload["error"]["message"], str)

    def test_no_match_is_success_with_empty_matches(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = run_cli_fixture(
                Path(temporary_directory),
                "term-that-cannot-match",
                "--json",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["matches"], [])

    def test_undersized_snippets_are_rejected_before_search(self):
        for snippet_chars in range(1, 5):
            with self.subTest(snippet_chars=snippet_chars):
                with tempfile.TemporaryDirectory() as temporary_directory:
                    result = run_cli_fixture(
                        Path(temporary_directory),
                        "roughness",
                        "--snippet-chars",
                        str(snippet_chars),
                        "--json",
                    )

                self.assertEqual(result.returncode, 2, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["error"]["code"], "invalid_snippet_chars")
                self.assertIn("at least 5", payload["error"]["message"])
                self.assertEqual(payload["matches"], [])


class SnippetTest(unittest.TestCase):
    def test_snippet_is_bounded_centered_and_word_trimmed(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = run_cli_fixture(
                Path(temporary_directory),
                "earliestneedle",
                "needle",
                "--limit",
                "5",
                "--snippet-chars",
                "80",
                "--json",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        matches = json.loads(result.stdout)["matches"]
        self.assertEqual(len(matches), 1)
        snippet = matches[0]["snippets"][0]
        self.assertLessEqual(len(snippet), 80)
        self.assertIn("earliestneedle", snippet)
        self.assertTrue(snippet.startswith("... "))
        self.assertTrue(snippet.endswith(" ..."))
        body_words = snippet.removeprefix("... ").removesuffix(" ...").split()
        self.assertTrue(set(body_words) <= {"alpha", "earliestneedle", "middle", "needle", "omega"})

    def test_untruncated_snippet_has_no_ellipses(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = run_cli_fixture(
                Path(temporary_directory),
                "roughness",
                "--snippet-chars",
                "250",
                "--json",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        snippet = json.loads(result.stdout)["matches"][0]["snippets"][0]
        self.assertNotIn("...", snippet)


if __name__ == "__main__":
    unittest.main(verbosity=2)
