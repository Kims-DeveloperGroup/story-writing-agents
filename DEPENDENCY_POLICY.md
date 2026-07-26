# Python Dependency Policy

- Use `pyproject.toml` as the single source of truth for project metadata and
  dependencies.
- Separate runtime, development, and test dependencies.
- Use one package manager consistently and commit its lock file.
- Install dependencies in a virtual environment; never rely on global
  packages.
- Declare every directly imported package explicitly.
- Add dependencies only when the standard library or an existing dependency
  cannot reasonably provide the required behavior.
- Review a package's maintenance, license, security history, size, and Python
  compatibility before adding it.
- Keep `pyproject.toml` and the lock file synchronized. Do not edit the lock
  file manually.
- Run tests, linting, type checks, and dependency audits after dependency
  changes.
- Remove unused dependencies and keep dependency changes in focused commits.
- CI and deployment must install from the lock file and fail on dependency
  metadata inconsistencies.
