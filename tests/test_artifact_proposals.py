"""Exact construction contracts for lead-reviewed artifact candidates."""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import story_writing_agents
from story_writing_agents import artifact_proposals
from story_writing_agents.agents import (
    CHARACTER_CUSTODIAN,
    EXECUTION_SPECIALIST,
    PLOT_ARCHITECT,
)
from story_writing_agents.artifact_proposals import (ARTIFACT_PROPOSAL_SCHEMA_VERSION,
    ArtifactCreateIntent,
    ArtifactDeleteIntent,
    ArtifactIntentEvidence,
    ArtifactPatchIntent,
    ArtifactProposalError,
    ArtifactProposalErrorCode,
    ArtifactProposalIntent,
    ArtifactRenameIntent,
    ArtifactReplaceIntent,
    ArtifactReviewCandidate,
    artifact_review_specialists,
    build_artifact_review_candidate,
    deserialize_artifact_review_candidate,
    serialize_artifact_review_candidate,
)
from story_writing_agents.writing_artifacts import (ArtifactChangeOperationKind,
    ArtifactReviewDomain,
    project_artifact_change_set,
)


_ROOT = Path(__file__).resolve().parents[1]
_OUTLINE = "core_documents/global_outline.md"
_CHARACTERS = "core_documents/character_sheets.md"
_RULES = "core_documents/narrative_rules.md"
_OPENING = "manuscript/part-01/opening.md"
_MOVE = "manuscript/part-01/move.md"
_RETIRED = "manuscript/part-01/retired.md"
_MODULE_EXPORTS = ("ARTIFACT_PROPOSAL_SCHEMA_VERSION",
    "ArtifactChangeIntent",
    "ArtifactCreateIntent",
    "ArtifactDeleteIntent",
    "ArtifactIntentEvidence",
    "ArtifactPatchIntent",
    "ArtifactProposalError",
    "ArtifactProposalErrorCode",
    "ArtifactProposalIntent",
    "ArtifactRenameIntent",
    "ArtifactReplaceIntent",
    "ArtifactReviewCandidate",
    "artifact_review_specialists",
    "build_artifact_review_candidate",
    "deserialize_artifact_review_candidate",
    "serialize_artifact_review_candidate",
)


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _line_bytes(content: bytes, start_line: int, end_line: int) -> bytes:
    return b"".join(content.splitlines(keepends=True)[start_line - 1 : end_line])


def _evidence(evidence_id: str,
    path: str,
    content: bytes,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
) -> ArtifactIntentEvidence:
    selected = (content
        if start_line is None
        else _line_bytes(content, start_line, end_line or start_line)
    )
    return ArtifactIntentEvidence(evidence_id=evidence_id,
        path=path,
        source_digest=_digest(content),
        boundary_digest=_digest(selected),
        start_line=start_line,
        end_line=end_line,
    )


def _combined_candidate() -> tuple[dict[str, bytes], ArtifactReviewCandidate, dict[str, bytes]
]:
    outline = "Act one.\n달빛 충돌.\nAct three.\n".encode()
    opening = b"Old opening.\n"
    moving = b"Move this scene.\n"
    retired = b"Retire this scene.\n"
    base = {_OUTLINE: outline,
        _OPENING: opening,
        _MOVE: moving,
        _RETIRED: retired,
    }
    evidence = (_evidence("outline_line", _OUTLINE, outline, start_line=2, end_line=2),
        _evidence("opening_whole", _OPENING, opening),
        _evidence("move_whole", _MOVE, moving),
        _evidence("retired_whole", _RETIRED, retired),
    )
    intent = ArtifactProposalIntent(operations=(
            ArtifactRenameIntent("move_whole", "manuscript/part-01/moved.md"),
            ArtifactPatchIntent("outline_line", "달빛 합의.\n"),
            ArtifactDeleteIntent("retired_whole"),
            ArtifactCreateIntent(_CHARACTERS, "Mara wants safety.\n"),
            ArtifactReplaceIntent("opening_whole", "New opening.\n"),
        )
    )
    candidate = build_artifact_review_candidate(candidate_id="candidate_all_operations",
        candidate_revision=1,
        intent=intent,
        evidence=evidence,
        grounding_evidence_ids=tuple(item.evidence_id for item in evidence),
        base_state=base,
        attached_core_paths=(_OUTLINE,),
    )
    projected = project_artifact_change_set(base, candidate.change_set)
    return base, candidate, projected


