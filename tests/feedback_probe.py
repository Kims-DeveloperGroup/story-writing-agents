"""CI-only RED/restoration/GREEN replay of the unchanged selected-material case."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CASE = "tests.test_selected_task_material_codec.SelectedTaskMaterialCodecTests.test_two_captures_keep_closed_wire_shape_and_order"
SOURCE_HASH = "a9696b95cd08e9b6bd4eacf89bcd036738c4bf8823c3d8a313b79791d5c258a3"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", required=True)
    parser.add_argument("--repository-root")
    args = parser.parse_args()
    output = Path(args.json).resolve()
    root = Path(args.repository_root).resolve() if args.repository_root else Path(__file__).resolve().parents[1]
    source = root / "tests/test_selected_task_material_codec.py"
    original = source.read_bytes()
    digest = lambda data: hashlib.sha256(data).hexdigest()
    if os.environ.get("GITHUB_ACTIONS") != "true" or Path.cwd().resolve() != root:
        raise RuntimeError("feedback replay requires the disposable CI checkout")
    if digest(original) != SOURCE_HASH or original.count(b"self.assertEqual(actual, expected)") != 1:
        raise RuntimeError("feedback source differs from the exact retained case")
    temporary = original.replace(b"self.assertEqual(actual, expected)",
                                 b"self.assertEqual(actual, expected[:1])")
    summary = {"canonical_id": CASE, "source_sha": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
               "controller_sha256": digest(Path(__file__).read_bytes()),
               "original_sha256": digest(original), "temporary_sha256": digest(temporary), "runs": []}
    try:
        for phase, content, code, status in (("red", temporary, 1, "failed"), ("green", original, 0, "passed")):
            source.write_bytes(content)
            report = output.with_name(output.stem + "." + phase + ".json")
            log = report.with_suffix(".log")
            command = [sys.executable, "-m", "tests.runner", "run", "--id", CASE, "--json", str(report)]
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
            if (result.returncode != code or row["status"] != status
                    or payload["selection_manifest"]["ordered_ids"] != [CASE]
                    or payload["totals"]["completed"] != 1 or payload["totals"]["errors"] != 0
                    or payload["totals"]["failures"] != int(phase == "red")
                    or payload["source_binding"]["source_valid"] is not True
                    or row["source_sha256"] != digest(content)):
                raise RuntimeError("feedback replay did not observe the exact expected case result")
        summary["outcome"] = "success"
    finally:
        source.write_bytes(original)
        summary["restored_sha256"] = digest(source.read_bytes())
        output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        if summary["restored_sha256"] != SOURCE_HASH:
            raise RuntimeError("feedback source restoration failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
