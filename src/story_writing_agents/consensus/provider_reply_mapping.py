"""Map strict Consensus provider replies into existing phase-result values."""

import hashlib
import json

from ..agents import EXECUTION_SPECIALIST, LEAD_ORCHESTRATOR
from ..models import AgentReply, AgentReplyKind, AgentReplyNextPhase, Verdict
from ..reply_trace import ReplyTraceOrigin
from ..specialist_routing import AuthoringIntent
from .contracts import (
    CONSENSUS_AUDITORS,
    ConsensusAuditVerdict,
    ConsensusMaterialHandle,
    ConsensusPhase,
)
from .execution import (
    ConsensusPhaseInvocation,
    ConsensusPhaseOutputKind,
    ConsensusProviderResult,
)


def map_consensus_provider_reply(
    invocation: ConsensusPhaseInvocation,
    reply: AgentReply,
    *,
    authored_material: ConsensusMaterialHandle | None = None,
    report_material: ConsensusMaterialHandle | None = None,
) -> ConsensusProviderResult:
    """Map one strict backend reply without deriving authority from prose."""

    if type(invocation) is not ConsensusPhaseInvocation:
        raise TypeError("invocation must be ConsensusPhaseInvocation")
    if type(reply) is not AgentReply or reply.revision_id != invocation.revision_id:
        raise ValueError("agent reply does not match consensus invocation")
    if invocation.phase in {ConsensusPhase.INPUT_ADOPTION, ConsensusPhase.REVIEW_COMPLETION}:
        raise ValueError("provider-free consensus phase cannot invoke or map a provider")
    if report_material is not None and (
        invocation.intent is not AuthoringIntent.REVIEW_ONLY
        or invocation.phase is not ConsensusPhase.AUDIT
        or reply.kind is not AgentReplyKind.AUDIT
        or (reply.trace is not None and reply.trace.origin is ReplyTraceOrigin.RUNTIME_FALLBACK)
        or type(report_material) is not ConsensusMaterialHandle
        or not report_material.locator.startswith("candidate:")
        or report_material.digest != hashlib.sha256(
            reply.content.encode("utf-8", errors="strict")
        ).hexdigest()
    ):
        raise ValueError("consensus report material must match an actual review-only audit")
    expected_agents = {
        ConsensusPhase.DRAFT: (EXECUTION_SPECIALIST,),
        ConsensusPhase.AUDIT: CONSENSUS_AUDITORS,
        ConsensusPhase.REPAIR: (EXECUTION_SPECIALIST,),
        ConsensusPhase.REAUDIT: CONSENSUS_AUDITORS,
        ConsensusPhase.SYNTHESIS: (LEAD_ORCHESTRATOR,),
        ConsensusPhase.FINALIZATION: (LEAD_ORCHESTRATOR,),
    }[invocation.phase]
    if (
        invocation.agent_id not in expected_agents
        or invocation.ordinal != expected_agents.index(invocation.agent_id) + 1
    ):
        raise ValueError("consensus invocation assignment is invalid")
    digest = hashlib.sha256(
        json.dumps(
            {
                "content": reply.content + "\n",
                "deadlocked": reply.deadlocked,
                "kind": reply.kind.value,
                "next_phase": (
                    None if reply.next_phase is None else reply.next_phase.value
                ),
                "reviewed_proposal_digest": reply.reviewed_proposal_digest,
                "reviewed_proposal_id": reply.reviewed_proposal_id,
                "revision_id": reply.revision_id,
                "verdict": None if reply.verdict is None else reply.verdict.value,
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8", errors="strict")
    ).hexdigest()
    review_findings = (
        reply.content
        if invocation.intent is AuthoringIntent.REVIEW_ONLY
        and invocation.phase is ConsensusPhase.SYNTHESIS
        and reply.kind in {AgentReplyKind.SYNTHESIS, AgentReplyKind.DEADLOCK}
        and (reply.trace is None or reply.trace.origin is not ReplyTraceOrigin.RUNTIME_FALLBACK)
        else None
    )
    if reply.kind is AgentReplyKind.DEADLOCK and reply.deadlocked:
        if authored_material is not None:
            raise ValueError("consensus deadlock cannot retain authored material")
        if invocation.phase is ConsensusPhase.DRAFT:
            return ConsensusProviderResult(
                ConsensusPhaseOutputKind.FAILURE,
                digest,
                None,
                trace=reply.trace,
                validation_details=reply.validation_details,
            )
        candidate = invocation.candidate_material
        if candidate is None:
            raise ValueError("consensus deadlock requires candidate material")
        return ConsensusProviderResult(
            ConsensusPhaseOutputKind.DEADLOCK,
            digest,
            candidate,
            review_findings=review_findings,
            trace=reply.trace,
            validation_details=reply.validation_details,
        )
    if invocation.phase in {ConsensusPhase.DRAFT, ConsensusPhase.REPAIR}:
        if (
            reply.kind is not AgentReplyKind.CANDIDATE
            or authored_material is None
            or authored_material.digest
            != hashlib.sha256(reply.content.encode("utf-8", errors="strict")).hexdigest()
            or not authored_material.locator.startswith("candidate:")
        ):
            raise ValueError("consensus candidate reply is invalid")
        return ConsensusProviderResult(
            ConsensusPhaseOutputKind.CANDIDATE,
            digest,
            authored_material,
            trace=reply.trace,
            validation_details=reply.validation_details,
        )
    candidate = invocation.candidate_material
    if candidate is None:
        raise ValueError("consensus phase requires candidate material")
    if invocation.phase in {ConsensusPhase.AUDIT, ConsensusPhase.REAUDIT}:
        if reply.kind is not AgentReplyKind.AUDIT or type(reply.verdict) is not Verdict:
            raise ValueError("consensus audit reply is invalid")
        conflict = reply.verdict is Verdict.CONFLICT
        if conflict:
            if (
                authored_material is None
                or authored_material.digest
                != hashlib.sha256(
                    reply.content.encode("utf-8", errors="strict")
                ).hexdigest()
                or not authored_material.locator.startswith("candidate:")
            ):
                raise ValueError("consensus conflict critique material is invalid")
        elif authored_material is not None:
            raise ValueError("consensus approval cannot retain critique material")
        return ConsensusProviderResult(
            (
                ConsensusPhaseOutputKind.AUDIT
                if reply.verdict is Verdict.APPROVE
                else ConsensusPhaseOutputKind.CONFLICT
            ),
            digest,
            candidate,
            ConsensusAuditVerdict(reply.verdict.value),
            conflict_material=authored_material,
            report_material=report_material,
            trace=reply.trace,
            validation_details=reply.validation_details,
        )
    if authored_material is not None:
        raise ValueError("consensus phase does not accept authored material")
    if invocation.phase is ConsensusPhase.SYNTHESIS:
        if (
            reply.kind is not AgentReplyKind.SYNTHESIS
            or type(reply.next_phase) is not AgentReplyNextPhase
        ):
            raise ValueError("consensus synthesis reply is invalid")
        return ConsensusProviderResult(
            ConsensusPhaseOutputKind.SYNTHESIS,
            digest,
            candidate,
            review_findings=review_findings,
            next_phase=ConsensusPhase(reply.next_phase.value),
            trace=reply.trace,
            validation_details=reply.validation_details,
        )
    if (
        invocation.phase is not ConsensusPhase.FINALIZATION
        or reply.kind is not AgentReplyKind.FINALIZATION
    ):
        raise ValueError("consensus finalization reply is invalid")
    return ConsensusProviderResult(
        ConsensusPhaseOutputKind.FINALIZATION,
        digest,
        candidate,
        trace=reply.trace,
        validation_details=reply.validation_details,
    )


__all__ = (
    "map_consensus_provider_reply",
)