class ArtifactIntentContractTests(unittest.TestCase):
    def test_five_intents_are_strict_frozen_content_hiding_values(self) -> None:
        values_and_fields = ((
                ArtifactCreateIntent(_OPENING, "secret create text"),
                ("suggested_path", "new_text"),
            ),
            (ArtifactPatchIntent("evidence_a", "secret patch text"),
                ("evidence_id", "replacement_text"),
            ),
            (ArtifactReplaceIntent("evidence_a", "secret replace text"),
                ("evidence_id", "new_text"),
            ),
            (ArtifactRenameIntent("evidence_a", _OPENING),
                ("evidence_id", "suggested_destination"),
            ),
            (ArtifactDeleteIntent("evidence_a"), ("evidence_id",)),
        )
        self.assertEqual(tuple(item.operation_kind for item, _fields in values_and_fields),
            tuple(ArtifactChangeOperationKind),
        )
        for value, expected_fields in values_and_fields:
            with self.subTest(intent=type(value).__name__):
                self.assertEqual(tuple(item.name for item in dataclasses.fields(value)),
                    expected_fields,
                )
                self.assertFalse(hasattr(value, "__dict__"))
                self.assertNotIn("secret", repr(value))
                with self.assertRaises(FrozenInstanceError):
                    setattr(value, expected_fields[0], "changed")
        with self.assertRaises(TypeError):
            ArtifactCreateIntent(  # type: ignore[call-arg]
                _OPENING,
                "text",
                apply_authority_granted=True,
            )
        with self.assertRaises(ArtifactProposalError):
            ArtifactRenameIntent("evidence_a", "../escape.md")
        with self.assertRaises(ArtifactProposalError):
            ArtifactProposalIntent(())
        with self.assertRaises(ArtifactProposalError):
            ArtifactProposalIntent(
                (ArtifactCreateIntent(_OPENING, "text"),),
                (ArtifactReviewDomain.TONE_EXECUTION,
                    ArtifactReviewDomain.PLOT_STRUCTURE,
                ),
            )

    def test_evidence_requires_exact_current_whole_or_line_identity(self) -> None:
        content = "alpha\n한글🌙\nomega\n".encode()
        ranged = _evidence("line_two",
            _OUTLINE,
            content,
            start_line=2,
            end_line=2,
        )
        self.assertFalse(ranged.is_whole)
        self.assertEqual(ranged.boundary_digest, _digest("한글🌙\n".encode()))
        whole = _evidence("whole", _OUTLINE, content)
        self.assertTrue(whole.is_whole)
        with self.assertRaises(ArtifactProposalError):
            replace(ranged, end_line=None)
        with self.assertRaises(ArtifactProposalError):
            replace(ranged, source_digest="0" * 64)


