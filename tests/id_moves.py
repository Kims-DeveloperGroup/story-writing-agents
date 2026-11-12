"""Closed, source-qualified receipts for approved canonical owner moves."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

RECEIPT_PATH = Path(__file__).parent / "fixtures" / "test_id_moves_v1.json"
RECEIPT_SHA256 = "2267591a8f0f47713ea3f51cb8ee40581621a09d8eae4a5ad989446f4e6ea5f1"
ORIGINAL_CAPS_SHA256 = "fc586b3eaa147b4ad4bc06e42e98378c50bd56a9fb09954219c20c45e882b99b"
S02_CAPS_SHA256 = "b554b945803dd496796fd0bf7cad3201c7f729c37821a90231fc734074576c52"
S03_CAPS_SHA256 = "7f3bdd0982191c0adef51815ae3445fa4a55c11dfcb5d7574179e0c542b0310c"
S05_CAPS_SHA256 = "8371eef31044fa33e0ee7e34af17bb3fb03e47556ac1b0f601124d13650b5298"
EXPECTED_CAPS_SHA256 = "0caff3ac47db80cd2344dc4ff480358ec9cc09d2ed68d151ffa2e754c100d156"
ORIGINAL_ID = "tests.test_agent_context_planning.PlanningSurfaceTests.test_exact_dataclass_fields_and_immutability"
SECOND_ID = "tests.test_agent_context_sources.SourceSurfacePolicyAndValueTests.test_descriptor_snapshot_records_are_exact_immutable_and_content_hidden"
THIRD_ID = "tests.test_specialist_routing.SpecialistRoutingTests.test_scope_and_review_basis_codecs_are_closed_and_round_trip"
FOURTH_ID = "tests.test_lead_artifact_application.LeadArtifactApplicationTests.test_atomic_multi_file_apply_has_exact_provenance_and_no_git_effect"
STAGES = (
    (964, "d198abaf60edb1956acbd1f410ad3b1b9fbbedd8", ORIGINAL_CAPS_SHA256, S02_CAPS_SHA256, 45),
    (965, "a18dfc6b3faa8dc326668c77d0da12441be388f8", S02_CAPS_SHA256, S03_CAPS_SHA256, 117),
    (967, "ef8093f49dbab2b2fea619acef40a1c7572bc439", S03_CAPS_SHA256, S05_CAPS_SHA256, 10),
    (1006, "a231649d001ff30d29f927c6884339ce09e2edc3", S05_CAPS_SHA256, EXPECTED_CAPS_SHA256, 8),
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

def remap_base_caps(current: dict[str, int], base: dict[str, int]) -> dict[str, int]:
    """Rekey only exact approved full inventories, never current case IDs."""
    if ORIGINAL_ID in current or (ORIGINAL_ID not in base and SECOND_ID not in base and THIRD_ID not in base and FOURTH_ID not in base) or current == base:
        return base
    base_digest = caps_digest(base)
    before = [stage[2] for stage in STAGES]
    if base_digest not in before:
        return base
    start = before.index(base_digest)
    ends = [stage[3] for stage in STAGES]
    current_digest = caps_digest(current)
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

def historical_feedback_caps(current: dict[str, int]) -> dict[str, int]:
    """Restore only the existing Issue 938 fixture's original owner keys."""
    digest = caps_digest(current)
    ends = [stage[3] for stage in STAGES]
    if digest not in ends:
        raise ValueError("historical fixture differs approved current inventory")
    result = dict(current)
    for stage in reversed(load_receipt()[:ends.index(digest) + 1]):
        for move in stage["moves"]:
            old, new, cap = move["old_id"], move["new_id"], move["subtest_cap"]
            if new not in result or old in result or result[new] != cap:
                raise ValueError("historical move target or cap differs approved receipt")
            result[old] = result.pop(new)
        if caps_digest(result) != stage["original_caps_sha256"]:
            raise ValueError("historical fixture differs original full inventory")
    return result
