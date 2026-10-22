"""Complete original whole-front test; bounded hosted diagnostic, never canonical credit.

Only TEST functions are cloned. The original application and JSON callables are
invoked unchanged. Run this file only in the root-authorized hosted carrier.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import copy
from collections import deque
import gc
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import signal
import sys
import time
import traceback
import types
import unittest


# Full source identity is intentionally a literal, separately checked by root.
SOURCE = "a86b5f63742e94381c67650731e922a158f78e81"
SOURCE_TREE = "2462953c0ad3d3e24344dcc8af4deceda75549b6"
TEST_PATH = "tests/test_lead_orchestration.py"
FIXTURE_PATH = "tests/provider_wire_fixture.py"
TEST_SHA = "d57317afeba5a163dd178a3c81cef24fbff2e999f09013cc0154acae0e9f5f7d"
FIXTURE_SHA = "3cf85cf8a106b12b2b34261db8090fc769118233a332efd683e0a51b24163070"
CASE_CLASS = "LeadOutcomeAndProposalTests"
METHOD = "test_front_schema_is_minimal_closed_and_provider_strict"
BASE_CLASS = "LeadOrchestrationTestCase"
HELPER = "_require_fixture_field_neighbors"
MAX_EVENTS_BYTES = 16 * 1024 * 1024
MAX_REPORT_BYTES = 16 * 1024 * 1024
ALIAS = "__whole_front_timer_v46__"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(node):
    return ast.dump(node, include_attributes=False)


def write_json(path, value):
    raw = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    if len(raw) > MAX_REPORT_BYTES:
        raise RuntimeError("diagnostic JSON exceeds the frozen artifact bound")
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(raw)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


class Timer:
    """Exact fixed-site aggregates with bounded raw prefix and progress evidence."""

    def __init__(self, path):
        self.stream = path.open("x", encoding="utf-8")
        self.progress_path = path.with_name("progress.json")
        self.bytes_written = 0
        self.started_wall = time.perf_counter()
        self.started_cpu = time.process_time()
        self.stack = []
        self.next_id = 0
        self.summaries = {}
        self.top_level_spans = []
        self.signals = []
        self.overflow = False
        self.closed = False
        self.recent = deque(maxlen=32)
        self.observed_event_count = 0
        self.stream_record_count = 0
        self.omitted_raw_events = 0
        self.checkpoint_count = 0
        self.last_checkpoint = self.started_wall - 5
        self.loop_entries = {}
        self.censored_at_interrupt = []
        self.test_replacements = []

    def active(self):
        wall, cpu = time.perf_counter(), time.process_time()
        return [{"span": s.id, "parent": s.parent, "label": s.label,
                 "file": s.file, "line": s.line,
                 "elapsed_wall_seconds": wall - s.wall,
                 "elapsed_cpu_seconds": cpu - s.cpu,
                 "complete": False} for s in self.stack]

    def write_record(self, row):
        raw = json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n"
        size = len(raw.encode("utf-8"))
        if self.bytes_written + size > MAX_EVENTS_BYTES:
            self.overflow = True
            raise RuntimeError("bounded evidence exceeds 16 MiB; diagnostic incomplete")
        self.stream.write(raw)
        self.stream.flush()
        self.bytes_written += size
        self.stream_record_count += 1

    def checkpoint(self, force=False):
        now = time.perf_counter()
        if not force and now - self.last_checkpoint < 5:
            return
        self.last_checkpoint = now
        self.checkpoint_count += 1
        snapshot = {"schema_version": 2, "diagnostic": "whole-front-40m-v46",
            "status": "running_or_partial", "checkpoint": self.checkpoint_count,
            "wall_elapsed_seconds": now - self.started_wall,
            "cpu_elapsed_seconds": time.process_time() - self.started_cpu,
            "started_span_count": self.next_id, "observed_event_count": self.observed_event_count,
            "omitted_raw_events": self.omitted_raw_events,
            "trace_complete": False, "active_spans": self.active(),
            "censored_at_interrupt": self.censored_at_interrupt,
            "loop_iterations_entered": dict(self.loop_entries),
            "summaries": list(self.summaries.values()), "recent_events": list(self.recent)}
        write_json(self.progress_path, snapshot)
        # The full current aggregates live in the bounded atomic snapshot, not every stream row.
        self.write_record({"event": "checkpoint", "checkpoint": self.checkpoint_count,
            "wall_elapsed_seconds": snapshot["wall_elapsed_seconds"],
            "cpu_elapsed_seconds": snapshot["cpu_elapsed_seconds"],
            "started_span_count": self.next_id, "observed_event_count": self.observed_event_count,
            "summary_site_count": len(self.summaries), "active_spans": snapshot["active_spans"],
            "omitted_raw_events": self.omitted_raw_events})

    def emit(self, row):
        self.observed_event_count += 1
        self.recent.append(row)
        # Prefix plus original top-level method statement boundaries; not a complete raw trace.
        if row["span"] <= 128 or row["label"].startswith("whole_front."):
            self.write_record(row)
        else:
            self.omitted_raw_events += 1
        self.checkpoint()

    def iteration(self, file, line):
        key = f"{file}:{line}"
        self.loop_entries[key] = self.loop_entries.get(key, 0) + 1

    def span(self, label, file="harness", line=0, detail=None):
        return Span(self, label, file, line, detail)

    def call(self, label, file, line, function, /, *args, **kwargs):
        # Exact TEST-function replacements handle original local fixture imports too.
        # No application callable is eligible, and no module namespace is mutated.
        for original, clone in self.test_replacements:
            if function is original:
                function = clone
                break
        detail = {"projection": kwargs.get("projection", False)} if label == "fixture.field_neighbors" else None
        with self.span(label, file, line, detail):
            return function(*args, **kwargs)

    def attr(self, label, file, line, value, name):
        with self.span(label, file, line):
            return getattr(value, name)

    def close(self):
        self.checkpoint(force=True)
        self.stream.close()
        self.closed = True


class Span:
    def __init__(self, timer, label, file, line, detail):
        self.timer = timer
        self.label, self.file, self.line, self.detail = label, file, line, detail
        self.child_wall = self.child_cpu = 0.0

    def __enter__(self):
        t = self.timer
        t.next_id += 1
        self.id = t.next_id
        self.parent = t.stack[-1].id if t.stack else None
        self.wall, self.cpu = time.perf_counter(), time.process_time()
        if len(t.stack) >= 128:
            raise RuntimeError("observed span nesting exceeds fixed bound")
        t.stack.append(self)
        try:
            t.emit({"event": "begin", "span": self.id, "parent": self.parent,
                    "label": self.label, "file": self.file, "line": self.line,
                    "wall_elapsed": self.wall - t.started_wall,
                    "cpu_elapsed": self.cpu - t.started_cpu, "detail": self.detail})
        except BaseException as error:
            self.__exit__(type(error), error, error.__traceback__)
            raise
        return self

    def __exit__(self, kind, value, tb):
        wall, cpu = time.perf_counter() - self.wall, time.process_time() - self.cpu
        t = self.timer
        if not t.stack or t.stack[-1] is not self:
            raise RuntimeError("timing stack ownership changed")
        t.stack.pop()
        if min(wall, cpu) < 0 or self.child_wall > wall + 1e-9 or self.child_cpu > cpu + 1e-9:
            raise RuntimeError("invalid nested timing clock relation")
        outcome = "complete" if kind is None else (
            "censored" if issubclass(kind, (KeyboardInterrupt, SystemExit)) else "raised_to_caller")
        exclusive_wall = max(0.0, wall - self.child_wall)
        exclusive_cpu = max(0.0, cpu - self.child_cpu)
        if t.stack:
            t.stack[-1].child_wall += wall
            t.stack[-1].child_cpu += cpu
        key = f"{self.file}:{self.line}:{self.label}"
        if key not in t.summaries and len(t.summaries) >= 4096:
            raise RuntimeError("timing site count exceeds fixed bound")
        row = t.summaries.setdefault(key, {"label": self.label, "file": self.file,
            "line": self.line, "count": 0, "outcomes": {}, "inclusive_wall_seconds": 0.0,
            "inclusive_cpu_seconds": 0.0, "exclusive_observed_wall_seconds": 0.0,
            "exclusive_observed_cpu_seconds": 0.0, "min_wall_seconds": wall,
            "max_wall_seconds": wall, "min_cpu_seconds": cpu, "max_cpu_seconds": cpu})
        family = "censored" if outcome == "censored" else "completed_or_raised"
        for clock, value in (("wall", wall), ("cpu", cpu)):
            field = family + "_inclusive_" + clock + "_seconds"
            row[field] = row.get(field, 0.0) + value
        row["count"] += 1
        row["outcomes"][outcome] = row["outcomes"].get(outcome, 0) + 1
        row["inclusive_wall_seconds"] += wall
        row["inclusive_cpu_seconds"] += cpu
        row["exclusive_observed_wall_seconds"] += exclusive_wall
        row["exclusive_observed_cpu_seconds"] += exclusive_cpu
        row["min_wall_seconds"] = min(row["min_wall_seconds"], wall)
        row["max_wall_seconds"] = max(row["max_wall_seconds"], wall)
        row["min_cpu_seconds"] = min(row["min_cpu_seconds"], cpu)
        row["max_cpu_seconds"] = max(row["max_cpu_seconds"], cpu)
        if self.parent is None:
            t.top_level_spans.append({"span": self.id, "label": self.label,
                "outcome": outcome, "wall_seconds": wall, "cpu_seconds": cpu})
        if not t.overflow:
            t.emit({"event": "end", "span": self.id, "parent": self.parent,
                "label": self.label, "file": self.file, "line": self.line,
                "outcome": outcome, "exception_type": kind.__name__ if kind else None,
                "inclusive_wall_seconds": wall, "inclusive_cpu_seconds": cpu,
                "exclusive_observed_wall_seconds": exclusive_wall,
                "exclusive_observed_cpu_seconds": exclusive_cpu,
                "wall_elapsed": time.perf_counter() - t.started_wall,
                "cpu_elapsed": time.process_time() - t.started_cpu})
        return False


def call_label(node):
    name = ast.unparse(node.func)
    if name == "json.dumps":
        return "json.dumps"
    if name == "json.loads":
        return "json.loads"
    if name == "copy.deepcopy":
        return "payload.deepcopy"
    if name == "diagnose_json_text":
        return "diagnostic.json." + ast.unparse(node.args[1])
    if isinstance(node.func, ast.Attribute):
        attr = node.func.attr
        if attr.startswith("assert"):
            return "assertion." + attr
        if attr == HELPER:
            return "fixture.field_neighbors"
        if attr in {"findings", "decode_response", "schema_value", "require_projection",
                    "require_current_offer", "for_offer", "declare", "literal_at"}:
            return "public." + attr
        if attr in {"write_text", "mkdir", "require_ready"}:
            return "setup." + attr
    if name in {"lead_fixture_response", "lead_fixture_scope_record", "offered_fixture_bindings",
                "indexed_response", "_fixture_types", "_fixture_partition_matches", "findings"}:
        return "fixture." + name
    if name in {"materialize_pristine_initialized_workspace", "provision_session_resources",
                "StoryRuntime", "Workspace", "_LeadBackend", "tempfile.TemporaryDirectory"}:
        return "setup." + name
    if name == "orchestrator_module._lead_front_result_contract":
        return "binding.front_result_contract"
    return None


class Instrument(ast.NodeTransformer):
    def __init__(self, file):
        self.file = file

    def visit_Call(self, node):
        label = call_label(node)
        node = self.generic_visit(node)
        if label is None:
            return node
        replacement = ast.Call(ast.Attribute(ast.Name(ALIAS, ast.Load()), "call", ast.Load()),
            [ast.Constant(label), ast.Constant(self.file), ast.Constant(node.lineno), node.func,
             *node.args], node.keywords)
        return ast.copy_location(replacement, node)

    def visit_Attribute(self, node):
        node = self.generic_visit(node)
        if isinstance(node.ctx, ast.Load) and node.attr in {"answer_json", "canonical_bytes"}:
            return ast.copy_location(ast.Call(
                ast.Attribute(ast.Name(ALIAS, ast.Load()), "attr", ast.Load()),
                [ast.Constant("read." + node.attr), ast.Constant(self.file),
                 ast.Constant(node.lineno), node.value, ast.Constant(node.attr)], []), node)
        return node


class LoopProgress(ast.NodeTransformer):
    def visit_For(self, node):
        node = self.generic_visit(node)
        counter = ast.Expr(ast.Call(ast.Attribute(ast.Name(ALIAS, ast.Load()), "iteration", ast.Load()),
            [ast.Constant(TEST_PATH), ast.Constant(node.lineno)], []))
        node.body.insert(0, ast.copy_location(counter, node))
        return node

    visit_AsyncFor = visit_For


class Uninstrument(ast.NodeTransformer):
    def visit_Expr(self, node):
        call = node.value
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name) and call.func.value.id == ALIAS
                and call.func.attr == "iteration"):
            return None
        return self.generic_visit(node)

    def visit_Call(self, node):
        node = self.generic_visit(node)
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id == ALIAS and node.func.attr == "call":
                return ast.copy_location(ast.Call(node.args[3], node.args[4:], node.keywords), node)
            if node.func.value.id == ALIAS and node.func.attr == "attr":
                return ast.copy_location(ast.Attribute(node.args[3], node.args[4].value, ast.Load()), node)
        return node

    def visit_With(self, node):
        if len(node.items) == 1:
            context = node.items[0].context_expr
            if (isinstance(context, ast.Call) and isinstance(context.func, ast.Attribute)
                    and isinstance(context.func.value, ast.Name)
                    and context.func.value.id == ALIAS and context.func.attr == "span"):
                return [self.visit(child) for child in node.body]
        return self.generic_visit(node)


def statement_label(node, section):
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        return section + "." + ast.unparse(target)
    if isinstance(node, ast.For):
        return section + ".loop." + ast.unparse(node.target)
    if isinstance(node, ast.Return):
        return section + ".return"
    if any(isinstance(n, ast.Await) for n in ast.walk(node)):
        return section + ".lead_bootstrap_await"
    return section + "." + type(node).__name__.lower()


def bracket(node, file, section):
    # Original function declarations/docstrings stay in their native lexical scope.
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Import, ast.ImportFrom)):
        return node
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
        return node
    context = ast.Call(ast.Attribute(ast.Name(ALIAS, ast.Load()), "span", ast.Load()),
        [ast.Constant(statement_label(node, section)), ast.Constant(file), ast.Constant(node.lineno)], [])
    return ast.copy_location(ast.With([ast.withitem(context, None)], [node]), node)


def instrument_function(original, file, section):
    node = Instrument(file).visit(copy.deepcopy(original))
    if section == "whole_front":
        node = LoopProgress().visit(node)
    node.body = [bracket(child, file, section) for child in node.body]
    ast.fix_missing_locations(node)
    inverse = Uninstrument().visit(copy.deepcopy(node))
    if dump(inverse) != dump(original):
        raise RuntimeError(f"AST inverse failed for {section}")
    return node


def named(tree, kind, name):
    rows = [n for n in tree.body if isinstance(n, kind) and n.name == name]
    if len(rows) != 1:
        raise RuntimeError(f"source node identity ambiguous: {name}")
    return rows[0]


def assigned(nodes, target, line):
    rows = [n for n in nodes if n.lineno == line and isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == target for t in n.targets)]
    if len(rows) != 1:
        raise RuntimeError(f"source assignment identity ambiguous: {target}:{line}")
    return copy.deepcopy(rows[0])


def at_line(nodes, kind, line):
    rows = [n for n in nodes if n.lineno == line and isinstance(n, kind)]
    if len(rows) != 1:
        raise RuntimeError(f"source span identity ambiguous: {kind.__name__}:{line}")
    return copy.deepcopy(rows[0])


def source_plan(checkout):
    """Read/parse only; the complete method is never selected, sliced or restricted."""
    texts = {p: (checkout / p).read_text() for p in (TEST_PATH, FIXTURE_PATH)}
    for p, expected in ((TEST_PATH, TEST_SHA), (FIXTURE_PATH, FIXTURE_SHA)):
        if sha((checkout / p).read_bytes()) != expected:
            raise RuntimeError(f"frozen source mismatch: {p}")
    tree, fixture = (ast.parse(texts[p], filename=str(checkout / p)) for p in (TEST_PATH, FIXTURE_PATH))
    base = named(tree, ast.ClassDef, BASE_CLASS)
    case = named(tree, ast.ClassDef, CASE_CLASS)
    original_method = named(case, ast.AsyncFunctionDef, METHOD)
    setup = named(base, ast.FunctionDef, "setUp")
    helper = named(base, ast.FunctionDef, HELPER)
    fixture_names = ("lead_fixture_response", "offered_fixture_bindings", "indexed_response", "lead_fixture_scope_record")
    originals = {"setUp": setup, HELPER: helper, METHOD: original_method}
    compiled = {"setUp": instrument_function(setup, TEST_PATH, "setup"),
        HELPER: instrument_function(helper, TEST_PATH, "neighbors"),
        METHOD: instrument_function(original_method, TEST_PATH, "whole_front")}
    for name in fixture_names:
        original = named(fixture, ast.FunctionDef, name)
        originals[name] = original
        compiled[name] = instrument_function(original, FIXTURE_PATH, "wire." + name)
    seams = []
    for name, original in originals.items():
        p = FIXTURE_PATH if name in fixture_names else TEST_PATH
        lines = texts[p].splitlines(keepends=True)
        seams.append({"name": name, "file": p, "start_line": original.lineno,
            "end_line": original.end_lineno, "original_ast_sha256": sha(dump(original).encode()),
            "instrumented_ast_sha256": sha(dump(compiled[name]).encode()),
            "span_bytes_sha256": sha("".join(lines[original.lineno - 1:original.end_lineno]).encode()),
            "ast_inverse": True,
            "assertion_sites": sum(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr.startswith("assert") for n in ast.walk(original))})
    return {"originals": originals, "compiled": compiled, "fixture_names": fixture_names,
        "seams": seams, "complete_method_statements": [
            {"start_line": n.lineno, "end_line": n.end_lineno, "ast_sha256": sha(dump(n).encode())}
            for n in original_method.body],
        "loop_sites": [{"file": TEST_PATH, "line": n.lineno,
                        "target": ast.unparse(n.target), "iterator": ast.unparse(n.iter),
                        "meaning": "iteration entered, not case completion or pass credit"}
                       for n in ast.walk(original_method) if isinstance(n, (ast.For, ast.AsyncFor))],
        "module_asts": {TEST_PATH: tree, FIXTURE_PATH: fixture}}


def namespace_names(tree):
    names = set(dir(builtins))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(n.id for target in targets for n in ast.walk(target) if isinstance(n, ast.Name))
    return names


def dependency_review(plan):
    rows = []
    for name, node in plan["originals"].items():
        file = FIXTURE_PATH if name in plan["fixture_names"] else TEST_PATH
        available = namespace_names(plan["module_asts"][file])
        local = {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
        local.update(n.arg for n in ast.walk(node) if isinstance(n, ast.arg))
        local.update(n.name for n in ast.walk(node) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        local.update(n.name for n in ast.walk(node) if isinstance(n, ast.ExceptHandler) and n.name)
        for n in ast.walk(node):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                local.update(alias.asname or alias.name.split(".")[0] for alias in n.names)
        loads = {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        missing = sorted(loads - local - available)
        if missing:
            raise RuntimeError(f"unbound extracted dependency {name}: {missing}")
        rows.append({"function": name, "unbound": missing, "external_names": sorted(loads - local)})
    return rows


def source_check(checkout, manifest):
    if manifest.get("candidate_sha") != SOURCE or manifest.get("tree") != SOURCE_TREE:
        raise RuntimeError("source identity mismatch")
    pins = manifest.get("source_hashes")
    if not isinstance(pins, dict) or len(pins) != 412:
        raise RuntimeError("source manifest must contain exactly 412 paths")
    actual = {}
    for relative, expected in sorted(pins.items()):
        path = checkout / relative
        if path.resolve().is_relative_to(checkout) is not True or not path.is_file():
            raise RuntimeError("source path escaped frozen checkout")
        actual[relative] = sha(path.read_bytes())
        if actual[relative] != expected:
            raise RuntimeError(f"source byte mismatch: {relative}")
    return {"candidate_sha": SOURCE, "tree": SOURCE_TREE, "file_count": len(actual),
            "hashes": actual, "canonical_hashes_sha256": sha(json.dumps(actual, sort_keys=True,
                separators=(",", ":")).encode())}


def gc_observation():
    return {"enabled": gc.isenabled(), "thresholds": list(gc.get_threshold()),
        "callbacks_registry_identity": id(gc.callbacks), "callback_ids": [id(x) for x in gc.callbacks],
        "profiler_identity": id(sys.getprofile()) if sys.getprofile() is not None else None,
        "trace_identity": id(sys.gettrace()) if sys.gettrace() is not None else None}


def install_function(node, namespace, filename):
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, filename, "exec"), namespace)
    function = namespace[node.name]
    if function.__code__.co_freevars:
        raise RuntimeError(f"cloned TEST function unexpectedly has a closure: {node.name}")
    return function


def execute_slice(checkout, timer, plan, report):
    sys.path.insert(0, str(checkout / "src"))
    sys.path.insert(0, str(checkout))
    with timer.span("imports.test_fixture_and_application"):
        module = importlib.import_module("tests.test_lead_orchestration")
        fixture = importlib.import_module("tests.provider_wire_fixture")
    if Path(module.__file__).resolve() != (checkout / TEST_PATH).resolve():
        raise RuntimeError("test module came from another checkout")
    if Path(fixture.__file__).resolve() != (checkout / FIXTURE_PATH).resolve():
        raise RuntimeError("wire fixture came from another checkout")
    original_json = (json.dumps, json.loads)
    fixture_globals = dict(vars(fixture))
    test_globals = dict(vars(module))
    if ALIAS in fixture_globals or ALIAS in test_globals:
        raise RuntimeError("diagnostic alias collides with an original global")
    fixture_globals[ALIAS] = test_globals[ALIAS] = timer
    for name in plan["fixture_names"]:
        install_function(plan["compiled"][name], fixture_globals, str(checkout / FIXTURE_PATH))
    for name in plan["fixture_names"]:
        test_globals[name] = fixture_globals[name]
        timer.test_replacements.append((getattr(fixture, name), fixture_globals[name]))
    setup = install_function(plan["compiled"]["setUp"], test_globals, str(checkout / TEST_PATH))
    helper = install_function(plan["compiled"][HELPER], test_globals, str(checkout / TEST_PATH))
    selected = install_function(plan["compiled"][METHOD], test_globals, str(checkout / TEST_PATH))
    case_class = getattr(module, CASE_CLASS)
    original_class_method = getattr(case_class, METHOD)
    case = case_class(METHOD)
    names = ("setUp", HELPER, METHOD)
    if any(name in vars(case) for name in names):
        raise RuntimeError("case instance already overrides a required test seam")
    result = unittest.TestResult()
    for name, function in zip(names, (setup, helper, selected)):
        setattr(case, name, types.MethodType(function, case))
    report["slice_started"] = True
    report["test_fixture_cleanup_attempted"] = False
    try:
        with timer.span("diagnostic.complete_whole_front_with_original_unittest_lifecycle"):
            case.run(result)
    finally:
        try:
            # TestCase normally drains this stack; SIGINT may bypass that branch.
            report["test_fixture_cleanup_attempted"] = True
            with timer.span("cleanup.remaining_test_fixture_cleanups"):
                case.doCleanups()
        finally:
            for name in names:
                delattr(case, name)
            report["test_overlay_restored"] = all(name not in vars(case) for name in names)
            report["original_method_preserved"] = getattr(case_class, METHOD) is original_class_method
            report["json_callable_identity_preserved"] = (json.dumps, json.loads) == original_json
            report["test_result"] = {"tests_run": result.testsRun,
                "failures": [text for _, text in result.failures], "errors": [text for _, text in result.errors],
                "skipped": [reason for _, reason in result.skipped], "successful": result.wasSuccessful()}
            report["fixture_cleanup_stack_empty"] = not case._cleanups
            report["asyncio_runner_debug"] = getattr(case._asyncioRunner, "_debug", None)
    if result.testsRun != 1 or not result.wasSuccessful() or result.skipped:
        raise RuntimeError("complete original assertion path did not complete successfully")
    report["whole_method_completed"] = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    checkout, output = args.checkout.resolve(), args.output.resolve()
    if output.is_relative_to(checkout):
        raise RuntimeError("diagnostic outputs must be outside the frozen checkout")
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("report.json", "events.jsonl", "source-seams.json")):
        raise RuntimeError("diagnostic output already exists; no silent repetition")
    timer = Timer(output / "events.jsonl")
    report = {"schema_version": 2, "diagnostic": "whole-front-40m-v46", "complete": False,
        "status": "running", "canonical_credit": False, "performance_improvement_credit": False,
        "source_candidate": SOURCE, "source_tree": SOURCE_TREE,
        "method_id": "tests.test_lead_orchestration." + CASE_CLASS + "." + METHOD,
        "profile_restriction": None, "execution_limit_seconds": 2400, "cleanup_grace_seconds": 30,
        "whole_method_completed": False, "slice_started": False,
        "scope": "Complete original whole-front method, all prelude/scenarios/wrappers/matrix/later families unchanged; no profile or scenario restriction. Timeout is incomplete/censored, never canonical or full-fit credit.",
        "timing_semantics": {"clock_wall": "time.perf_counter", "clock_cpu": "time.process_time",
            "inclusive": "Includes descendant measured spans; do not sum nested inclusives.",
            "exclusive_observed": "Inclusive minus direct observed child spans; includes uninstrumented calls and timer overhead, not profiler self time.",
            "public_decode_response": "Actual unchanged public callable, inclusive; no internal substitute probes.",
            "answer_json_read": "Property access only; semantic normalization remains inside the inclusive public decoder.",
            "asyncio_lifecycle": "Original IsolatedAsyncioTestCase.run creates/closes its debug asyncio runner and performs original fixture cleanup.",
            "exception_spans": "Expected negative-validation exceptions remain counted; unittest assertions determine diagnostic outcome.",
            "censored": "Interrupted span duration is elapsed-to-interruption, never a complete operation duration."},
        "worker_context": {"pid": os.getpid(), "ppid": os.getppid(), "platform": platform.platform(),
            "system": platform.system(), "machine": platform.machine(), "python": platform.python_version(),
            "implementation": sys.implementation.name, "cpu_count": os.cpu_count(),
            "affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
            "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "workflow_carrier_sha": os.environ.get("GITHUB_SHA"), "job": os.environ.get("GITHUB_JOB")},
        "errors": []}
    handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def interrupted(signum, frame):
        timer.signals.append(signum)
        if len(timer.signals) == 1:
            timer.censored_at_interrupt = [dict(row, censored=True) for row in timer.active()]
        if len(timer.signals) == 1:
            raise KeyboardInterrupt(f"hosted diagnostic interrupted by signal {signum}")

    for sig in handlers:
        signal.signal(sig, interrupted)
    exit_code = 1
    try:
        host = report["worker_context"]
        if not (host["system"] == "Linux" and host["machine"] == "aarch64"
                and host["implementation"] == "cpython" and sys.version_info[:2] == (3, 11)
                and host["cpu_count"] == 4 and len(host["affinity"] or []) == 4):
            raise RuntimeError("diagnostic requires hosted Linux ARM64 CPython 3.11 with four available CPUs")
        manifest_raw = args.source_manifest.read_bytes()
        manifest = json.loads(manifest_raw)
        report["source_manifest_sha256"] = sha(manifest_raw)
        with timer.span("source.before_412"):
            report["source_before"] = source_check(checkout, manifest)
        report["gc_before"] = gc_observation()
        if not (report["gc_before"]["enabled"] and report["gc_before"]["thresholds"] == [700, 10, 10]
                and not report["gc_before"]["callback_ids"] and report["gc_before"]["profiler_identity"] is None
                and report["gc_before"]["trace_identity"] is None):
            raise RuntimeError("default unprofiled GC/process observation precondition failed")
        with timer.span("instrumentation.source_extract_compile"):
            plan = source_plan(checkout)
            dependencies = dependency_review(plan)
            write_json(output / "source-seams.json", {"source": SOURCE, "test_sha256": TEST_SHA,
                "fixture_sha256": FIXTURE_SHA, "functions": plan["seams"], "dependencies": dependencies,
                "complete_method_statements": plan["complete_method_statements"],
                "loop_sites": plan["loop_sites"],
                "generator_restriction": None, "omitted_original_statements": [],
                "original_agent_context_invoke_wrapper": "Retained exactly, with the complete method AST.",
                "production_monkeypatches_added": [],
                "original_setup_fixture_patch": "Unchanged setUp _provider_execution_workspace_manager patch is retained and cleaned by unittest."})
        execute_slice(checkout, timer, plan, report)
        exit_code = 0
    except BaseException as error:
        report["errors"].append({"type": type(error).__name__, "message": str(error),
                                  "traceback": traceback.format_exc()})
        exit_code = 130 if isinstance(error, KeyboardInterrupt) else 1
    finally:
        try:
            if "source_before" in report:
                with timer.span("source.after_412"):
                    report["source_after"] = source_check(checkout, manifest)
                report["source_unchanged"] = report["source_before"] == report["source_after"]
            report["gc_after"] = gc_observation()
            report["gc_unchanged"] = report.get("gc_before") == report["gc_after"]
            if not report.get("source_unchanged") or not report["gc_unchanged"]:
                exit_code = exit_code or 1
                report["errors"].append({"type": "FinalObservationMismatch", "message": "source or default GC/process state changed"})
        except BaseException as error:
            exit_code = exit_code or 1
            report["errors"].append({"type": type(error).__name__, "message": str(error), "stage": "final_observation"})
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        report["handlers_restored"] = all(signal.getsignal(sig) is handler for sig, handler in handlers.items())
        report["signals"] = timer.signals
        report["censored_at_interrupt"] = timer.censored_at_interrupt
        report["loop_iterations_entered"] = dict(timer.loop_entries)
        report["trace_policy"] = {"complete_raw_trace": False, "raw_prefix_span_ids_through": 128,
            "additional_events": "original top-level method statement boundaries",
            "checkpoint_interval_seconds": 5, "checkpoint_trigger": "observed boundaries and finalization",
            "recent_event_ring_capacity": 32, "summary_site_limit": 4096, "active_span_limit": 128,
            "omitted_raw_events": timer.omitted_raw_events,
            "all_site_aggregates_retained": True, "loop_counts_are_entries_not_completed_cases": True}
        report["event_overflow"] = timer.overflow
        report["active_spans"] = [{"span": s.id, "label": s.label, "file": s.file, "line": s.line,
                                    "censored": True} for s in timer.stack]
        report["elapsed_wall_seconds"] = time.perf_counter() - timer.started_wall
        report["elapsed_cpu_seconds"] = time.process_time() - timer.started_cpu
        report["summaries"] = list(timer.summaries.values())
        report["top_level_spans"] = timer.top_level_spans
        completed_sites = [row for row in timer.summaries.values() if not row["outcomes"].get("censored")]
        report["largest_completed_exclusive_sites"] = sorted(completed_sites,
            key=lambda row: (-row["exclusive_observed_wall_seconds"], row["file"], row["line"]))[:20]
        report["largest_completed_inclusive_sites_do_not_sum"] = sorted(completed_sites,
            key=lambda row: (-row["inclusive_wall_seconds"], row["file"], row["line"]))[:20]
        report["event_count"] = timer.next_id
        report["complete"] = (exit_code == 0 and report["whole_method_completed"]
            and not timer.stack and not timer.overflow and report["handlers_restored"]
            and report.get("test_overlay_restored") and report.get("fixture_cleanup_stack_empty")
            and report.get("asyncio_runner_debug") is True
            and report.get("json_callable_identity_preserved") and report.get("original_method_preserved"))
        if not report["complete"] and exit_code == 0:
            exit_code = 1
        report["status"] = "completed_whole_front_diagnostic" if report["complete"] else "incomplete_whole_front_diagnostic"
        report["exit_code"] = exit_code
        timer.close()
        report["observed_event_count"] = timer.observed_event_count
        report["stream_record_count"] = timer.stream_record_count
        report["events_bytes"] = timer.bytes_written
        report["checkpoint_count"] = timer.checkpoint_count
        report["events_sha256"] = sha((output / "events.jsonl").read_bytes())
        write_json(output / "report.json", report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