class ArtifactCandidateConstructionTests(unittest.TestCase):
    def test_all_operations_resolve_exact_bytes_digests_and_reviewers(self) -> None:
        base, candidate, projected = _combined_candidate()
        self.assertNotEqual(candidate.change_set.base_state_digest,
            candidate.change_set.projected_state_digest,
        )
        self.assertEqual(tuple(item.operation_kind for item in candidate.change_set.operations),
            (ArtifactChangeOperationKind.CREATE,
                ArtifactChangeOperationKind.PATCH,
                ArtifactChangeOperationKind.RENAME,
                ArtifactChangeOperationKind.REPLACE,
                ArtifactChangeOperationKind.DELETE,
            ),
        )
        patch = candidate.change_set.operations[1]
        self.assertEqual(patch.hunks[0].start_utf8_byte, len("Act one.\n".encode()))
        self.assertEqual(patch.hunks[0].end_utf8_byte,
            len("Act one.\n달빛 충돌.\n".encode()),
        )
        self.assertEqual(projected[_OUTLINE], "Act one.\n달빛 합의.\nAct three.\n".encode()
        )
        self.assertEqual(projected[_OPENING], b"New opening.\n")
        self.assertEqual(projected["manuscript/part-01/moved.md"], base[_MOVE])
        self.assertNotIn(_MOVE, projected)
        self.assertNotIn(_RETIRED, projected)
        self.assertEqual(projected[_CHARACTERS], b"Mara wants safety.\n")
        self.assertEqual(
            candidate.mandatory_review_domains,
            (
                ArtifactReviewDomain.PLOT_STRUCTURE,
                ArtifactReviewDomain.CHARACTER_WORLD,
                ArtifactReviewDomain.TONE_EXECUTION,
            ),
        )
        self.assertEqual(
            artifact_review_specialists(candidate.disclosed_review_domains),
            (PLOT_ARCHITECT, CHARACTER_CUSTODIAN, EXECUTION_SPECIALIST),
        )

    def test_optional_domain_is_disclosed_but_never_silently_added(self) -> None:
        candidate = build_artifact_review_candidate(candidate_id="candidate_optional_review",
            candidate_revision=1,
            intent=ArtifactProposalIntent(
                (ArtifactCreateIntent(_OUTLINE, "Outline.\n"),),
                (ArtifactReviewDomain.TONE_EXECUTION,),
            ),
            evidence=(),
            grounding_evidence_ids=("status_outline",),
            base_state={},
            attached_core_paths=(),
        )
        self.assertEqual(
            candidate.mandatory_review_domains,
            (ArtifactReviewDomain.PLOT_STRUCTURE,),
        )
        self.assertEqual(candidate.disclosed_review_domains,
            (
                ArtifactReviewDomain.PLOT_STRUCTURE,
                ArtifactReviewDomain.TONE_EXECUTION,
            ),
        )
        self.assertEqual(
            artifact_review_specialists(candidate.disclosed_review_domains),
            (PLOT_ARCHITECT, EXECUTION_SPECIALIST),
        )

    def test_fixed_core_identity_and_stale_evidence_fail_content_free(self) -> None:
        content = "Sensitive plot phrase.\n".encode()
        evidence = _evidence("outline_whole", _OUTLINE, content)
        for operation in (ArtifactRenameIntent("outline_whole", _RULES),
            ArtifactDeleteIntent("outline_whole"),
        ):
            with self.subTest(operation=operation.operation_kind):
                with self.assertRaises(ArtifactProposalError) as raised:
                    build_artifact_review_candidate(candidate_id="candidate_fixed_core",
                        candidate_revision=1,
                        intent=ArtifactProposalIntent((operation,)),
                        evidence=(evidence,),
                        grounding_evidence_ids=("outline_whole",),
                        base_state={_OUTLINE: content},
                        attached_core_paths=(_OUTLINE,),
                    )
                self.assertIs(raised.exception.code,
                    ArtifactProposalErrorCode.INVALID_INTENT,
                )
                self.assertNotIn("Sensitive", str(raised.exception))
                self.assertNotIn("Sensitive", repr(raised.exception))
                self.assertIsNone(raised.exception.__cause__)
                self.assertIsNone(raised.exception.__context__)

        stale = replace(evidence, source_digest=_digest(b"older bytes"))
        with self.assertRaises(ArtifactProposalError) as raised:
            build_artifact_review_candidate(candidate_id="candidate_stale",
                candidate_revision=1,
                intent=ArtifactProposalIntent(
                    (ArtifactReplaceIntent("outline_whole", "New outline.\n"),)
                ),
                evidence=(stale,),
                grounding_evidence_ids=("outline_whole",),
                base_state={_OUTLINE: content},
                attached_core_paths=(_OUTLINE,),
            )
        self.assertIs(raised.exception.code, ArtifactProposalErrorCode.INVALID_EVIDENCE)
        self.assertEqual(raised.exception.operation_index, 0)
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_dangling_rename_rejects_while_atomic_repair_and_cycles_are_valid(self,
    ) -> None:
        index = b"[next](second.md)\n"
        second = b"Second scene.\n"
        base = {"manuscript/part-01/index.md": index,
            "manuscript/part-01/second.md": second,
        }
        evidence = (_evidence("index_whole", "manuscript/part-01/index.md", index),
            _evidence("second_whole", "manuscript/part-01/second.md", second),
        )
        rename = ArtifactRenameIntent("second_whole", "manuscript/part-01/third.md")
        with self.assertRaises(ArtifactProposalError) as raised:
            build_artifact_review_candidate(candidate_id="candidate_dangling",
                candidate_revision=1,
                intent=ArtifactProposalIntent((rename,)),
                evidence=evidence,
                grounding_evidence_ids=("second_whole",),
                base_state=base,
                attached_core_paths=(),
            )
        self.assertIs(raised.exception.code, ArtifactProposalErrorCode.INVALID_INTENT)

        repaired = build_artifact_review_candidate(candidate_id="candidate_repaired",
            candidate_revision=1,
            intent=ArtifactProposalIntent(
                (ArtifactReplaceIntent("index_whole", "[next](third.md)\n"),
                    rename,
                )
            ),
            evidence=evidence,
            grounding_evidence_ids=("index_whole", "second_whole"),
            base_state=base,
            attached_core_paths=(),
        )
        self.assertTrue(repaired.relationship_validation.is_valid)
        self.assertEqual(repaired.relationship_validation.projected_dangling_edges, ())

        cycle = build_artifact_review_candidate(candidate_id="candidate_cycle",
            candidate_revision=1,
            intent=ArtifactProposalIntent(
                (ArtifactCreateIntent("manuscript/part-02/a.md", "[b](b.md)\n"),
                    ArtifactCreateIntent("manuscript/part-02/b.md", "[a](a.md)\n"),
                )
            ),
            evidence=(),
            grounding_evidence_ids=("status_outline",),
            base_state={},
            attached_core_paths=(),
        )
        self.assertTrue(cycle.relationship_validation.is_valid)
        self.assertEqual(len(cycle.relationship_validation.added_edges), 2)


