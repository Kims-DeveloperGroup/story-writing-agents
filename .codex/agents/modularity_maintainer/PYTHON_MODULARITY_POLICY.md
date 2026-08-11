# Python Modularity Policy

## Authority and Scope

This policy governs Python package and module boundaries for this repository.
It applies when work adds, removes, moves, splits, or merges an importable unit;
changes responsibility, dependency direction, public imports or exports,
shared abstractions, or state and resource ownership; or makes a structural
compatibility decision. It also applies to behavior-preserving refactors with
those effects.

It does not require a modularity workflow for every Python edit. A localized
change within an established module responsibility remains outside this policy
when imports, exports, ownership, dependency direction, and public contracts do
not change.

The terms **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are
normative:

- **MUST** and **MUST NOT** define requirements. An exception requires explicit
  authorization and the record described in
  [Policy Exceptions](#policy-exceptions).
- **SHOULD** and **SHOULD NOT** define the normal design. Deviations require a
  concrete reason in the change description.
- **MAY** identifies an allowed option, not a default requirement.

This policy governs internal structure. The repository's
[Python Dependency Policy](../../../DEPENDENCY_POLICY.md) separately governs
third-party dependencies, packaging metadata, package-manager use, and lock
files. Both policies apply when a change crosses those concerns.

## Terms

- A **package** is an importable namespace that owns a cohesive capability or
  feature and may contain modules or subpackages.
- A **module** is one importable source file with a cohesive responsibility.
- A **feature boundary** contains behavior, data, and interfaces that change
  for the same product reason.
- A **public interface** is any documented or exported import path, callable,
  type, constant, data shape, exception, or observable behavior used outside
  its owning module.
- A **private implementation** is an implementation detail not promised to
  consumers. A leading underscore communicates intent but does not by itself
  make external use safe.
- **Independent** means understandable, importable, testable, and reusable
  without initializing or depending on unrelated features. It does not mean
  dependency-free.
- **Cohesion** is the degree to which a unit's contents serve the same
  responsibility. **Coupling** is the knowledge one unit requires about
  another unit's implementation or lifecycle.

## Responsibility and Ownership

### Required boundaries

- Every package and module MUST have one concise responsibility that can be
  stated without joining unrelated concerns with “and.”
- Code that changes for the same feature reason SHOULD remain together. Code
  that changes for unrelated reasons SHOULD live behind separate boundaries.
- Each stateful resource, cache, registry, or lifecycle MUST have one clear
  owner. Other modules MUST interact with it through that owner's public
  interface.
- Feature-specific code MUST remain within its feature unless it satisfies the
  shared-code criteria below.
- A module MUST NOT become a dumping ground such as an unbounded `utils`,
  `helpers`, `common`, or `misc` module. A shared module needs a specific domain
  name and responsibility.

### When to split

A package or module SHOULD be split when at least one of these conditions is
demonstrated:

- it owns responsibilities that change independently;
- consumers need distinct subsets of its interface;
- testing one responsibility requires unrelated setup;
- optional infrastructure is pulled into consumers that do not use it;
- dependency direction becomes unclear or cyclic;
- separate ownership or lifecycle boundaries are being hidden.

Line count alone MUST NOT trigger a split. The proposed units still need clear
responsibilities and interfaces.

### When to merge

Packages or modules SHOULD be merged when they have no meaningful independent
responsibility, always change together, expose forwarding-only interfaces, or
create indirection without reducing coupling. A merge MUST preserve intentional
public imports or include an authorized migration.

## Dependency Direction

- Dependencies MUST be explicit in imports, parameters, constructors, or
  declared interfaces. Behavior MUST NOT depend on import order or unrelated
  initialization having occurred first.
- Dependencies MUST point from orchestration and adapters toward stable domain
  contracts, not from reusable domain logic toward entry points or concrete
  infrastructure.
- Circular runtime imports between modules or packages are prohibited.
- `typing.TYPE_CHECKING`, local imports, string annotations, or deferred imports
  MUST NOT be used merely to conceal an architectural cycle. They MAY break a
  type-only import cycle when the runtime dependency direction is already
  valid and documented.
- A module MUST NOT reach into another module's underscored names or mutate its
  internal state. Cross-boundary use goes through the owner's public interface.
- A lower-level module MUST NOT import a higher-level workflow solely to call
  back into it. Use an explicit callback, protocol, or data contract owned by
  the lower-level boundary when inversion is required.
- Optional integrations SHOULD be isolated behind adapters so importing core
  behavior does not require optional infrastructure.
- Dynamic imports MAY be used only for an intentional extension point or to
  satisfy a documented platform constraint. They MUST validate the imported
  interface and surface actionable failures.
- Production code MUST NOT modify `sys.path` to cross package boundaries.
- Wildcard imports are prohibited.

## Public Interfaces and Compatibility

- Public interfaces MUST be intentional, minimal, documented at the point of
  ownership, and covered by contract-focused tests.
- Package `__init__.py` exports and module `__all__` declarations MUST match the
  supported public surface. Adding a re-export is an API decision, not a
  convenience-only cleanup.
- Consumers SHOULD import from the owning public path. They MUST NOT depend on
  a peer's private file layout when a supported package-level path exists.
- Function and method signatures, accepted value domains, return shapes,
  raised public exceptions, and externally visible side effects are part of
  compatibility unless explicitly documented otherwise.
- Moving a public symbol MUST preserve its established import path with a
  forwarding export when compatibility is required. The forwarding export
  MUST have a removal plan if it is temporary.
- A breaking interface change requires explicit authorization, updated
  consumers, migration notes, compatibility tests where practical, and removal
  of stale exports in the same complete change.
- Facades MUST add a stable abstraction boundary. A facade that only duplicates
  every underlying symbol without hiding volatility SHOULD NOT be introduced.

## State, Side Effects, and Resource Lifecycles

- Importing a reusable module MUST NOT perform network calls, filesystem
  writes, provider authentication, subprocess execution, thread creation, or
  application startup.
- Import-time reads or registration MAY occur only when they are deterministic,
  local, required by the module's stated responsibility, and tested.
- Mutable module-level state SHOULD be avoided. When it is necessary, the
  owning module MUST define initialization, mutation, synchronization, reset,
  and shutdown behavior.
- External resources MUST have an explicit owner and lifecycle. Acquisition and
  release SHOULD use context managers or an equally visible lifecycle API.
- Domain computation SHOULD remain deterministic and side-effect-light.
  Filesystem, process, provider, clock, and environment access SHOULD be kept in
  adapters and passed into reusable logic through narrow interfaces.
- Tests MUST be able to replace external collaborators without initializing the
  entire application.

## Reuse and Shared Code

- Shared code MUST represent a stable domain or infrastructure concept, not
  merely identical syntax.
- Extraction normally requires at least two real consumers with compatible
  semantics. An approved near-term consumer MAY justify earlier extraction
  when its contract is already known.
- Shared interfaces MUST be owned by the side that defines the abstraction,
  not by an arbitrary consumer or a generic utility package.
- A reusable module SHOULD accept collaborators and data through explicit
  inputs instead of importing application singletons.
- Feature flags or mode parameters MUST NOT accumulate unrelated behaviors in
  one implementation. Split strategies or adapters when modes have distinct
  dependencies, invariants, or lifecycles.
- Limited local duplication MAY be preferable to a premature abstraction. Any
  later extraction must reconcile semantic differences rather than hide them.

## Python Source and Import Conventions

- Production packages and modules MUST follow the repository's `src/` layout.
  This project's importable runtime remains under `src/story_writing_agents/`
  unless a separately authorized packaging change establishes another root.
- Importable package directories MUST contain `__init__.py` unless an explicit
  namespace-package design is authorized and documented.
- In this repository, sibling imports within `story_writing_agents` SHOULD use
  explicit relative imports consistently. A change MUST NOT mix import styles
  without a documented interoperability reason.
- Imports SHOULD be grouped as standard library, third-party, and local, with
  unused imports removed.
- Modules MUST be directly importable in a clean interpreter after installation
  of declared dependencies. They MUST NOT rely on the current working directory
  or test-only path manipulation.
- Public callables, classes, and data structures SHOULD use type annotations.
  Boundary types MUST describe accepted optionality and collection shapes
  accurately; `Any` requires a boundary-specific reason.
- Protocols or abstract base classes SHOULD be introduced only when multiple
  implementations, substitution in tests, or a real dependency inversion
  requires them. They MUST NOT duplicate a concrete class without reducing
  coupling.
- Package data MUST be declared through the packaging configuration rather than
  discovered from an assumed checkout layout.
- `pyproject.toml` is the source of truth for project packaging metadata. Any
  third-party dependency or packaging-metadata change also triggers
  [DEPENDENCY_POLICY.md](../../../DEPENDENCY_POLICY.md).

## Readability and Documentation

- Names MUST communicate domain responsibility. Generic names are acceptable
  only inside a narrowly named owning module where their meaning is clear.
- Public modules, classes, and non-obvious functions SHOULD document purpose,
  inputs, outputs, side effects, raised exceptions, and lifecycle constraints.
- Comments SHOULD explain why a boundary or invariant exists, not narrate
  syntax.
- Functions and classes SHOULD remain focused enough that their invariants and
  collaborators are visible without tracing unrelated workflows.
- File size, function length, or class count MAY be used as investigation
  signals but MUST NOT be enforced as standalone architectural thresholds.
- Dead forwarding layers, obsolete compatibility aliases, and unused exports
  MUST be removed when their approved compatibility period ends.

## Testing Requirements

- New or changed modules MUST have focused tests for their public behavior and
  important failure modes.
- Tests for a public contract SHOULD import through its supported public path.
  Direct-module tests MAY cover private algorithms but do not establish those
  names as public API.
- A new or moved module MUST be tested for direct import in a clean process when
  import-time behavior or dependency availability is material.
- Boundary tests MUST cover collaborator failures, invalid data, and resource
  cleanup where those behaviors cross modules.
- Refactors MUST retain behavior tests before relying on new implementation
  tests. Public symbol moves require coverage for preserved import paths or the
  authorized migration.
- Test helpers MUST NOT become a production dependency. Production modules MUST
  NOT import from `tests`.
- Verification MUST check for circular imports, stale import paths, unintended
  exports, and the relevant focused and broader test suites. Use existing tools
  before adding a new dependency solely for boundary checking.

## Required Change Workflow

Before editing:

1. State the current and intended responsibility of every affected package or
   module.
2. Inventory public import paths, consumers, imports, side effects, state
   ownership, tests, and documentation.
3. Draw or describe the intended dependency direction and identify any cycle,
   private reach-through, or compatibility constraint.
4. Decide whether the work is a split, merge, move, extraction, adapter, or
   interface change and explain why that operation improves the boundary.

During implementation:

1. Make the smallest complete structural change.
2. Ensure consumers, exports, type annotations, tests, and documentation are
   updated within the same integrated change. Repository specialists MAY
   complete those portions sequentially under the main agent; this requirement
   does not authorize concurrent writers or overlapping file ownership.
3. Preserve public behavior and import paths unless a breaking change is
   explicitly authorized.
4. Avoid unrelated cleanup and new abstractions outside the demonstrated
   boundary need.

Before completion:

1. Import each affected module through its supported path.
2. Check for cycles, stale paths, private cross-boundary access, and unintended
   import-time side effects.
3. Run focused tests followed by the relevant broader suite.
4. Review the diff for unrelated movement, compatibility changes, and missing
   documentation.
5. Report the resulting responsibility and dependency direction, API impact,
   checks, and any remaining coupling.

## Policy Exceptions

An authorized exception MUST be recorded with:

- the exact rule being waived;
- the technical reason normal compliance is not currently viable;
- the affected files and consumers;
- compatibility, maintenance, and testing risks;
- containment measures and verification performed; and
- a removal condition or explicit decision that the exception is permanent.

An undocumented exception is a policy violation. Existing noncompliance does
not authorize new noncompliance; avoid expanding it and report it when it is
material to the requested change.
