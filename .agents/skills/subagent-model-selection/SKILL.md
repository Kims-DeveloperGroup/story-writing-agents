---
name: subagent-model-selection
description: >-
  Use for subagent model/effort selection/override/inheritance; excludes root/story agents.
---

Resolve root model/effort after aliases/defaults/session overrides/runtime child pairs.

|Class|Trigger|Model|Effort|
|---|---|---|---|
|`routine`|Mechanical/reversible; no unresolved judgment/material risk|`gpt-5.6-terra`|`medium`|
|`standard`|Bounded semantics; one-established-responsibility|`gpt-5.6-sol`|`high`|
|`complex`|Ambiguity/unknown cause; multi-domain/boundary/API/compatibility/migration/security/authorization/concurrency/process/PTY/lifecycle/persistence/resource ownership/difficult-nonlocal-edges|`gpt-5.6-sol`|`xhigh`|
|`critical`|Two interacting complex triggers or high-consequence security/data-loss/irreversible-migration/breaking-compatibility|`gpt-5.6-sol`|`max`|

Roles default routine; index-only keeps actual class.

|Role|Uplift|
|---|---|
|Requirements/diagnosis|Substantial-ambiguity/unknown-cause|
|Modularity|Boundaries|
|Runtime|Security/concurrency/migration/API/difficult-nonlocal-edges|
|Tests|Security/concurrency/PTY/process/lifecycle/complex coverage|
|Docs|Normative-governance/security/compatibility/migration|

```text
desired_model=max(task_model_target,role_model_target)
desired_effort=max(task_effort_target,role_effort_target)
capped_model=min(desired_model,root_effective_model)
capped_effort=min(desired_effort,root_effective_effort)
```

Model order: `gpt-5.6-luna<gpt-5.6-terra<gpt-5.6-sol`; effort:
`none<low<medium<high<xhigh<max`; add `ultra` only when runtime-comparable. Select exact cap
or nearest stronger compatible pair <=root by fewest steps; tie: smaller model, then higher
effort. Never weaken; weaker-only returns.

Override both or neither; unresolved root/order/support/availability inherits exactly;
model-only may default effort. Implement selection through explicit spawn values. Full-history
forbids overrides: use bounded/no history, else inherit both with reason. File-level
`model|model_reasoning_effort` beats spawn; keep role TOMLs unpinned.

`ceiling_limited` means either cap is below desired: high-consequence work narrows or needs
stronger root; otherwise record risk. Record root/source, complexity/triggers, role target,
desired/capped/selected/compatible pairs, limitation, fallback/inheritance, exclusions,
accepted decisions, criteria, evidence `observed|inferred|proposed|unknown`, uncertainty stops.

[OpenAI](https://developers.openai.com/codex/subagents/) enforces neither classification nor
child<=root.