class ArtifactCandidateCodecAndBoundaryTests(unittest.TestCase):
    def test_candidate_codec_is_exact_canonical_and_rejects_every_drift_family(self,
    ) -> None:
        _base, candidate, _projected = _combined_candidate()
        encoded = serialize_artifact_review_candidate(candidate)
        self.assertEqual(deserialize_artifact_review_candidate(encoded), candidate)
        self.assertIn("달빛 합의", encoded.decode())
        canonical = json.loads(encoded)
        malformed: list[bytes] = [b"{", b"\xff", b" " + encoded, b"{}"]
        unknown = dict(canonical)
        unknown["private_reasoning"] = "must reject"
        malformed.append(
            json.dumps(unknown,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        )
        reordered = {key: canonical[key] for key in reversed(tuple(canonical))}
        malformed.append(
            json.dumps(reordered,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        )
        legacy = dict(canonical)
        legacy["schema_version"] = 0
        malformed.append(
            json.dumps(legacy,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        )
        digest_drift = dict(canonical)
        digest_drift["candidate_digest"] = "sha256:" + "0" * 64
        malformed.append(
            json.dumps(digest_drift,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        )
        for ordinal, payload in enumerate(malformed, start=1):
            with self.subTest(ordinal=ordinal):
                with self.assertRaises(ArtifactProposalError) as raised:
                    deserialize_artifact_review_candidate(payload)
                self.assertIsNone(raised.exception.__cause__)
                self.assertIsNone(raised.exception.__context__)

    def test_public_surface_is_direct_only_exact_and_clean_importable(self) -> None:
        self.assertEqual(ARTIFACT_PROPOSAL_SCHEMA_VERSION, 1)
        self.assertEqual(tuple(artifact_proposals.__all__), _MODULE_EXPORTS)
        self.assertEqual(len(artifact_proposals.__all__), 16)
        for name in _MODULE_EXPORTS:
            value = getattr(artifact_proposals, name)
            if inspect.isclass(value) or inspect.isfunction(value):
                self.assertEqual(value.__module__, artifact_proposals.__name__)
            self.assertFalse(hasattr(story_writing_agents, name))
        self.assertFalse(hasattr(artifact_proposals, "__getattr__"))
        self.assertEqual(tuple(inspect.signature(build_artifact_review_candidate).parameters),
            ("candidate_id",
                "candidate_revision",
                "intent",
                "evidence",
                "grounding_evidence_ids",
                "base_state",
                "attached_core_paths",
            ),
        )

        source = _ROOT / "src"
        with tempfile.TemporaryDirectory() as temporary:
            code = f"""
import importlib, pathlib, sys
sys.path.insert(0, {str(source)!r})
before = tuple(pathlib.Path.cwd().iterdir())
module = importlib.import_module('story_writing_agents.artifact_proposals')
assert len(module.__all__) == 16
assert '__getattr__' not in vars(module)
assert before == tuple(pathlib.Path.cwd().iterdir())
"""
            completed = subprocess.run([sys.executable, "-B", "-I", "-c", code],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
