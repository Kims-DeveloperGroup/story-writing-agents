---
name: subagent-model-selection
description: >-
  Select a root-capped model and reasoning effort for runtime_implementer or test_engineer;
  excludes root and story-runtime agents.
---

Use the least expensive capable pair. Inherit the root pair when support, limits, or task
classification are uncertain. A child never exceeds the root's effective model or effort.

| Class | Trigger | Target |
| --- | --- | --- |
| `routine` | Mechanical, bounded, reversible, no unresolved judgment. | Fast model, medium effort. |
| `standard` | Bounded semantics within one established responsibility. | General model, high effort. |
| `complex` | Unknown failure, boundary/API/migration/security/concurrency/lifecycle risk. | General model, xhigh effort. |
| `critical` | Multiple complex triggers or high-consequence irreversible risk. | Strongest root-compatible pair. |

`runtime_implementer` gains complexity for public APIs, modularity, migration, security,
concurrency, persistence, or difficult nonlocal behavior. `test_engineer` gains complexity for
unknown regressions, process/PTY/concurrency/lifecycle behavior, or broad coverage design.

Cap the desired pair at the root's effective pair. Override both model and effort together;
if a supported pair cannot be determined, inherit both. Full-history forks inherit both.
When the cap is weaker than the task needs, narrow high-consequence work or return it to the
root; otherwise record the limitation.

Record only: root pair/source, task class and triggers, selected or inherited pair, cap or
availability limitation, and any narrowing decision. Do not spend budget on model selection
when default inheritance is adequate.
