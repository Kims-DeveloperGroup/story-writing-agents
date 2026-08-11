# Python Modularity Policy

## Authority and Scope

This policy governs Python package and module boundaries in any project that
selects it. It applies when work adds, removes, moves, splits, or merges an
importable unit; changes responsibility, dependency direction, public imports
or exports, shared abstractions, or state and resource ownership; or makes a
structural compatibility decision. It also applies to behavior-preserving
refactors with those effects.

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

This policy governs internal structure. Apply any project dependency,
packaging, security, or compatibility policies separately when a change crosses
those concerns. Repository instructions take precedence when they impose a
stricter compatible requirement.

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

## Python Module Index

The project inventory is `<PROJECT_ROOT>/PYTHON_MODULE_INDEX.md`. It is a
project-owned descriptive navigation record for the current Python
implementation; this packaged policy remains the normative source for boundary
rules. Proposed or partially implemented structures belong in a modularity
brief, not in the index. When the index is missing, the modularity maintainer
MUST inventory the implementation and create it at the project root in its own
write-enabled pass; separate user approval is not required.

### Coverage and entry format

- The index MUST name every production importable package and module beneath
  each declared Python source root exactly once. Cache directories, generated
  bytecode, and test modules are excluded unless they are intentionally shipped
  as part of the installed package.
- Each entry MUST record the import path, source path, one current
  responsibility, supported public imports or entry points, direct internal
  dependencies, owned state or external resources, material side effects, and
  primary verification or documentation references.
- Package entries MUST identify supported re-exports. Determine the supported
  surface from package-level exports such as `__all__`, documented import paths,
  and entry points in the active build or packaging manifests. A direct import
  used only by tests does not by itself make a symbol public.
- Dependencies MUST describe runtime direction between project modules. Type-
  only imports MAY be noted when they materially constrain a boundary, but
  standard-library and third-party import lists SHOULD NOT be duplicated.
- An entry MUST describe observed behavior only. It MUST NOT present a proposed
  move, split, export, dependency, or ownership transfer as already implemented.

### Freshness and reconciliation

- Modularity analysis MUST read the index as a starting map and verify all
  affected entries against the source files, package exports, imports, tests,
  and documentation before relying on them.
- Adding, deleting, renaming, moving, splitting, or merging a module requires a
  matching index change. So does a material change to responsibility, supported
  imports, direct dependency direction, state or resource ownership, entry
  points, or side effects.
- The index MUST be reconciled after the final implementation is observable and
  within the same complete change. Removed units and obsolete paths MUST be
  deleted rather than retained as historical records.
- When implementation and index disagree, the implementation is the observed
  fact for diagnosis, but the change is incomplete. Either bring the code back
  to the accepted boundary or update the index to the accepted implemented
  state; do not silently choose a new design.
- Review MUST verify source-path parity for the whole declared source root and
  semantic parity for every affected entry. A missing index, an unlisted module,
  a deleted-but-listed module, or stale responsibility, export, dependency, or
  ownership information is a policy violation.
- Index-only maintenance MUST NOT change production behavior. It requires a
  repository-derived inventory and exclusive ownership of the index file.

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

- Production packages and modules MUST remain under the source roots declared
  by the project's build configuration, packaging manifests, or repository
  instructions. Introducing or moving a source root is a packaging and
  modularity decision that requires explicit scope.
- Importable package directories MUST contain `__init__.py` unless an explicit
  namespace-package design is authorized and documented.
- Imports within a package MUST follow the project's established absolute or
  explicit-relative style consistently. A change MUST NOT mix styles without a
  documented interoperability reason.
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
- The active build or packaging manifest MUST be treated as the source of truth
  for package discovery, entry points, package data, and dependency metadata.
  Any separate project dependency or packaging policy also applies.

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

1. Read the root Python module index, or complete the required bootstrap when
   it is missing. Check source-path parity and verify every affected entry
   against the implementation.
2. State the current and intended responsibility of every affected package or
   module.
3. Inventory public import paths, consumers, imports, side effects, state
   ownership, tests, and documentation.
4. Draw or describe the intended dependency direction and identify any cycle,
   private reach-through, or compatibility constraint.
5. Decide whether the work is a split, merge, move, extraction, adapter, or
   interface change and explain why that operation improves the boundary.

During implementation:

1. Make the smallest complete structural change.
2. Ensure consumers, exports, type annotations, tests, and documentation are
   updated within the same integrated change. Repository specialists MAY
   complete those portions sequentially under the main agent; this requirement
   does not authorize concurrent writers or overlapping file ownership.
3. Reconcile affected module-index entries after the implemented structure is
   stable. When another agent owns production code, perform index maintenance
   as a sequential, index-only pass.
4. Preserve public behavior and import paths unless a breaking change is
   explicitly authorized.
5. Avoid unrelated cleanup and new abstractions outside the demonstrated
   boundary need.

Before completion:

1. Import each affected module through its supported path.
2. Check for cycles, stale paths, private cross-boundary access, and unintended
   import-time side effects.
3. Run focused tests followed by the relevant broader suite.
4. Review the diff for unrelated movement, compatibility changes, and missing
   documentation.
5. Compare discovered importable units with the module index and recheck every
   affected entry for responsibility, exports, dependencies, ownership, and
   side effects.
6. Report the resulting responsibility and dependency direction, API impact,
   module-index consistency, checks, and any remaining coupling.

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
