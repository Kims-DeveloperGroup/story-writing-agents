"""Fresh, uncached source graph collection for boundary assertions."""
import ast
from pathlib import Path


def module_inventory(source_root: Path):
    modules: dict[str, Path] = {}
    for path in source_root.rglob("*.py"):
        relative = path.relative_to(source_root)
        parts = (
            ("story_writing_agents", *relative.parts[:-1])
            if relative.name == "__init__.py"
            else ("story_writing_agents", *relative.with_suffix("").parts))
        modules[".".join(parts)] = path
    return modules






def internal_edges(modules, runtime_nodes=None):
    def resolve(target: str) -> str | None:
        parts = target.split(".")
        while parts and ".".join(parts) not in modules:
            parts.pop()
        candidate = ".".join(parts)
        return candidate if candidate in modules else None
    if runtime_nodes is None:
        def runtime_nodes(node: ast.AST):
            yield node
            if isinstance(node, ast.If) and (
                isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
                or isinstance(node.test, ast.Attribute) and node.test.attr == "TYPE_CHECKING"
            ):
                for child in node.orelse:
                    yield from runtime_nodes(child)
                return
            for child in ast.iter_child_nodes(node):
                yield from runtime_nodes(child)
    edges: set[tuple[str, str]] = set()
    for module, path in modules.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        context = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
        for node in runtime_nodes(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = context[: len(context) - (node.level - 1)]
                    if node.module:
                        base.extend(node.module.split("."))
                    target = ".".join(base)
                else:
                    target = node.module or ""
                targets.append(target)
                targets.extend(
                    f"{target}.{alias.name}" if target else alias.name
                    for alias in node.names
                )
            for target in targets:
                resolved = resolve(target)
                if resolved is not None and resolved != module:
                    edges.add((module, resolved))
    return edges


def module_graph(source_root, *, runtime_nodes=None):
    modules = module_inventory(source_root)
    return modules, internal_edges(modules, runtime_nodes)


def assert_acyclic(self, modules, edges):
    successors = {module: set() for module in modules}
    for source, target in edges:
        successors[source].add(target)
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(module: str) -> None:
        if module in visiting:
            self.fail(f"internal import cycle reaches {module}")
        if module in visited:
            return
        visiting.add(module)
        for dependency in successors[module]:
            visit(dependency)
        visiting.remove(module)
        visited.add(module)
    for module in modules:
        visit(module)
