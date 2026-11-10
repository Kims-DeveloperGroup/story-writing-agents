"""Closed, source-qualified receipts for approved canonical owner moves."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


RECEIPT_PATH = Path(__file__).parent / "fixtures" / "test_id_moves_v1.json"
ORIGINAL_CAPS_SHA256 = "fc586b3eaa147b4ad4bc06e42e98378c50bd56a9fb09954219c20c45e882b99b"
EXPECTED_CAPS_SHA256 = "b554b945803dd496796fd0bf7cad3201c7f729c37821a90231fc734074576c52"
RECEIPT_SHA256 = "1712755b79983c58a7210d60e8c8788fcd12faa3d0503c67f1ff764d7a6e182a"
ORIGIN_COMMIT = "d198abaf60edb1956acbd1f410ad3b1b9fbbedd8"
ORIGINAL_ID = "tests.test_agent_context_planning.PlanningSurfaceTests.test_exact_dataclass_fields_and_immutability"


def caps_digest(caps: dict[str, int]) -> str:
    return hashlib.sha256(json.dumps(caps, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_receipt() -> dict:
    """Read the one approved immutable receipt without caching observations."""
    try:
        raw = RECEIPT_PATH.read_bytes()
        receipt = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("cannot read canonical move receipt") from error
    if (hashlib.sha256(raw).hexdigest() != RECEIPT_SHA256
            or receipt.get("schema_version") != 1 or receipt.get("issue") != 964
            or receipt.get("origin_commit") != ORIGIN_COMMIT
            or receipt.get("original_caps_sha256") != ORIGINAL_CAPS_SHA256
            or receipt.get("expected_caps_sha256") != EXPECTED_CAPS_SHA256):
        raise ValueError("canonical move receipt is not approved")
    moves = receipt["moves"]
    old_ids = {move["old_id"] for move in moves}
    new_ids = {move["new_id"] for move in moves}
    if len(moves) != 45 or len(old_ids) != 45 or len(new_ids) != 45:
        raise ValueError("canonical move receipt is not bijective")
    return receipt


def remap_base_caps(current: dict[str, int], base: dict[str, int]) -> dict[str, int]:
    """Rekey only the fixed Issue 964 bijection; never alias current IDs."""
    if ORIGINAL_ID not in base or ORIGINAL_ID in current or current == base:
        return base
    if caps_digest(base) != ORIGINAL_CAPS_SHA256:
        return base
    if caps_digest(current) != EXPECTED_CAPS_SHA256:
        raise ValueError("canonical move differs approved complete inventory")
    moves = load_receipt()["moves"]
    old_ids = {move["old_id"] for move in moves}
    new_ids = {move["new_id"] for move in moves}
    if not old_ids <= base.keys() or new_ids & (base.keys() - old_ids):
        raise ValueError("canonical move receipt overlaps inventory")
    relocated = {identifier: cap for identifier, cap in base.items()
                 if identifier not in old_ids}
    for move in moves:
        if move["subtest_cap"] != base[move["old_id"]]:
            raise ValueError("canonical move changed subtest cap")
        relocated[move["new_id"]] = base[move["old_id"]]
    if relocated != current:
        raise ValueError("canonical move omitted or added IDs")
    return relocated


def historical_feedback_caps(current: dict[str, int]) -> dict[str, int]:
    """Restore only the existing Issue 938 fixture's historical owner keys."""
    if caps_digest(current) != EXPECTED_CAPS_SHA256:
        raise ValueError("historical fixture differs approved current inventory")
    original = current.copy()
    for move in load_receipt()["moves"]:
        cap = original.pop(move["new_id"])
        if cap != move["subtest_cap"] or move["old_id"] in original:
            raise ValueError("historical fixture changed approved move")
        original[move["old_id"]] = cap
    if caps_digest(original) != ORIGINAL_CAPS_SHA256:
        raise ValueError("historical fixture differs original inventory")
    return original
