"""One source-derived whole-front profile; hosted diagnostic, never canonical credit.

Only TEST functions are cloned. The original application and JSON callables are
invoked unchanged. Run this file only in the root-authorized hosted carrier.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import copy
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
PROFILE = {"intent": "authoring", "activity": "workflow", "domains": [], "roles": []}
MAX_EVENTS_BYTES = 16 * 1024 * 1024
MAX_REPORT_BYTES = 16 * 1024 * 1024
ALIAS = "__negative_decode_timer_v48__"



# Observation only: production source, functions, defaults and identities are untouched.
STAGE_LINES = (
    (787, 787, "current_offer_guard"), (788, 794, "first_strict_parse"),
    (795, 800, "envelope_shape"), (801, 804, "action_index_selection"),
    (805, 820, "response_utf8_and_pair"), (821, 821, "member_token_full_validation_rescan"),
    (822, 827, "member_utf8_limit"), (828, 833, "outer_schema_findings"),
    (834, 835, "inner_index_selection"), (836, 843, "inner_schema_findings"),
    (844, 844, "structure_return"), (855, 857, "structure_dispatch"),
    (858, 862, "outer_selected_typed_checks"), (863, 867, "inner_selected_typed_checks"),
    (868, 872, "decoded_result_construction"),
)
WATCH = {
    "story_writing_agents.agent_context.service": (
        "BoundCallContract.decode_response", "BoundCallContract._validate_response_structure",
        "BoundCallContract.require_current_offer"),
    "story_writing_agents.lead_provider_contracts": (
        "LeadContractIndex.require_provenance", "LeadContractIndex.select",
        "LeadContractIndex.decode_and_validate", "LeadContractIndex.schema_value", "LeadStagePolicy.identity_for"),
    "story_writing_agents.structured_output_validation": (
        "BoundContract.require_owner_provenance", "BoundContract.decode_selected",
        "BoundContract.schema_value", "BoundContract.require_host_checks",
        "TypedDecoder.require_provenance", "NamedHostCheck.require_provenance",
        "SchemaRules.schema_value", "SchemaRules.findings", "SchemaRules.literal_at",
        "strict_json_value", "strict_json_object", "strict_json_member_text",
        "bound_schema_node_fingerprint", "_hook_guard_matches", "_capture_hook_layout",
        "_schema_rule_template_query", "_schema_template_findings", "_diagnostic_context", "diagnose_value"),
    "json": ("dumps", "loads"),
}

class DecoderStages:
    """Bounded original-code observation for the existing negative decoder calls."""
    def __init__(self, mode, output):
        self.mode, self.output = mode, output
        self.cases, self.functions, self.codes, self.trace_codes = [], [], {}, set()
        self.active = None
        self.raw_bytes = 0
        self.corpus = (output / "negative-corpus.jsonl").open("x", encoding="utf-8")
        self.records = (output / "negative-cases.jsonl").open("x", encoding="utf-8")
        self.record_bytes = 0

    def register(self):
        if self.functions:
            return
        for module_name, names in WATCH.items():
            module = sys.modules[module_name]
            for name in names:
                function = module
                for part in name.split("."):
                    function = getattr(function, part)
                code = function.__code__
                self.functions.append((module_name, name, function, code))
                if code in self.codes:
                    raise RuntimeError("ambiguous observed production code")
                self.codes[code] = module_name + "." + name
                if name in {"BoundCallContract.decode_response", "BoundCallContract._validate_response_structure"}:
                    self.trace_codes.add(code)

    def write_record(self, stream, row, counter):
        raw = json.dumps(row, separators=(",", ":"), ensure_ascii=True, allow_nan=False) + "\n"
        size = len(raw.encode())
        if getattr(self, counter) + size > MAX_REPORT_BYTES:
            raise RuntimeError("bounded decoder observation exceeds16MiB")
        stream.write(raw)
        stream.flush()
        setattr(self, counter, getattr(self, counter) + size)

    def mutation(self):
        frame = sys._getframe(1)
        findings_line = None
        while frame:
            if frame.f_code.co_name == "findings" and frame.f_code.co_filename.endswith(TEST_PATH):
                findings_line = frame.f_lineno
            if frame.f_code.co_name == HELPER and frame.f_code.co_filename.endswith(TEST_PATH):
                values = frame.f_locals
                return {"helper_line": frame.f_lineno, "decoder_line": findings_line,
                        "path": list(values.get("path", ())), "key": values.get("key"),
                        "replacement_type": type(values.get("replacement")).__name__,
                        "projection": values.get("projection", False)}
            frame = frame.f_back
        raise RuntimeError("negative decoder is outside original helper")

    def tick(self, stage, wall=None, cpu=None):
        c = self.active
        wall = time.perf_counter() if wall is None else wall
        cpu = time.process_time() if cpu is None else cpu
        row = c["stages"].setdefault(c["stage"], {"wall_seconds": 0.0, "cpu_seconds": 0.0, "segments": 0})
        row["wall_seconds"] += wall - c["stamp_wall"]
        row["cpu_seconds"] += cpu - c["stamp_cpu"]
        row["segments"] += 1
        c["stage"], c["stamp_wall"], c["stamp_cpu"] = stage, wall, cpu

    def profile(self, frame, event, arg):
        started_wall, started_cpu = time.perf_counter(), time.process_time()
        try:
            c = self.active
            c["profile_callback_count"] += 1
            if frame.f_code not in self.codes or event not in {"call", "return"}:
                return
            if event == "call":
                parent = c["stack"][-1][1] if c["stack"] else None
                c["stack"].append([frame, self.codes[frame.f_code], parent, c["stage"],
                                   started_wall, started_cpu, 0.0, 0.0])
            else:
                if not c["stack"] or c["stack"][-1][0] is not frame:
                    raise RuntimeError("observed function stack mismatch")
                _, name, parent, stage, wall, cpu, child_wall, child_cpu = c["stack"].pop()
                wall, cpu = started_wall - wall, started_cpu - cpu
                if c["stack"]:
                    c["stack"][-1][6] += wall
                    c["stack"][-1][7] += cpu
                key = (stage, parent, name)
                if key not in c["functions"] and len(c["functions"]) >= 256:
                    raise RuntimeError("bounded function aggregates exceeded")
                row = c["functions"].setdefault(key, {"stage_at_entry": stage, "parent_observed_function": parent,
                    "function": name, "count": 0, "inclusive_wall_seconds": 0.0, "inclusive_cpu_seconds": 0.0,
                    "exclusive_observed_wall_seconds": 0.0, "exclusive_observed_cpu_seconds": 0.0})
                row["count"] += 1
                row["inclusive_wall_seconds"] += wall
                row["inclusive_cpu_seconds"] += cpu
                row["exclusive_observed_wall_seconds"] += max(0.0, wall - child_wall)
                row["exclusive_observed_cpu_seconds"] += max(0.0, cpu - child_cpu)
        finally:
            self.active["observer_callback_wall_seconds"] += time.perf_counter() - started_wall
            self.active["observer_callback_cpu_seconds"] += time.process_time() - started_cpu

    def trace(self, frame, event, arg):
        if frame.f_code not in self.trace_codes:
            return None
        started_wall, started_cpu = time.perf_counter(), time.process_time()
        try:
            c = self.active
            c["trace_callback_count"] += 1
            if event == "line":
                for lo, hi, stage in STAGE_LINES:
                    if lo <= frame.f_lineno <= hi:
                        if stage != c["stage"]:
                            self.tick(stage, started_wall, started_cpu)
                        break
            elif event == "exception" and len(c["exception_events"]) < 8:
                kind, failure, _ = arg
                c["exception_events"].append({"function": self.codes[frame.f_code], "line": frame.f_lineno,
                    "stage": c["stage"], "type": kind.__name__, "code": getattr(failure, "code", None)})
            return self.trace
        finally:
            self.active["observer_callback_wall_seconds"] += time.perf_counter() - started_wall
            self.active["observer_callback_cpu_seconds"] += time.process_time() - started_cpu

    def invoke(self, function, args, kwargs):
        self.register()
        if self.active is not None or len(self.cases) >= 67:
            raise RuntimeError("negative corpus count/recursion changed")
        if len(args) != 1 or kwargs or type(args[0]) is not str:
            raise RuntimeError("negative decoder call shape changed")
        old_profile, old_trace = sys.getprofile(), sys.gettrace()
        if old_profile is not None or old_trace is not None:
            raise RuntimeError("observation requires no prior profiler/trace")
        raw = args[0].encode("utf-8", "strict")
        row = {"ordinal": len(self.cases) + 1, "mode": self.mode,
               "mutation": self.mutation(), "raw_sha256": sha(raw), "raw_utf8_bytes": len(raw)}
        if row["mutation"]["projection"]:
            raise RuntimeError("provider-only expansion unexpectedly invoked typed decoder")
        self.write_record(self.corpus, {**row, "raw_text": args[0]}, "raw_bytes")
        start_wall, start_cpu = time.perf_counter(), time.process_time()
        c = {"stage": "observation_boundary", "stamp_wall": start_wall, "stamp_cpu": start_cpu,
             "stages": {}, "stack": [], "functions": {}, "exception_events": [],
             "profile_callback_count": 0, "trace_callback_count": 0,
             "observer_callback_wall_seconds": 0.0, "observer_callback_cpu_seconds": 0.0}
        self.active = c
        try:
            if self.mode == "profiled":
                sys.setprofile(self.profile)
                sys.settrace(self.trace)
            value = function(*args, **kwargs)
            row["outcome"] = "returned_unexpectedly"
            return value
        except BaseException as failure:
            row.update(outcome="censored" if isinstance(failure, (KeyboardInterrupt, SystemExit)) else "raised_to_original_assertion",
                       exception_type=type(failure).__name__, rejection_code=getattr(failure, "code", None),
                       requirements=list(getattr(failure, "requirements", ())))
            raise
        finally:
            sys.settrace(old_trace)
            sys.setprofile(old_profile)
            end_wall, end_cpu = time.perf_counter(), time.process_time()
            self.tick("finished", end_wall, end_cpu)
            row.update(elapsed_wall_seconds=end_wall - start_wall, elapsed_cpu_seconds=end_cpu - start_cpu,
                       stage_totals=c["stages"] if self.mode == "profiled" else None,
                       function_totals=list(c["functions"].values()) if self.mode == "profiled" else None,
                       first_observed_exception=c["exception_events"][0] if c["exception_events"] else None,
                       observed_exceptions=c["exception_events"],
                       profile_callback_count=c["profile_callback_count"], trace_callback_count=c["trace_callback_count"],
                       observer_callback_wall_seconds=c["observer_callback_wall_seconds"],
                       observer_callback_cpu_seconds=c["observer_callback_cpu_seconds"],
                       profiler_restored=sys.getprofile() is old_profile, trace_restored=sys.gettrace() is old_trace,
                       observed_stack_empty=not c["stack"])
            self.active = None
            self.cases.append(row)
            self.write_record(self.records, row, "record_bytes")

    def finish(self):
        self.corpus.close()
        self.records.close()
        identity_preserved = all(function.__code__ is code for _, _, function, code in self.functions)
        complete = (len(self.cases) == 67 and identity_preserved and self.active is None
                    and all(row["outcome"] == "raised_to_original_assertion" and row["rejection_code"]
                            and row["profiler_restored"] and row["trace_restored"] and row["observed_stack_empty"]
                            for row in self.cases))
        result = {"schema_version": 1, "diagnostic": "negative-decode-stages-v48", "mode": self.mode,
                  "source": SOURCE, "profile": PROFILE, "family": "clarification_question",
                  "complete": complete, "negative_case_count": len(self.cases), "expected_negative_cases": 67,
                  "production_code_identities_preserved": identity_preserved,
                  "watch": [{"function": module + "." + name, "filename": code.co_filename,
                             "first_line": code.co_firstlineno} for module, name, _, code in self.functions],
                  "cases": self.cases,
                  "corpus_sha256": sha((self.output / "negative-corpus.jsonl").read_bytes()),
                  "case_records_sha256": sha((self.output / "negative-cases.jsonl").read_bytes()),
                  "semantics": {"stages": "Disjoint observed elapsed line regions for original structural validator and decoder; profiled only.",
                     "functions": "Nested inclusive plus observed-exclusive original-code call aggregates; do not add overlapping inclusives to stage totals.",
                     "first_rejection": "First exception observed in either original public decoder frame; final public code recorded separately.",
                     "overhead": "Python profile/trace dispatch can disproportionately inflate Python-heavy work. Callback-self time is a lower bound; do not scale stage proportions onto ordinary or prior run times.",
                     "ordinary": "Original public negative calls timed without internal observer; same complete fixture/assertions run in its own fresh process.",
                     "cross_context": "Raw corpus bytes/hashes retained separately. Fresh offers may differ; no blanket ID normalization or raw-byte equivalence claim."}}
        write_json(self.output / "stage-report.json", result)
        return {"complete": complete, "negative_case_count": len(self.cases), "mode": self.mode,
                "stage_report_sha256": sha((self.output / "stage-report.json").read_bytes())}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(node):
    return ast.dump(node, include_attributes=False)


def write_json(path, value):
    raw = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    if len(raw) > MAX_REPORT_BYTES:
        raise RuntimeError("diagnostic JSON exceeds the frozen artifact bound")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(raw)
    temporary.replace(path)


class Timer:
    """Inclusive spans and exclusive-of-observed-child spans, not profiler self time."""

    def __init__(self, path):
        self.stream = path.open("x", encoding="utf-8")
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

    def emit(self, row):
        raw = json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n"
        size = len(raw.encode("utf-8"))
        if self.bytes_written + size > MAX_EVENTS_BYTES:
            self.overflow = True
            raise RuntimeError("ordered timing events exceed 16 MiB; diagnostic incomplete")
        self.stream.write(raw)
        self.stream.flush()
        self.bytes_written += size

    def span(self, label, file="harness", line=0, detail=None):
        return Span(self, label, file, line, detail)

    def call(self, label, file, line, function, /, *args, **kwargs):
        detail = None
        if label == "fixture.field_neighbors":
            detail = {"projection": kwargs.get("projection", False)}
        with self.span(label, file, line, detail):
            if label == "public.decode_response" and file == TEST_PATH and line == 1179:
                return self.decoder_observer.invoke(function, args, kwargs)
            return function(*args, **kwargs)

    def attr(self, label, file, line, value, name):
        with self.span(label, file, line):
            return getattr(value, name)

    def close(self):
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
        t.emit({"event": "begin", "span": self.id, "parent": self.parent,
                "label": self.label, "file": self.file, "line": self.line,
                "wall_elapsed": self.wall - t.started_wall,
                "cpu_elapsed": self.cpu - t.started_cpu, "detail": self.detail})
        t.stack.append(self)
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
        row = t.summaries.setdefault(key, {"label": self.label, "file": self.file,
            "line": self.line, "count": 0, "outcomes": {}, "inclusive_wall_seconds": 0.0,
            "inclusive_cpu_seconds": 0.0, "exclusive_observed_wall_seconds": 0.0,
            "exclusive_observed_cpu_seconds": 0.0, "min_wall_seconds": wall,
            "max_wall_seconds": wall, "min_cpu_seconds": cpu, "max_cpu_seconds": cpu})
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


class Uninstrument(ast.NodeTransformer):
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
    """Read/parse only. This function is also used by the owner's static review."""
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
    nodes = original_method.body
    chosen = [copy.deepcopy(nodes[0]), copy.deepcopy(nodes[1])]
    bootstrap = at_line(nodes, ast.With, 1730)
    if len(bootstrap.body) != 1 or not any(isinstance(n, ast.Await) for n in ast.walk(bootstrap.body[0])):
        raise RuntimeError("bootstrap source seam changed")
    chosen += [copy.deepcopy(bootstrap.body[0]), at_line(nodes, ast.Expr, 1732)]
    chosen += [assigned(nodes, "call", 1850), at_line(nodes, ast.ImportFrom, 1871),
               assigned(nodes, "task", 1877), at_line(nodes, ast.ImportFrom, 3062),
               at_line(nodes, ast.ImportFrom, 3063)]
    for name, line in (("factory", 3070), ("declared", 3071), ("profile_record", 3143),
            ("profile_scope", 3147), ("profile_facts", 3148), ("workflow_record", 3259),
            ("workflow_value", 3261), ("workflow_facts", 3262)):
        chosen.append(assigned(nodes, name, line))
    chosen.append(at_line(nodes, ast.ImportFrom, 3314))
    for name, line in (("free_facts", 3328), ("free_seed", 3329), ("profile_binding", 3331),
            ("profile_schema", 3332), ("profile_projections", 3333), ("profile_projection_rules", 3335),
            ("profile_projections", 3337), ("stem", 3339), ("accepted_profiles", 3449)):
        chosen.append(assigned(nodes, name, line))
    outer = at_line(nodes, ast.For, 3450)
    inner = outer
    for _ in range(3):
        if len(inner.body) != 1 or not isinstance(inner.body[0], ast.For):
            raise RuntimeError("profile matrix nesting changed")
        inner = inner.body[0]
    packet_nodes = []
    for node in inner.body:
        packet_nodes.append(copy.deepcopy(node))
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == HELPER):
            break
    else:
        raise RuntimeError("clarification packet boundary absent")
    if packet_nodes[-1].lineno != 3481:
        raise RuntimeError("clarification packet source boundary drift")
    # One original innermost body, with its original legality branch and continue.
    single_for = ast.For(
        target=ast.Tuple([ast.Name(n, ast.Store()) for n in ("intent", "activity", "domains", "roles")], ast.Store()),
        iter=ast.Tuple([ast.Tuple([ast.Constant("authoring"), ast.Constant("workflow"),
                                  ast.Tuple([], ast.Load()), ast.Tuple([], ast.Load())], ast.Load())], ast.Load()),
        body=packet_nodes, orelse=[])
    ast.copy_location(single_for, outer)
    chosen.append(single_for)
    fn = ast.AsyncFunctionDef(name="_single_profile", args=copy.deepcopy(original_method.args),
        body=chosen, decorator_list=[], returns=copy.deepcopy(original_method.returns), type_comment=None)
    ast.copy_location(fn, original_method)
    ast.fix_missing_locations(fn)
    transformed = instrument_function(fn, TEST_PATH, "profile")
    # Bracket each selected original packet statement, not later profile families.
    loop = next(n for n in ast.walk(transformed) if isinstance(n, ast.For))
    loop.body = [bracket(n, TEST_PATH, "packet") for n in loop.body]
    ast.fix_missing_locations(transformed)
    if dump(Uninstrument().visit(copy.deepcopy(transformed))) != dump(fn):
        raise RuntimeError("selected packet inverse failed")
    fixture_names = ("lead_fixture_response", "offered_fixture_bindings", "indexed_response", "lead_fixture_scope_record")
    originals = {"setUp": setup, HELPER: helper, "_single_profile": fn}
    compiled = {"setUp": instrument_function(setup, TEST_PATH, "setup"),
                HELPER: instrument_function(helper, TEST_PATH, "neighbors"), "_single_profile": transformed}
    for name in fixture_names:
        original = named(fixture, ast.FunctionDef, name)
        originals[name] = original
        compiled[name] = instrument_function(original, FIXTURE_PATH, "wire." + name)
    seams = []
    for name, original in originals.items():
        p = FIXTURE_PATH if name in fixture_names else TEST_PATH
        source_lines = texts[p].splitlines(keepends=True)
        seams.append({"name": name, "file": p, "start_line": original.lineno,
            "end_line": original.end_lineno, "original_ast_sha256": sha(dump(original).encode()),
            "instrumented_ast_sha256": sha(dump(compiled[name]).encode()),
            "span_bytes_sha256": sha("".join(source_lines[original.lineno - 1:original.end_lineno]).encode()),
            "ast_inverse": True,
            "assertion_sites": sum(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr.startswith("assert") for n in ast.walk(original))})
    return {"originals": originals, "compiled": compiled, "fixture_names": fixture_names,
        "seams": seams, "selected_original_spans": [
            {"start_line": n.lineno, "end_line": n.end_lineno, "ast_sha256": sha(dump(n).encode())}
            for n in chosen[:-1]],
        "packet_original_spans": [{"start_line": n.lineno, "end_line": n.end_lineno,
                                    "ast_sha256": sha(dump(n).encode())} for n in packet_nodes],
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
        local.update(n.name for n in ast.walk(node) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
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
    setup = install_function(plan["compiled"]["setUp"], test_globals, str(checkout / TEST_PATH))
    helper = install_function(plan["compiled"][HELPER], test_globals, str(checkout / TEST_PATH))
    selected = install_function(plan["compiled"]["_single_profile"], test_globals, str(checkout / TEST_PATH))
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
        with timer.span("diagnostic.selected_profile_with_original_unittest_lifecycle"):
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
        raise RuntimeError("selected original assertion path did not complete successfully")
    report["selected_profile_completed"] = True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("ordinary", "profiled"))
    args = parser.parse_args(argv)
    checkout, output = args.checkout.resolve(), args.output.resolve()
    if output.is_relative_to(checkout):
        raise RuntimeError("diagnostic outputs must be outside the frozen checkout")
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("report.json", "events.jsonl", "source-seams.json")):
        raise RuntimeError("diagnostic output already exists; no silent repetition")
    timer = Timer(output / "events.jsonl")
    timer.decoder_observer = DecoderStages(args.mode, output)
    report = {"schema_version": 1, "diagnostic": "negative-decode-stages-v48", "complete": False,
        "status": "running", "canonical_credit": False, "performance_improvement_credit": False,
        "source_candidate": SOURCE, "source_tree": SOURCE_TREE, "selected_profile": PROFILE,
        "selected_profile_count": 1, "selected_packet_family": "clarification_question",
        "selected_profile_completed": False, "slice_started": False,
        "scope": "One original clarification packet plus its complete existing field-neighbor/provider expansion; later family variants and full whole-front method are not executed.",
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
                "selected_original_spans": plan["selected_original_spans"],
                "packet_original_spans": plan["packet_original_spans"],
                "generator_restriction": "Exactly one literal authoring/workflow/()/() iteration; original inner body through first neighbor call retained.",
                "omitted_wrapper": "Unrelated original AgentContextService.invoke capture wrapper is omitted; original lead_turn await retained.",
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
        report["decoder_measurement"] = timer.decoder_observer.finish()
        if not report["decoder_measurement"]["complete"]:
            exit_code = exit_code or 1
        report["event_count"] = timer.next_id
        report["events_bytes"] = timer.bytes_written
        report["complete"] = (exit_code == 0 and report["selected_profile_completed"]
            and not timer.stack and not timer.overflow and report["handlers_restored"]
            and report.get("test_overlay_restored") and report.get("fixture_cleanup_stack_empty")
            and report.get("asyncio_runner_debug") is True
            and report.get("json_callable_identity_preserved") and report.get("original_method_preserved"))
        if not report["complete"] and exit_code == 0:
            exit_code = 1
        report["status"] = "completed_diagnostic_slice" if report["complete"] else "incomplete_diagnostic_slice"
        report["exit_code"] = exit_code
        timer.close()
        report["events_sha256"] = sha((output / "events.jsonl").read_bytes())
        write_json(output / "report.json", report)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
