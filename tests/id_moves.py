"""Closed, source-qualified receipts for approved canonical owner moves."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

RECEIPT_PATH = Path(__file__).parent / "fixtures" / "test_id_moves_v1.json"
RECEIPT_SHA256 = "87fc781f32d1139547e68125fe84486df39af2ee5cde312dbaa0b679754edbae"
ORIGINAL_CAPS_SHA256 = "fc586b3eaa147b4ad4bc06e42e98378c50bd56a9fb09954219c20c45e882b99b"
S02_CAPS_SHA256 = "b554b945803dd496796fd0bf7cad3201c7f729c37821a90231fc734074576c52"
S03_CAPS_SHA256 = "7f3bdd0982191c0adef51815ae3445fa4a55c11dfcb5d7574179e0c542b0310c"
S05_CAPS_SHA256 = "8371eef31044fa33e0ee7e34af17bb3fb03e47556ac1b0f601124d13650b5298"
S06_CAPS_SHA256 = "0caff3ac47db80cd2344dc4ff480358ec9cc09d2ed68d151ffa2e754c100d156"
S07_CAPS_SHA256 = "ce4382b4bac0778363a7ea7ca90c5c81be63c89077b23abcc4dedbbc170167d9"
BEFORE_998_CAPS_SHA256 = "52d0575e721cf829d7ca7cc0409cd28861ac1db33b587b684d4611aff6df728c"
EXPECTED_CAPS_SHA256 = "2823d24a5e6436e5b6fbe2356047375707e88c3e73c5f784011e8efa37e30fe5"
ORIGINAL_ID = "tests.test_agent_context_planning.PlanningSurfaceTests.test_exact_dataclass_fields_and_immutability"
SECOND_ID = "tests.test_agent_context_sources.SourceSurfacePolicyAndValueTests.test_descriptor_snapshot_records_are_exact_immutable_and_content_hidden"
THIRD_ID = "tests.test_specialist_routing.SpecialistRoutingTests.test_scope_and_review_basis_codecs_are_closed_and_round_trip"
FOURTH_ID = "tests.test_lead_artifact_application.LeadArtifactApplicationTests.test_atomic_multi_file_apply_has_exact_provenance_and_no_git_effect"
FIFTH_ID = "tests.test_specialist_orchestration.SpecialistAccountingAcceptanceTests.test_local_reference_and_search_answer_count_only_provider_calls"
SIXTH_ID = "tests.test_specialist_orchestration_accounting.SpecialistAccountingAcceptanceTests.test_result_persistence_failure_cleans_marker_after_provider_completion"
SEVENTH_ID = "tests.test_lead_orchestration.LeadSchemaFactoringTests.test_small_factoring_annotations_and_owner_refs_are_exact"
STAGES = (
    (964, "d198abaf60edb1956acbd1f410ad3b1b9fbbedd8", ORIGINAL_CAPS_SHA256, S02_CAPS_SHA256, 45),
    (965, "a18dfc6b3faa8dc326668c77d0da12441be388f8", S02_CAPS_SHA256, S03_CAPS_SHA256, 117),
    (967, "ef8093f49dbab2b2fea619acef40a1c7572bc439", S03_CAPS_SHA256, S05_CAPS_SHA256, 10),
    (1006, "a231649d001ff30d29f927c6884339ce09e2edc3", S05_CAPS_SHA256, S06_CAPS_SHA256, 8),
    (1007, "b7dd8d8d986d92aad22aad8db44e0ad0d02a4a68", S06_CAPS_SHA256, S07_CAPS_SHA256, 22),
    (1020, "a1fba1a38327de3c5e7427bec4366a588bb62ba1", S07_CAPS_SHA256, BEFORE_998_CAPS_SHA256, 4),
    (998, "65ad7c277d1da5ad7b1cab931f48364607067d67", BEFORE_998_CAPS_SHA256, EXPECTED_CAPS_SHA256, 2),
)

def caps_digest(caps: dict[str, int]) -> str:
    return hashlib.sha256(json.dumps(caps, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def load_receipt() -> list[dict]:
    """Read all immutable, individually qualified stages without a cache."""
    try:
        raw = RECEIPT_PATH.read_bytes()
        receipt = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("cannot read canonical move receipt") from error
    if hashlib.sha256(raw).hexdigest() != RECEIPT_SHA256:
        raise ValueError("canonical move receipt differs approved source")
    stages = receipt.get("stages", [])
    if receipt.get("schema_version") != 2 or len(stages) != len(STAGES):
        raise ValueError("canonical move receipt differs approved stages")
    for stage, expected in zip(stages, STAGES):
        issue, origin, before, after, count = expected
        actual = tuple(stage.get(key) for key in ("issue", "origin_commit", "original_caps_sha256", "expected_caps_sha256"))
        moves = stage.get("moves", [])
        if stage.get("schema_version") != 1 or actual != expected[:4] or len(moves) != count:
            raise ValueError("canonical move stage differs approved metadata")
        if len({move["old_id"] for move in moves}) != count or len({move["new_id"] for move in moves}) != count:
            raise ValueError("canonical move receipt is not bijective")
    return stages

def remap_base_caps(current: dict[str, int], base: dict[str, int], *, digests: tuple[str, str] | None = None) -> dict[str, int]:
    """Rekey exact approved inventories; digests are hints and every resulting stage is independently verified."""
    if ORIGINAL_ID in current or (ORIGINAL_ID not in base and SECOND_ID not in base and THIRD_ID not in base and FOURTH_ID not in base and FIFTH_ID not in base and SIXTH_ID not in base and SEVENTH_ID not in base) or current == base:
        return base
    base_digest = caps_digest(base) if digests is None else digests[0]
    before = [stage[2] for stage in STAGES]
    if base_digest not in before:
        return base
    start = before.index(base_digest)
    ends = [stage[3] for stage in STAGES]
    current_digest = caps_digest(current) if digests is None else digests[1]
    if current_digest not in ends or ends.index(current_digest) < start:
        raise ValueError("canonical inventory differs approved move target")
    result = dict(base)
    for stage in load_receipt()[start:ends.index(current_digest) + 1]:
        for move in stage["moves"]:
            old, new, cap = move["old_id"], move["new_id"], move["subtest_cap"]
            if old not in result or new in result or result[old] != cap:
                raise ValueError("canonical move source or cap differs approved receipt")
            result[new] = result.pop(old)
        if caps_digest(result) != stage["expected_caps_sha256"]:
            raise ValueError("canonical move differs approved intermediate inventory")
    if result != current:
        raise ValueError("canonical move adds, drops, or changes cases")
    return result

def historical_feedback_caps(current: dict[str, int], *, with_publication_base: bool = False, with_before_998_base: bool = False) -> dict[str, int] | tuple[dict[str, int], dict[str, int] | None] | tuple[dict[str, int], dict[str, int] | None, dict[str, int] | None]:
    """Restore historical keys and explicitly requested verified checkpoints."""
    digest = caps_digest(current)
    ends = [stage[3] for stage in STAGES]
    if digest not in ends:
        raise ValueError("historical fixture differs approved current inventory")
    result, publication_base, before_998_base = dict(current), None, dict(current) if with_before_998_base and digest == BEFORE_998_CAPS_SHA256 else None
    for stage in reversed(load_receipt()[:ends.index(digest) + 1]):
        for move in stage["moves"]:
            old, new, cap = move["old_id"], move["new_id"], move["subtest_cap"]
            if new not in result or old in result or result[new] != cap:
                raise ValueError("historical move target or cap differs approved receipt")
            result[old] = result.pop(new)
        if caps_digest(result) != stage["original_caps_sha256"]:
            raise ValueError("historical fixture differs original full inventory")
        if with_publication_base and stage["original_caps_sha256"] == S07_CAPS_SHA256:
            publication_base = dict(result)
        if with_before_998_base and stage["original_caps_sha256"] == BEFORE_998_CAPS_SHA256:
            before_998_base = dict(sorted(result.items()))
    if with_before_998_base:
        return result, publication_base, before_998_base
    return (result, publication_base) if with_publication_base else result
