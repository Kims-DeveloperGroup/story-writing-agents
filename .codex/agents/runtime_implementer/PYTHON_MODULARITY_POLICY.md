# Python Modularity Policy

## Scope and ownership

This policy applies when Python work changes importable structure, responsibility or lifecycle
ownership, dependency direction, public imports/exports, shared abstractions, or structural
compatibility. Local edits within an established boundary do not trigger it.

The repository's `runtime_implementer` owns modularity analysis, implementation review, and
`PYTHON_MODULE_INDEX.md` reconciliation. The root accepts cross-slice decisions and performs
final integration review. Tests, docs, formatting, typing-only edits, ordinary configuration,
and dependency metadata do not need a separate modularity pass unless they change an actual
runtime boundary.

`MUST` and `MUST NOT` are requirements. `SHOULD` and `SHOULD NOT` are defaults that may be
changed for a documented concrete reason. Repository dependency, packaging, security, and
compatibility policies continue to apply.

## Module index

`PYTHON_MODULE_INDEX.md` is a concise, project-owned navigation map of the implemented runtime.
It MUST:

- stay at or below 300 lines;
- organize production Python by subsystem or package rather than exhaustive per-file prose;
- identify each subsystem's responsibility, public facade/import path, and dependency
  direction;
- name state/resource/lifecycle owners where they affect boundaries;
- describe implemented structure only, not proposals or history.

The index does not need one prose entry per module, source/test counts, file totals, test
references for every entry, or duplicated standard-library/third-party import inventories.
Source and packaging configuration remain authoritative for exact file membership.

Adding, deleting, moving, splitting, or merging a module requires an index update only when
the subsystem map, public facade, dependency direction, or ownership meaning changes. The
runtime owner reconciles the index in the same slice. Index-only maintenance changes no
runtime behavior and uses static validation, not runtime tests.

Review the affected source, package exports, public imports, and index sections. A stale public
facade, reversed dependency, missing subsystem, hidden cycle, or incorrect state owner is a
policy violation. Mere omission of a private file name is not.

## Responsibilities and ownership

- Every package/module has one cohesive responsibility and a clearly stated owner for mutable
  state, caches, registries, processes, and other lifecycle resources.
- Other modules interact with owned resources through the owner's public interface.
- Feature-specific behavior stays within its feature unless a stable abstraction has at least
  two compatible real consumers or one accepted near-term consumer with a known contract.
- Avoid unbounded `utils`, `helpers`, `common`, and `misc` dumping grounds.
- Split when responsibilities, consumers, setup, dependencies, or lifecycles vary
  independently. Do not split because of line count alone.
- Merge forwarding-only or inseparable units when doing so preserves intentional public
  imports and accepted compatibility.

## Dependency direction and cycles

- Dependencies are explicit through imports, parameters, constructors, or declared protocols.
- Orchestration and adapters depend on stable domain contracts; reusable domain logic does not
  depend on entry points or concrete infrastructure.
- Circular runtime imports are prohibited. Local imports, string annotations, or
  `TYPE_CHECKING` MUST NOT conceal an invalid runtime cycle.
- Lower-level code does not import higher-level code. Put inversion contracts at the stable
  lower-level boundary.
- Cross-boundary callers MUST NOT use another module's underscored names or mutate its private
  state.
- Optional integrations stay behind adapters so importing core behavior does not require the
  optional infrastructure.
- Wildcard imports and production `sys.path` mutation are prohibited. Dynamic imports are
  limited to intentional extension points or documented platform constraints and validate
  their interface with actionable failures.

## Public interfaces and compatibility

- Public imports, facades, protocols, exports, call signatures, result shapes, public
  exceptions, and externally visible side effects are intentional and minimal.
- Package `__init__.py`, `__all__`, entry points, documentation, and supported import paths MUST
  agree. A re-export is an API decision, not housekeeping.
- Consumers use the owning public path instead of a peer's private layout.
- Moving a public symbol preserves the established path when compatibility is required. A
  temporary forwarding export has a removal plan.
- Breaking changes require explicit acceptance, updated consumers, migration notes when
  applicable, and practical compatibility coverage.
- A facade or protocol MUST create a stable boundary; do not add one that only duplicates a
  concrete implementation.

## State, imports, and resources

Importing reusable modules MUST NOT perform network calls, filesystem writes, authentication,
subprocess execution, thread creation, or application startup. Deterministic local
registration is allowed only when it belongs to the stated responsibility.

Avoid mutable module state. When required, the owner exposes initialization, mutation,
synchronization, reset, and shutdown behavior appropriate to the resource. Keep domain logic
side-effect-light and inject filesystem, provider, process, clock, and environment adapters
through narrow interfaces.

Production modules reside under packaging-declared source roots, are directly importable in a
clean installation, and do not depend on checkout layout or test-only path manipulation.
Package data, entry points, and discovery follow active packaging configuration.

## Modularity review

Before structural implementation, record:

1. the current responsibility, public facade/imports, dependencies, state/resource owner, and
   compatibility constraints;
2. the accepted target boundary and dependency direction;
3. affected consumers and any migration or forwarding path;
4. the compact index delta.

After implementation, verify public imports/facades, dependency direction, absence of runtime
cycles and private-boundary access, lifecycle ownership, affected consumers, and index
consistency. Runtime tests are required only when runtime code changed; otherwise static import,
path, reference, and diff checks are sufficient.

An exception records the rule, scope, reason, owner, risk, containment, and removal or review
condition. Convenience, deadline pressure, and unchanged tests alone are not sufficient.
