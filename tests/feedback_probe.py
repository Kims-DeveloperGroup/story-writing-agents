"""CI-only RED/restoration/GREEN replay of an unchanged selected case."""
import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CASE = "tests.test_selected_task_material_codec.SelectedTaskMaterialCodecTests.test_two_captures_keep_closed_wire_shape_and_order"
SOURCE_HASH = "a9696b95cd08e9b6bd4eacf89bcd036738c4bf8823c3d8a313b79791d5c258a3"


SOURCE_WITNESSES = (
    ("tests/test_agent_context_sources.py",
     "src/story_writing_agents/agent_context/sources.py",
     "tests.test_agent_context_sources.SourceSurfacePolicyAndValueTests.test_descriptor_snapshot_records_are_exact_immutable_and_content_hidden"),
    ("tests/agent_context/sources/test_source_contracts.py",
     "src/story_writing_agents/agent_context/sources/capture_values.py",
     "tests.agent_context.sources.test_source_contracts.SourceSurfacePolicyAndValueTests.test_descriptor_snapshot_records_are_exact_immutable_and_content_hidden"),
)

def production_fault(root, digest, witness):
    witnesses = SOURCE_WITNESSES if witness == "source-snapshot" else ACCOUNTING_WITNESSES
    layouts = [row for row in witnesses if (root / row[0]).is_file() and (root / row[1]).is_file()]
    if witness == "provider-call-ordinal":
        layouts = [row for row in layouts if any(isinstance(node, ast.ClassDef)
                   and node.name == row[3]
                   for node in ast.parse((root / row[1]).read_bytes()).body)]
    if len(layouts) != 1:
        raise RuntimeError("source witness differs the two approved owner layouts")
    case_path, source_path, case = layouts[0][:3]
    source = root / source_path
    original = source.read_bytes()
    for path in (case_path, source_path):
        committed = subprocess.check_output(["git", "show", "HEAD:" + path], cwd=root)
        if (root / path).read_bytes() != committed:
            raise RuntimeError("source witness differs its exact HEAD blob")
    if witness == "source-snapshot":
        targets = [node for owner in ast.parse(original).body
                   if isinstance(owner, ast.ClassDef) and owner.name == "ContextSourceSnapshot"
                   for node in owner.body if isinstance(node, ast.AnnAssign)
                   and isinstance(node.target, ast.Name) and node.target.id == "content"]
        if len(targets) != 1 or not isinstance(targets[0].value, ast.Call):
            raise RuntimeError("source witness content field is not unique")
        arguments = [item.value for item in targets[0].value.keywords if item.arg == "repr"]
        if len(arguments) != 1 or not isinstance(arguments[0], ast.Constant) or arguments[0].value is not False:
            raise RuntimeError("source witness repr guard differs approved field")
        target = arguments[0]
        expected, replacement = b"False", b"True"
    else:
        classes = [node for node in ast.parse(original).body
                   if isinstance(node, ast.ClassDef)
                   and node.name == layouts[0][3]]
        methods = [node for owner in classes for node in owner.body
                   if isinstance(node, ast.FunctionDef) and node.name == "_prepare"]
        assignments = [node for method in methods for node in method.body
                       if isinstance(node, ast.Assign) and len(node.targets) == 1
                       and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "ordinal"]
        if len(classes) != 1 or len(methods) != 1 or len(assignments) != 1:
            raise RuntimeError("provider-call ordinal assignment is not unique")
        value = assignments[0].value
        if (not isinstance(value, ast.BinOp) or not isinstance(value.op, ast.Add)
                or ast.dump(value.left) != ast.dump(ast.parse("len(self._ordinals)", mode="eval").body)):
            raise RuntimeError("provider-call ordinal expression differs")
        target = value.right
        if not isinstance(target, ast.Constant) or type(target.value) is not int or target.value != 1:
            raise RuntimeError("provider-call ordinal token differs")
        expected, replacement = b"1", b"0"
    lines = original.splitlines(keepends=True)
    start = sum(map(len, lines[:target.lineno - 1])) + target.col_offset
    end = sum(map(len, lines[:target.end_lineno - 1])) + target.end_col_offset
    if original[start:end] != expected:
        raise RuntimeError("source witness repr token differs approved field")
    return source, original, original[:start] + replacement + original[end:], case, digest((root / case_path).read_bytes())

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", required=True)
    parser.add_argument("--repository-root")
    parser.add_argument("--witness", choices=("codec", "source-snapshot", "provider-call-ordinal"), default="codec")
    args = parser.parse_args()
    output = Path(args.json).resolve()
    root = Path(args.repository_root).resolve() if args.repository_root else Path(__file__).resolve().parents[1]
    digest = lambda data: hashlib.sha256(data).hexdigest()
    if os.environ.get("GITHUB_ACTIONS") != "true" or Path.cwd().resolve() != root:
        raise RuntimeError("feedback replay requires the disposable CI checkout")
    if args.witness != "codec":
        source, original, temporary, case, case_hash = production_fault(root, digest, args.witness)
    else:
        case = CASE
        source = root / "tests/test_selected_task_material_codec.py"
        original = source.read_bytes()
        case_hash = SOURCE_HASH
        if digest(original) != SOURCE_HASH or original.count(b"self.assertEqual(actual, expected)") != 1:
            raise RuntimeError("feedback source differs from the exact retained case")
        temporary = original.replace(b"self.assertEqual(actual, expected)", b"self.assertEqual(actual, expected[:1])")
    summary = {"canonical_id": case, "witness": args.witness, "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
               "controller_sha256": digest(Path(__file__).read_bytes()),
               "original_sha256": digest(original), "case_source_sha256": case_hash, "temporary_sha256": digest(temporary), "runs": []}
    try:
        for phase, content, code, status in (("red", temporary, 1, "failed"), ("green", original, 0, "passed")):
            source.write_bytes(content)
            report = output.with_name(output.stem + "." + phase + ".json")
            log = report.with_suffix(".log")
            command = [sys.executable, "-m", "tests.runner", "run", "--id", case, "--json", str(report)]
            started = time.perf_counter()
            with log.open("w", encoding="utf-8") as stream:
                result = subprocess.run(command, cwd=root, env={**os.environ, "PYTHONPATH": "src"},
                                        stdout=stream, stderr=subprocess.STDOUT)
            row = {"phase": phase, "command": command, "exit_code": result.returncode,
                   "command_seconds": time.perf_counter() - started, "report": str(report), "log": str(log),
                   "source_sha256": digest(source.read_bytes())}
            summary["runs"].append(row)
            payload = json.loads(report.read_text(encoding="utf-8"))
            row["status"] = payload["tests"][0]["status"] if payload.get("tests") else "missing"
            expected_failures = int(phase == "red") * (2 if args.witness == "provider-call-ordinal" else 1)
            if (result.returncode != code or row["status"] != status
                    or payload["selection_manifest"]["ordered_ids"] != [case]
                    or payload["totals"]["completed"] != 1 or payload["totals"]["errors"] != 0
                    or payload["totals"]["failures"] != expected_failures
                    or payload["source_binding"]["source_valid"] is not True
                    or row["source_sha256"] != digest(content)):
                raise RuntimeError("feedback replay did not observe the exact expected case result")
            if args.witness == "provider-call-ordinal" and phase == "red":
                failures = payload["details"]
                if (len(failures) != 2 or payload["totals"]["skipped"] != 0
                        or any(detail["kind"] != "failure" or detail["canonical_id"] != case for detail in failures)
                        or "test_failure_closure_write_error_keeps_history_gated" not in failures[0]["traceback"]
                        or "self.assertEqual(len(self.backend.calls), 1)" not in failures[0]["traceback"]
                        or "AssertionError: 0 != 1" not in failures[0]["traceback"]
                        or "require_fixture_valid" not in failures[1]["traceback"]
                        or "tests.provider_wire_fixture.FixturePhasePlanError: fixture-phase-plan:extra_plan" not in failures[1]["traceback"]):
                    raise RuntimeError("provider-call ordinal fault did not reach the two existing failures")
        summary["outcome"] = "success"
    finally:
        source.write_bytes(original)
        summary["restored_sha256"] = digest(source.read_bytes())
        output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        if summary["restored_sha256"] != (SOURCE_HASH if args.witness == "codec" else digest(original)):
            raise RuntimeError("feedback source restoration failed")
    return 0


ACCOUNTING_WITNESSES = (
    ("tests/test_provider_timeout_recovery.py",
     "src/story_writing_agents/orchestrator.py",
     "tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_failure_closure_write_error_keeps_history_gated",
     "_AccountingContextProvider"),
    ("tests/test_provider_timeout_recovery.py",
     "src/story_writing_agents/runtime_resources/provider_call_accounting.py",
     "tests.test_provider_timeout_recovery.ProviderTimeoutRecoveryTests.test_failure_closure_write_error_keeps_history_gated",
     "AccountingContextProvider"),
)
if __name__ == "__main__":
    raise SystemExit(main())
