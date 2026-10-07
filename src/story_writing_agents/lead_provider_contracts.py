"""Pure projections of the facts offered to one Lead provider invocation.

These snapshots describe choices, not authority. Orchestration still owns source
freshness, completed READ receipts, confirmation, candidate construction and state.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .agents import SPECIALIST_IDS
from .conversations import COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES
from .conversations.events import authoring_scope_from_dict
from .specialist_routing import (
    ArtifactReviewDomain, AuthoringActivity, AuthoringIntent, AuthoringScope,
    required_authoring_specialists, required_authoring_domains, assignment_mode_for_specialist,
    AuthoringContributionRole,
)
from .lead_premise_decisions import (
    PremiseAnalysis, PremiseDecisionSummary, premise_question_intent_from_dict,
    premise_analysis_from_dict, premise_summary_from_dict,
)
from .structured_output_validation import DomainValidationRule, validation_failure


PREPARATION_INPUT_IDS = ("recommendation_request", "recommendation_decision")


class LeadContractError(ValueError):
    """A rejected Lead choice described only by closed, content-free rules."""

    def __init__(self, rules: tuple[DomainValidationRule, ...]) -> None:
        self.rules = rules
        super().__init__("Lead result violates its offered constraints")


def closed_object(properties: dict[str, object]) -> dict[str, object]:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def string_array(
    values: tuple[str, ...] | None = None, *, minimum: int = 0,
    maximum: int | None = None,
) -> dict[str, object]:
    item: dict[str, object] = {"type": "string"}
    if values:
        item["enum"] = list(values)
    result: dict[str, object] = {"type": "array", "items": item, "minItems": minimum}
    if maximum is not None:
        result["maxItems"] = maximum
    return result


def alternatives(variants: list[dict[str, object]]) -> dict[str, object]:
    return variants[0] if len(variants) == 1 else {"anyOf": variants}


def nonempty_text_schema() -> dict[str, object]:
    # The common provider dialect deliberately excludes minLength and pattern.
    return {"type": "string", "description": "Nonempty UTF-8 text."}


def _scope_extensions(scope: AuthoringScope | None) -> dict[str, object]:
    if scope is not None and scope.schema_version < 3:
        return {}
    domains = tuple(item.value for item in required_authoring_domains(scope)) if scope else tuple(item.value for item in ArtifactReviewDomain)
    contributions = [
        {
            **closed_object({
                "domain": {"type": "string", "enum": [item.domain.value]},
                "role": {"type": "string", "enum": [item.role.value]},
            }),
            "description": "The bound contribution role for this declared story domain.",
        }
        for item in scope.contributions
    ] if scope else [{
        **closed_object({
            "domain": {"type": "string", "enum": list(domains)},
            "role": {"type": "string", "enum": [item.value for item in AuthoringContributionRole]},
        }),
        "description": "One explicit contribution role for a declared story domain.",
    }]
    slots = [
        {
            **closed_object({
                "slot_id": {"type": "string", "enum": [item.slot_id]},
                "dependent_domains": string_array(
                    tuple(domain.value for domain in item.dependent_domains),
                    minimum=len(item.dependent_domains), maximum=len(item.dependent_domains),
                ),
            }),
            "description": "The bound shared-premise dependency for these draft-owner domains.",
        }
        for item in scope.dependency_slots
    ] if scope and scope.dependency_slots else [{
        **closed_object({
            "slot_id": nonempty_text_schema(),
            "dependent_domains": string_array(domains, minimum=1),
        }),
        "description": "One explicit shared-premise dependency for declared draft-owner domains.",
    }]
    return {
        "contributions": {
            "type": "array", "items": alternatives(contributions) if contributions else closed_object({}),
            "minItems": len(scope.contributions) if scope else 0,
            "maxItems": len(scope.contributions) if scope else len(domains),
        },
        "dependency_slots": {
            "type": "array", "items": alternatives(slots), "minItems": 0,
            **({"minItems": len(scope.dependency_slots), "maxItems": len(scope.dependency_slots)} if scope else {}),
        },
    }


def _scope_for_rules(raw: dict[str, object]) -> AuthoringScope:
    version = 3 if "contributions" in raw else 2 if "intent" in raw else 1
    return authoring_scope_from_dict({"schema_version": version, "activity": raw["activity"],
        "target_paths": raw["target_paths"], "domains": raw["domains"],
        "interpretation": "Provider constraint projection", "task_id": "constraint", "task_revision": 1,
        **({"intent": raw.get("intent", "authoring")} if version >= 2 else {}),
        **({"contributions": raw["contributions"], "dependency_slots": raw["dependency_slots"]} if version == 3 else {})})


def canonical_scope(value: object) -> object:
    """Normalize domain order only after retaining duplicate rejection."""
    if type(value) is not dict or type(value.get("domains")) is not list:
        return value
    domains = value["domains"]
    if any(type(item) is not str for item in domains):
        return value
    if len(set(domains)) != len(domains):
        raise LeadContractError((DomainValidationRule.DOMAIN_DUPLICATE,))
    order = [item.value for item in ArtifactReviewDomain]
    if any(item not in order for item in domains):
        return value
    return {**value, "domains": sorted(domains, key=order.index)}


def canonical_assignments(value: list[object]) -> list[object]:
    """Preserve malformed entries for the decoder; never deduplicate roles."""
    if any(type(item) is not dict or item.get("agent_id") not in SPECIALIST_IDS
           for item in value):
        return value
    ids = [item["agent_id"] for item in value]
    if len(set(ids)) != len(ids):
        raise validation_failure(
            LeadContractError((DomainValidationRule.SPECIALIST_DUPLICATE,)),
            '$',
            'agent_id values must be unique before canonical ordering',
        )
    return sorted(value, key=lambda item: SPECIALIST_IDS.index(item["agent_id"]))


def visible_text(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        return 0 < len(value.encode("utf-8", errors="strict")) <= COLLABORATION_VISIBLE_TEXT_MAX_UTF8_BYTES
    except UnicodeError:
        return False


@dataclass(frozen=True, slots=True)
class LeadExplorationConstraints:
    """Offered provisional choices; runtime authorizes actual artifact READs."""

    input_ids: tuple[str, ...] = ()

    def prompt_facts(self) -> dict[str, object]:
        return {
            "interaction_mode": "exploration", "provisional": True,
            "offered_basis_input_ids": list(self.input_ids),
            "authority": "No specialists, completion, candidate, objective, Apply or Commit.",
            "artifact_observations": "Cite actual authorized artifact READs only; never SEARCH, lists, user prose or earlier ideas.",
        }

    def schema(self) -> dict[str, object]:
        idea = closed_object({
            "kind": {"type": "string", "enum": ["hypothesis", "alternative", "short_example"]},
            "material_text": nonempty_text_schema(), "basis_input_ids": string_array(),
        })
        observation = closed_object({"text": nonempty_text_schema(), "evidence_input_ids": string_array(minimum=1)})
        common = {"kind": {"type": "string", "enum": ["exploration"]}, "message": nonempty_text_schema()}
        result = alternatives([
            closed_object({**common, "status": {"type": "string", "enum": ["ideas"]},
                "ideas": {"type": "array", "items": idea, "minItems": 0},
                "grounded_observations": {"type": "array", "items": observation, "minItems": 0}}),
            closed_object({**common, "status": {"type": "string", "enum": ["clarification"]}}),
        ])
        result["description"] = "Only explicit user-selected provisional exploration; no authoring or mutation authority."
        for variant in result.get("anyOf", (result,)):
            variant["description"] = ("Present provisional ideas and qualified actual artifact READ observations." if "ideas" in variant["properties"] else "Ask for clarification without creating idea material or authority.")
        return result

    def diagnose(self, answer: str, *, read_ids: tuple[str, ...] = (), read_reviewers: tuple[tuple[str, str], ...] = ()) -> tuple[DomainValidationRule, ...]:
        del read_reviewers
        try:
            value = json.loads(answer)
            if type(value) is not dict or value.get("kind") != "exploration":
                raise ValueError("exploration mode is required")
            status = value.get("status")
            expected = {"kind", "status", "message"} | ({"ideas", "grounded_observations"} if status == "ideas" else set())
            if status not in {"ideas", "clarification"} or set(value) != expected or not visible_text(value["message"]):
                raise ValueError("invalid closed exploration result")
            if status == "ideas":
                ideas, observations = value["ideas"], value["grounded_observations"]
                if type(ideas) is not list or type(observations) is not list or not (ideas or observations):
                    raise ValueError("exploration material is empty")
                for idea in ideas:
                    if type(idea) is not dict or set(idea) != {"kind", "material_text", "basis_input_ids"} or idea["kind"] not in {"hypothesis", "alternative", "short_example"} or not visible_text(idea["material_text"]) or type(idea["basis_input_ids"]) is not list or not set(idea["basis_input_ids"]).issubset((*self.input_ids, *read_ids)):
                        raise ValueError("idea must use offered provisional basis")
                for observation in observations:
                    ids = observation.get("evidence_input_ids") if type(observation) is dict else None
                    if type(observation) is not dict or set(observation) != {"text", "evidence_input_ids"} or not visible_text(observation["text"]) or type(ids) is not list or not ids or len(set(ids)) != len(ids) or not set(ids).issubset(read_ids):
                        raise ValueError("observation requires current artifact READs")
        except (ValueError, TypeError, KeyError):
            return (DomainValidationRule.LEAD_OUTCOME_REJECTED,)
        return ()


@dataclass(frozen=True, slots=True)
class LeadProviderConstraints:
    """One immutable set of offered choices shared by schema, prompt and guard."""

    input_ids: tuple[str, ...] = ()
    reviewer_inputs: tuple[tuple[str, str], ...] = ()
    task_revisions: tuple[tuple[str, int], ...] = ()
    source_ids: tuple[str, ...] | None = None
    objective_ids: tuple[str, ...] | None = None
    allow_read_sources: bool = False
    scope: AuthoringScope | None = None
    preparation_kind: str | None = None
    preparation_objective_id: str | None = None
    preparation_targets: tuple[str, ...] = ()
    preparation_specialists: tuple[str, ...] = ()
    recommendation_only: bool = False
    continuation: bool = False
    premise_question_id: str | None = None

    def __post_init__(self) -> None:
        for values in (self.input_ids, self.task_revisions, self.reviewer_inputs,
                       self.preparation_targets, self.preparation_specialists):
            if type(values) is not tuple:
                raise TypeError("Lead constraint collections must be immutable tuples")
        for values in (self.source_ids, self.objective_ids):
            if values is not None and type(values) is not tuple:
                raise TypeError("Lead constraint offers must be immutable tuples")
        if self.scope is not None and type(self.scope) is not AuthoringScope:
            raise TypeError("Lead constraint scope must be an AuthoringScope")

    def prompt_facts(self) -> dict[str, object]:
        return {
            **({"offered_premise_question_id": self.premise_question_id,
                "premise_analysis_rule": "Only an undecided question needs target-free alternatives analysis. Reuse disclosed resolved inputs. Analysis requires its own Confirm and cannot draft, review a candidate, select a premise, or update an objective."}
               if self.premise_question_id is not None else {}),
            "offered_assignment_input_ids": list(self.input_ids),
            "required_input_ids": [item for item in self.input_ids
                                   if not self.preparation_kind or item not in PREPARATION_INPUT_IDS],
            "mandatory_reviewer_inputs": [
                {"agent_id": agent, "input_id": alias} for agent, alias in self.reviewer_inputs
            ],
            "confirmed_scope": None if self.scope is None else {
                **({"intent": self.scope.intent.value} if self.scope.schema_version == 2 else {}),
                "activity": self.scope.activity.value,
                "target_paths": list(self.scope.target_paths),
                "domains": [item.value for item in self.scope.domains],
                "task": f"{self.scope.task_id}@{self.scope.task_revision}",
            },
            "preparation": None if self.preparation_kind is None else {
                "kind": self.preparation_kind,
                "objective_id": self.preparation_objective_id,
                "target_paths": list(self.preparation_targets),
                "specialist_ids": list(self.preparation_specialists),
                "recommendation_only": self.recommendation_only,
            },
            "objective_steps": {"question": "clarify", "consultation": "consult", "artifact": "prepare"},
            "specialist_order": list(SPECIALIST_IDS),
            "domain_order": [item.value for item in ArtifactReviewDomain],
            "source_membership": (
                "Assignment input_ids select only the offered assignment inputs"
                + (" or evidence IDs from completed authorized READs. " if self.allow_read_sources else ". ")
                + "Historical draft IDs offered for preparation recommendations are not assignment inputs until "
                "selected and materialized by confirmed preparation. "
                "Consultation and continuation assignments must cover every required input; each core-document reviewer must "
                "receive its own source. Do not invent or omit source aliases."
            ),
            "offered_source_ids": None if self.source_ids is None else list(self.source_ids),
            "recommendation_source_membership": (
                "Prior same-task revision drafts may be selected only as input material for a fresh "
                "AUTHORING DRAFT/REVISE consultation preparation. Their event, owner, scope and digest "
                "remain historical; they never satisfy current owner drafts, candidate review or Apply. "
                "Artifact preparation still requires the exact current completed authoring source. "
                "Recommendation source_ids select only the offered preparation recommendation sources"
                + (" or evidence IDs from completed authorized READs. " if self.allow_read_sources else ". ")
                + "These sources are not extra assignment inputs. To prepare a document from an approved historical draft, "
                "select its exact offered draft ID and preserve its authoring task and specialist provenance. "
                "Use artifact preparation without another drafting consultation when that task already has complete "
                "required specialist drafts; otherwise use consultation preparation. Confirmation materializes the "
                "selected sources without authorizing Apply or Commit."
            ),
            "artifact_path_rule": (
                "Use normalized workspace-relative POSIX artifact paths. Existing core-document targets are only "
                "core_documents/global_outline.md, core_documents/part_outlines.md, "
                "core_documents/character_sheets.md and core_documents/narrative_rules.md. "
                "New documents use unused manuscript/PART/PATH.md paths. PART is 1-64 ASCII letters, digits, "
                "underscores or hyphens, starting with a letter or digit. Every remaining path component is "
                "1-128 ASCII letters, digits, underscores, hyphens or dots, starting with a letter or digit; "
                "the final component ends in .md. The whole path is at most 1024 UTF-8 bytes. "
                "Keep the requested title and language in the document content and disclose the proposed destination. "
                "An existing empty document needs replacement, not creation."
            ),
            "offered_objective_ids": None if self.objective_ids is None else list(self.objective_ids),
            "runtime_input_ids": list(PREPARATION_INPUT_IDS) if self.preparation_kind else [],
            "runtime_input_rule": "Runtime appends only its recommendation request/decision aliases after exact confirmation.",
        }

    def scope_schema(self) -> dict[str, object]:
        scope = self.scope
        tokens = tuple(f"{task}@{revision}" for task, revision in self.task_revisions)
        task: dict[str, object] = {"type": "string"}
        if tokens:
            task["enum"] = list(tokens)
        return {**closed_object({
            **_scope_extensions(scope),
            **({"intent": {
                "type": "string",
                "enum": [scope.intent.value] if scope else [item.value for item in AuthoringIntent],
                "description": (
                    "Disclose review_only only for an explicit request to evaluate the supplied "
                    "original without authoring or artifact authority. Activity review alone does "
                    "not imply this intent. Preserve bound intent; requested revisions need fresh "
                    "authoring work."
                ),
            }} if scope is None or scope.schema_version >= 2 else {}),
            "activity": {"type": "string", "enum": ([scope.activity.value] if scope else
                          [item.value for item in AuthoringActivity])},
            "target_paths": string_array(
                scope.target_paths if scope else None,
                minimum=len(scope.target_paths) if scope else 0,
                maximum=len(scope.target_paths) if scope else None,
            ),
            "domains": string_array(
                tuple(item.value for item in scope.domains) if scope else
                tuple(item.value for item in ArtifactReviewDomain),
                minimum=len(scope.domains) if scope else 0,
                maximum=len(scope.domains) if scope else len(ArtifactReviewDomain),
            ),
            "task": task,
        }), "description": (
            "Classify the current work and include every affected specialist domain and "
            "canonical workspace-relative target. Preserve the bound scope and task/revision "
            "when supplied."
        )}

    def assignment_schema(self) -> dict[str, object]:
        required = () if self.scope is None else required_authoring_specialists(self.scope)
        mode = "review" if self.scope and self.scope.activity is AuthoringActivity.REVIEW else "draft"
        agents = self.preparation_specialists or tuple(SPECIALIST_IDS)
        input_rule = (
            "Assignment sources use only offered assignment inputs"
            + (" or evidence IDs from completed authorized READs. " if self.allow_read_sources else ". ")
            + "Preparation recommendation sources are not assignment inputs unless confirmed preparation has "
            "materialized and offered them here. Preserve all required inputs and each reviewer's own source."
        )
        assignment_purposes = {
            "plot_architect": (
                "Assign Plot when plot, causality, pacing, or outline structure is affected; "
                "request its scoped draft or review."
            ),
            "character_custodian": (
                "Assign Character when characterization, motivation, or relationships are "
                "affected; request its scoped draft or review."
            ),
            "execution_specialist": (
                "Assign Execution when scene prose, narrative voice, or narrative rules are "
                "affected; request its scoped draft or review."
            ),
        }
        return alternatives([{**closed_object({
            "agent_id": {"type": "string", "enum": [agent]},
            "instruction": {
                **nonempty_text_schema(),
                "description": (
                    "Nonempty UTF-8 task instructions specifying this specialist's "
                    "concrete deliverable and success criteria "
                    "against the shared writing-task brief. Preserve its protected elements, "
                    "change boundaries, and exact offered task/source identities."
                ),
            },
            "input_ids": {
                **string_array(None if self.allow_read_sources else self.input_ids, minimum=1,
                               maximum=None if self.allow_read_sources else len(self.input_ids) or None),
                "description": input_rule,
            },
            "mode": {"type": "string", "enum": ([assignment_mode_for_specialist(self.scope, agent).value] if self.scope and self.scope.schema_version == 3 and agent in required else
                [mode] if agent in required or (self.scope and self.scope.intent is AuthoringIntent.REVIEW_ONLY) else
                ["draft", "review", "constraint_assessment"] if self.scope is None else ["draft", "review"])},
        }), "description": assignment_purposes[agent]} for agent in agents])

    def objective_schema(self, step: str | None, *, cancel: bool = False) -> dict[str, object]:
        if self.preparation_kind:
            if self.preparation_objective_id is not None:
                return {**closed_object({"action": {"type": "string", "enum": ["continue"]},
                    "objective_id": {"type": "string", "enum": [self.preparation_objective_id]},
                    "next_step": {"type": "string", "enum": [step]}}), "description": (
                        "Advance the matching pending document change request through this "
                        "workflow step; preserve its identity and do not claim it is applied."
                    )}
            return {**closed_object({"action": {"type": "string", "enum": ["start"]},
                "target_paths": string_array(self.preparation_targets, minimum=len(self.preparation_targets),
                                             maximum=len(self.preparation_targets)),
                "next_step": {"type": "string", "enum": [step]}}), "description": (
                    "Start a document change request when no matching request exists; track "
                    "the selected targets without treating preparation as Apply."
                )}
        variants = [{**closed_object({"action": {"type": "string", "enum": ["start"]},
            "target_paths": string_array(minimum=1), "next_step": {"type": "string", "enum": [step]}}), "description": (
                "Start a document change request when no matching request exists; track the "
                "selected targets without treating preparation as Apply."
            )}]
        if self.objective_ids is None or self.objective_ids:
            identity: dict[str, object] = {"type": "string"}
            if self.objective_ids:
                identity["enum"] = list(self.objective_ids)
            variants.append({**closed_object({"action": {"type": "string", "enum": ["continue"]},
                "objective_id": identity, "next_step": {"type": "string", "enum": [step]}}), "description": (
                    "Advance the matching pending document change request through this "
                    "workflow step; preserve its identity and do not claim it is applied."
                )})
            if cancel:
                variants.append({**closed_object({"action": {"type": "string", "enum": ["cancel"]},
                                               "objective_id": identity}), "description": (
                                                   "Cancel the selected document change request only on explicit"
                                                   " user cancellation; close tracking without changing "
                                                   "documents."
                                               )})
        return alternatives(variants)

    def recommendation_schema(self) -> dict[str, object]:
        sources = None if self.allow_read_sources else self.source_ids
        source_schema = string_array(sources, minimum=1, maximum=len(sources) if sources else None)
        source_schema["description"] = (
            "Recommendation source_ids select only offered preparation recommendation sources"
            + (" or evidence IDs from completed authorized READs. " if self.allow_read_sources else ". ")
            + "Select the exact approved historical draft to prepare its document; preserve its task and provenance. "
            "This selection does not make it an ordinary assignment input or authorize a write."
        )
        common = {"instruction": nonempty_text_schema(), "source_ids": source_schema}
        if self.scope and self.scope.intent is AuthoringIntent.REVIEW_ONLY:
            return {**closed_object({
                **common,
                "kind": {"type": "string", "enum": ["prepare_consultation"]},
                "specialist_ids": string_array(
                    tuple(SPECIALIST_IDS), minimum=1, maximum=len(SPECIALIST_IDS),
                ),
            }), "description": (
                "Prepare only another exact supplied-material review consultation; "
                "no replacement or artifact-objective authority."
            )}
        objectives: list[dict[str, object]] = [{}]
        if self.objective_ids is None or self.objective_ids:
            identity: dict[str, object] = {"type": "string"}
            if self.objective_ids:
                identity["enum"] = list(self.objective_ids)
            objectives.append({"objective_id": identity})
        variants = []
        for objective in objectives:
            association_description = (
                "Continue the identified pending document change request."
                if objective else
                "Use this variant when no existing document change request is being continued."
            )
            variants.extend([
                {**closed_object({**common, **objective, "kind": {"type": "string", "enum": ["prepare_artifact"]},
                               "target_paths": string_array(minimum=1)}), "description": (
                                   "Draft a document-change proposal when the selected sources "
                                   "already support exact changes to these targets. "
                               ) + association_description},
                {**closed_object({**common, **objective, "kind": {"type": "string", "enum": ["prepare_consultation"]},
                               "specialist_ids": string_array(tuple(SPECIALIST_IDS), minimum=1, maximum=len(SPECIALIST_IDS))}), "description": (
                                   "Draft a specialist consultation proposal when further "
                                   "drafting or review is needed; include every affected "
                                   "specialist. "
                               ) + association_description},
            ])
        result = alternatives(variants)
        result["description"] = (
            (
                "Request confirmation for one Lead proposal-drafting step, not specialist "
                "execution, Apply, or Commit. "
            )
            + result.get("description", "")
        ).rstrip()
        return result

    def diagnose(self, answer: str, *, read_ids: tuple[str, ...] = (),
                 read_reviewers: tuple[tuple[str, str], ...] = ()) -> tuple[DomainValidationRule, ...]:
        try:
            value = json.loads(answer)
        except (TypeError, ValueError):
            return (DomainValidationRule.MALFORMED_RESULT,)
        return self.rules(value, read_ids=read_ids, read_reviewers=read_reviewers)

    def rules(self, value: object, *, read_ids: tuple[str, ...] = (),
                 read_reviewers: tuple[tuple[str, str], ...] = ()) -> tuple[DomainValidationRule, ...]:
        if type(value) is dict and type(value.get("proposal")) is dict and "premise_decision" in value["proposal"]:
            if self.premise_question_id is None:
                return (DomainValidationRule.LEAD_OUTCOME_REJECTED,)
            return PremiseDecisionConstraints(self.premise_question_id, self.input_ids,
                allow_read_sources=self.allow_read_sources).front_rules(value, read_ids=read_ids)
        if not self.allow_read_sources:
            read_ids = ()
            read_reviewers = ()
        R = DomainValidationRule
        if type(value) is not dict:
            return (R.RESULT_OBJECT_REQUIRED,)
        rules: list[DomainValidationRule] = []
        raw_scope = value.get("authoring_scope")
        message = value.get("message")
        if not visible_text(message) or (type(raw_scope) is dict and type(message) is str
                                        and (not message.strip() or "\x00" in message)):
            rules.append(R.MESSAGE_REQUIRED)
        if type(raw_scope) is dict:
            domains = raw_scope.get("domains")
            if type(domains) is list and all(type(item) is str for item in domains):
                if len(set(domains)) != len(domains):
                    rules.append(R.DOMAIN_DUPLICATE)
            paths = raw_scope.get("target_paths")
            if type(paths) is list and all(type(item) is str for item in paths) and len(set(paths)) != len(paths):
                rules.append(R.TARGET_DUPLICATE)
            if self.scope and (raw_scope.get("activity") != self.scope.activity.value
                or raw_scope.get("intent", "authoring") != self.scope.intent.value
                or paths != list(self.scope.target_paths)
                or type(domains) is not list
                or sorted(domains, key=str) != sorted(item.value for item in self.scope.domains)
                or raw_scope.get("task") not in {f"{task}@{revision}" for task, revision in self.task_revisions}):
                rules.append(R.PREPARATION_SCOPE)
        proposal = value.get("next_intent" if self.continuation else "proposal")
        if type(proposal) is dict:
            if self.continuation and not visible_text(proposal.get("purpose")):
                rules.append(R.PURPOSE_REQUIRED)
            assignments = proposal.get("assignments")
            if type(assignments) is list:
                roles = [item.get("agent_id") for item in assignments if type(item) is dict]
                if all(type(item) is str for item in roles) and len(set(roles)) != len(roles):
                    rules.append(R.SPECIALIST_DUPLICATE)
                selected: set[str] = set()
                by_role: dict[str, set[str]] = {}
                required = set((*self.input_ids, *read_ids))
                if self.preparation_kind:
                    required.difference_update(PREPARATION_INPUT_IDS)
                for item in assignments:
                    if type(item) is not dict:
                        continue
                    instruction = item.get("instruction")
                    if not visible_text(instruction) or (
                        item.get("mode") == "review" and type(instruction) is str
                        and (not instruction.strip() or "\x00" in instruction)
                    ):
                        rules.append(R.INSTRUCTION_REQUIRED)
                    aliases = item.get("input_ids")
                    if type(aliases) is not list or not aliases or any(type(alias) is not str for alias in aliases):
                        rules.append(R.ASSIGNMENT_INPUTS_REQUIRED)
                        continue
                    if len(set(aliases)) != len(aliases):
                        rules.append(R.INPUT_DUPLICATE)
                    if not set(aliases).issubset((*self.input_ids, *read_ids)):
                        rules.append(R.INPUT_NOT_OFFERED)
                    selected.update(aliases)
                    if type(item.get("agent_id")) is str:
                        by_role[item["agent_id"]] = set(aliases)
                if not required.issubset(selected):
                    rules.append(R.CONTINUATION_INPUT_COVERAGE if self.continuation else R.INPUT_COVERAGE)
                if any(alias not in by_role.get(agent, set()) for agent, alias in (*self.reviewer_inputs, *read_reviewers)):
                    rules.append(R.REVIEWER_INPUT)
                scope = self.scope
                if scope is None and type(raw_scope) is dict:
                    domains = raw_scope.get("domains", [])
                    paths = raw_scope.get("target_paths", [])
                    try:
                        scope = _scope_for_rules(raw_scope)
                    except (KeyError, TypeError, ValueError):
                        scope = None
                if scope:
                    required_roles = required_authoring_specialists(scope)
                    if not set(required_roles).issubset(roles):
                        rules.append(R.SPECIALIST_REQUIRED)
                    expected_mode = "review" if scope.activity is AuthoringActivity.REVIEW else "draft"
                    if any(
                        type(item) is dict
                        and (item.get("agent_id") in required_roles
                             or scope.intent is AuthoringIntent.REVIEW_ONLY)
                        and item.get("mode") != (assignment_mode_for_specialist(scope, item["agent_id"]).value if scope.schema_version == 3 else expected_mode)
                        for item in assignments
                    ):
                        rules.append(R.ASSIGNMENT_MODE)
                if self.preparation_specialists and tuple(role for role in SPECIALIST_IDS if role in roles) != self.preparation_specialists:
                    rules.append(R.PREPARATION_SPECIALISTS)
        action = value.get("recommended_action")
        if type(action) is dict:
            roles = action.get("specialist_ids")
            if (
                action.get("kind") == "prepare_consultation"
                and type(roles) is list
                and all(type(role) is str for role in roles)
            ):
                if not roles or len(set(roles)) != len(roles) or any(role not in SPECIALIST_IDS for role in roles):
                    rules.append(R.RECOMMENDATION_SPECIALISTS)
                if type(raw_scope) is dict:
                    try:
                        selected_scope = _scope_for_rules(raw_scope)
                        if not set(required_authoring_specialists(selected_scope)).issubset(roles):
                            rules.append(R.RECOMMENDATION_SPECIALISTS)
                    except (KeyError, TypeError, ValueError):
                        pass
            paths = action.get("target_paths")
            if type(paths) is list and all(type(path) is str for path in paths):
                if not paths or len(set(paths)) != len(paths):
                    rules.append(R.RECOMMENDATION_TARGETS)
            if not visible_text(action.get("instruction")):
                rules.append(R.RECOMMENDATION_INSTRUCTION)
            sources = action.get("source_ids")
            if type(sources) is list and all(type(item) is str for item in sources):
                if not sources or len(set(sources)) != len(sources):
                    rules.append(R.RECOMMENDATION_SOURCES)
                if self.source_ids is not None and not set(sources).issubset((*self.source_ids, *read_ids)):
                    rules.append(R.SOURCE_NOT_OFFERED)
            if self.objective_ids is not None and "objective_id" in action and action["objective_id"] not in self.objective_ids:
                rules.append(R.RECOMMENDATION_OBJECTIVE_NOT_OFFERED)
        artifact = value.get("artifact_intent")
        nested = artifact if type(artifact) is dict else proposal
        objective = nested.get("objective_intent") if type(nested) is dict else value.get("objective_intent")
        if type(objective) is dict:
            targets = objective.get("target_paths")
            if objective.get("action") == "start" and type(targets) is list and all(type(path) is str for path in targets):
                if not targets or len(set(targets)) != len(targets):
                    rules.append(R.ARTIFACT_OBJECTIVE_TARGETS if type(artifact) is dict else
                                 R.PROPOSAL_OBJECTIVE_TARGETS if type(proposal) is dict else R.OBJECTIVE_TARGETS)
            if self.objective_ids is not None and "objective_id" in objective and objective["objective_id"] not in self.objective_ids:
                rules.append(R.OBJECTIVE_NOT_OFFERED)
            expected_step = "prepare" if type(artifact) is dict else "consult" if type(proposal) is dict else "clarify"
            if objective.get("action") == "cancel":
                if type(artifact) is dict or type(proposal) is dict or value.get("kind") == "question":
                    rules.append(R.OBJECTIVE_BRANCH)
            elif (objective.get("next_step") != expected_step
                  or (expected_step == "clarify" and value.get("kind") != "question")):
                rules.append(R.OBJECTIVE_BRANCH)
        if self.preparation_kind and not self.recommendation_only and value.get("kind") != "question":
            expected_field = "artifact_intent" if self.preparation_kind == "prepare_artifact" else "proposal"
            if value.get("kind") != "proposal" or type(value.get(expected_field)) is not dict:
                rules.append(R.PREPARATION_KIND)
            needs_objective = expected_field == "artifact_intent" or self.preparation_objective_id is not None
            if needs_objective:
                expected_action = "start" if self.preparation_objective_id is None else "continue"
                if (type(objective) is not dict or objective.get("action") != expected_action
                    or (self.preparation_objective_id is not None and objective.get("objective_id") != self.preparation_objective_id)
                    or (self.preparation_objective_id is None and objective.get("target_paths") != list(self.preparation_targets))):
                    rules.append(R.PREPARATION_OBJECTIVE)
            elif objective is not None:
                rules.append(R.PREPARATION_OBJECTIVE)
        if self.continuation:
            continuation_rules = {
                R.INSTRUCTION_REQUIRED: R.CONTINUATION_INSTRUCTION_REQUIRED,
                R.SPECIALIST_DUPLICATE: R.CONTINUATION_SPECIALIST_DUPLICATE,
                R.ASSIGNMENT_INPUTS_REQUIRED: R.CONTINUATION_ASSIGNMENT_INPUTS_REQUIRED,
                R.INPUT_DUPLICATE: R.CONTINUATION_INPUT_DUPLICATE,
                R.INPUT_NOT_OFFERED: R.CONTINUATION_INPUT_NOT_OFFERED,
                R.REVIEWER_INPUT: R.CONTINUATION_REVIEWER_INPUT,
                R.SPECIALIST_REQUIRED: R.CONTINUATION_SPECIALIST_REQUIRED,
                R.ASSIGNMENT_MODE: R.CONTINUATION_ASSIGNMENT_MODE
            }
            rules = [continuation_rules.get(rule, rule) for rule in rules]
        return tuple(dict.fromkeys(rules))


@dataclass(frozen=True, slots=True)
class PremiseDecisionConstraints:
    """Invocation-only question, selected sources and actual analyst choices."""

    question_id: str
    input_ids: tuple[str, ...] = ()
    reply_alternatives: tuple[tuple[str, tuple[str, ...]], ...] = ()
    unavailable_reply_ids: tuple[str, ...] = ()
    allow_read_sources: bool = False

    def question_schema(self) -> dict[str, object]:
        return closed_object({
            "question_id": {"type": "string", "enum": [self.question_id]},
            "slot_id": nonempty_text_schema(),
            "question": nonempty_text_schema(),
            "dependent_domains": string_array(tuple(item.value for item in ArtifactReviewDomain), minimum=1, maximum=3),
        })

    def front_schema(self) -> dict[str, object]:
        assignment = closed_object({
            "agent_id": {"type": "string", "enum": list(SPECIALIST_IDS)},
            "instruction": nonempty_text_schema(),
            "input_ids": string_array(None if self.allow_read_sources else self.input_ids, minimum=1, maximum=64),
            "mode": {"type": "string", "enum": ["premise_analysis"]},
        })
        result = closed_object({
            "kind": {"type": "string", "enum": ["proposal"]},
            "message": nonempty_text_schema(),
            "authoring_scope": {"type": "null"},
            "proposal": closed_object({
                "assignments": {"type": "array", "items": assignment, "minItems": 1, "maxItems": 3},
                "premise_decision": self.question_schema(),
            }),
        })
        result["description"] = "Propose candidate-free analysis of the offered unresolved premise. No drafting, review, artifact or objective authority."
        return result

    def front_rules(self, value: dict[str, object], *, read_ids: tuple[str, ...] = ()) -> tuple[DomainValidationRule, ...]:
        try:
            if set(value) != {"kind", "message", "authoring_scope", "proposal"} or value["kind"] != "proposal" or value["authoring_scope"] is not None or not visible_text(value["message"]):
                raise ValueError("premise proposal is not candidate-free")
            proposal = value["proposal"]
            if type(proposal) is not dict or set(proposal) != {"assignments", "premise_decision"}:
                raise ValueError("premise proposal fields are not closed")
            intent = premise_question_intent_from_dict(proposal["premise_decision"])
            if intent.question_id != self.question_id:
                raise ValueError("premise question was not offered")
            assignments = proposal["assignments"]
            if type(assignments) is not list:
                raise TypeError("premise assignments must be an array")
            from_ids = tuple(item["agent_id"] for item in canonical_assignments(assignments))
            expected = tuple(SPECIALIST_IDS[index] for index, domain in enumerate(ArtifactReviewDomain)
                             if domain in intent.dependent_domains)
            if from_ids != expected:
                raise ValueError("premise analysis omits an affected domain")
            allowed = set(self.input_ids) | (set(read_ids) if self.allow_read_sources else set())
            selected = set()
            for item in assignments:
                if set(item) != {"agent_id", "instruction", "input_ids", "mode"} or item["mode"] != "premise_analysis" or not visible_text(item["instruction"]):
                    raise ValueError("premise assignment is not an analysis")
                if type(item["input_ids"]) is not list or not item["input_ids"] or len(set(item["input_ids"])) != len(item["input_ids"]) or not set(item["input_ids"]) <= allowed:
                    raise ValueError("premise assignment selected unavailable inputs")
                selected.update(item["input_ids"])
            if not set(self.input_ids) <= selected:
                raise ValueError("premise proposal omits required inputs")
        except (KeyError, TypeError, ValueError):
            return (DomainValidationRule.LEAD_OUTCOME_REJECTED,)
        return ()

    def analysis_schema(self) -> dict[str, object]:
        alternative = closed_object({
            "alternative_key": nonempty_text_schema(), "premise_text": nonempty_text_schema(),
            "rationale": nonempty_text_schema(), "tradeoffs": string_array(maximum=4),
            "basis_input_ids": string_array(self.input_ids, minimum=1, maximum=64),
        })
        result = closed_object({
            "assessment": {"type": "string", "enum": ["alternatives_assessed", "conflict", "insufficient_evidence"]},
            "message": nonempty_text_schema(),
            "alternatives": {"type": "array", "items": alternative, "minItems": 0, "maxItems": 4},
        })
        result["description"] = "Return provisional alternatives grounded in the selected actual inputs; separate tradeoffs and unknowns without drafting a candidate."
        return result

    def validate_analysis(self, value: object) -> PremiseAnalysis:
        result = premise_analysis_from_dict(value)
        if any(not set(item.basis_input_ids) <= set(self.input_ids) for item in result.alternatives):
            raise ValueError("premise analysis cites an unavailable selected input")
        return result

    def summary_schema(self) -> dict[str, object]:
        disposition = closed_object({
            "reply_event_id": {"type": "string", "enum": [item[0] for item in self.reply_alternatives]},
            "alternative_keys": string_array(maximum=4),
            "disposition": {"type": "string", "enum": ["used", "modified", "deferred", "rejected", "unavailable"]},
            "reason": nonempty_text_schema(), "residual_cost": nonempty_text_schema(),
        })
        option = closed_object({
            "option_key": nonempty_text_schema(), "premise_text": nonempty_text_schema(),
            "rationale": nonempty_text_schema(),
            "reply_dispositions": {"type": "array", "items": disposition,
                "minItems": len(self.reply_alternatives), "maxItems": len(self.reply_alternatives)},
        })
        result = closed_object({
            "status": {"type": "string", "enum": ["options", "clarification"]},
            "message": nonempty_text_schema(),
            "options": {"type": "array", "items": option, "minItems": 0, "maxItems": 6},
            "recommended_option_key": {"anyOf": [nonempty_text_schema(), {"type": "null", "description": "No recommendation is available; retain USER choice authority."}]},
        })
        result["description"] = "Integrate actual ordered analyst replies into whole provisional options. Disclose every reply disposition, reason and residual cost; USER choice remains separate."
        return result

    def validate_summary(self, value: object) -> PremiseDecisionSummary:
        result = premise_summary_from_dict(value)
        ids = tuple(item[0] for item in self.reply_alternatives)
        actual = dict(self.reply_alternatives)
        for option in result.options:
            if tuple(item.reply_event_id for item in option.reply_dispositions) != ids:
                raise ValueError("premise option must retain every ordered analyst reply")
            for item in option.reply_dispositions:
                if not set(item.alternative_keys) <= set(actual[item.reply_event_id]):
                    raise ValueError("premise disposition invents an alternative")
                if item.disposition == "unavailable" and item.reply_event_id not in self.unavailable_reply_ids:
                    raise ValueError("premise disposition hides available analysis")
        return result


@dataclass(frozen=True, slots=True)
class LeadDiscussionConstraints:
    """Host-selected read-only answer/question contract, never task authority."""

    origin_control_event_id: str
    associated_task_id: str | None = None
    associated_task_revision: int | None = None

    def schema(self) -> dict[str, object]:
        schema = closed_object({
            "kind": {"type": "string", "enum": ["answer", "question"]},
            "message": nonempty_text_schema(),
            "used_evidence_ids": string_array(),
        })
        schema["description"] = (
            "Answer or ask a question in USER-selected discussion; no task "
            "proposal, specialist dispatch, artifact candidate, or write authority."
        )
        return schema

    def prompt_facts(self) -> dict[str, object]:
        return {
            "discussion_control_event_id": self.origin_control_event_id,
            "associated_task": None if self.associated_task_id is None else {
                "task_id": self.associated_task_id,
                "task_revision": self.associated_task_revision,
            },
            "discussion_rule": (
                "Answer or ask a question. Discussion retains the selected task "
                "without changing its goal, scope, completion, or confirmation. "
                "Do not propose execution, dispatch specialists, or claim artifact "
                "facts without actual qualified READ evidence."
            ),
        }
