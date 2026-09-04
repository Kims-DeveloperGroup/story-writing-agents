---
name: subagent-model-selection
description: >-
  Use before selecting, requesting, overriding, or inheriting a subagent model, reasoning
  effort, or dispatch context. Excludes root-session configuration and story-runtime agents.
---

# Subagent Model Selection

[AGENTS.md](../../../AGENTS.md) governs delegation and root ceilings. This procedure cannot
override repository policy.

## Resolve Inputs

Resolve effective root model/effort after aliases, defaults, session overrides, and runtime
child settings/pairs.

`dispatch_context_tokens` counts only the task packet, intentionally inherited turns,
attachments, preloads, and irreducible child material. Record
`observed|estimated|unavailable`, method, and confidence.

|Context|Tokens|
|---|---:|
|`compact`|<=8,192|
|`moderate`|8,193--32,768|
|`large`|32,769--131,072|
|`oversized`|>131,072 or no verified output/reasoning fit|

Unknown fit is oversized. Narrow or split it without relaxing root ceilings.

## Classify Work

|Class|Trigger|Model|Effort|
|---|---|---|---|
|`routine`|Mechanical/reversible; no unresolved judgment/material risk|`gpt-5.6-terra`|`medium`|
|`standard`|Bounded semantics; one established responsibility|`gpt-5.6-sol`|`high`|
|`complex`|Ambiguity/unknown cause; multi-domain/boundary/API/compatibility/migration/security/authorization/concurrency/process/PTY/lifecycle/persistence/resource ownership/difficult nonlocal edges|`gpt-5.6-sol`|`xhigh`|
|`critical`|Two interacting complex triggers or high-consequence security/data-loss/irreversible-migration/breaking-compatibility|`gpt-5.6-sol`|`max`|

Roles default routine; index-only work keeps its actual class.

|Role|Complex uplift|
|---|---|
|Requirements/diagnosis|Substantial ambiguity/unknown cause|
|Modularity|Boundaries|
|Runtime|Security/concurrency/migration/API/difficult nonlocal edges|
|Tests|Security/concurrency/PTY/process/lifecycle/complex coverage|
|Documentation|Normative governance/security/compatibility/migration|

Context affects only model: compact/moderate floor Luna; large floors Sol. Complexity and
risk alone control effort.

## Derive and Select

Derive and clamp both axes independently:

```text
desired_model=max(task_model_target,role_model_target,context_model_target)
desired_effort=max(task_effort_target,role_effort_target)
capped_model=min(desired_model,root_effective_model)
capped_effort=min(desired_effort,root_effective_effort)
```

- Compare only `gpt-5.6-luna < gpt-5.6-terra < gpt-5.6-sol`; assume no other model order.
- Order effort `none < low < medium < high < xhigh < max`. Use `ultra` only when the runtime
  exposes it as comparable and selectable.
- Treat caps as minimum quality; every selected axis must stay <=root.
- Select the exact cap, otherwise the nearest stronger compatible pair within root. Minimize
  upward steps; ties prefer smaller model, then effort increase.
- Never select below cap. Weaker-only availability returns to root.
- Set both overrides or neither. Unresolved root/order/availability/support requires exact
  inheritance; model-only selection may default effort.
- Define `ceiling_limited` when either cap is below desired. High-consequence work must
  narrow or require a stronger root; otherwise proceed compatibly only with recorded risk.

Explicit spawn model/effort values implement the selection. Keep role TOMLs unpinned because
file-level `model` or `model_reasoning_effort` takes precedence over explicit spawn
selection. OpenAI does not natively enforce this task classification or child<=root policy.

A full-history fork cannot take model/effort overrides. Dynamic selection must use no or
bounded inherited history; otherwise inherit both axes and record the reason.

## Record the Decision

Record root/source; context count/class/method/confidence; complexity/triggers; role target;
desired/capped/selected/compatible pairs; limitation; and fallback/inheritance reason.

Keep packets bounded. Include exclusions, accepted decisions, observable criteria,
`observed|inferred|proposed|unknown` evidence, and uncertainty stops.

Sources: [subagents](https://developers.openai.com/codex/subagents/) and
[model guidance](https://developers.openai.com/api/docs/guides/latest-model).
