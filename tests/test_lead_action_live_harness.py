"""Provider-free evidence for the explicit live runner's hard gates."""
from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from tests import lead_action_live_harness as live


class LiveHarnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="a641-guard-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.counter = 0

    def authorization(self, label="case", *, case=live.CASE):
        return {"authorization_id": label, "release_id": "synthetic-release", "case": case, "limits": live.LIMITS, "release_ledger": str(self.root / "release.json"), "model": {"executable": live.EXECUTABLE, "version": live.VERSION, "model": live.MODEL, "effort": live.EFFORT}}

    def guard(self, label="case", **kwargs):
        return live.CallGuard(self.authorization(label, case=kwargs.pop("case", live.CASE)), self.root / label, validate=kwargs.pop("validate", lambda: None), **kwargs)

    @staticmethod
    def request(**changes):
        fields = {"command": [live.EXECUTABLE, "exec", "--model", live.MODEL, "--config", 'model_reasoning_effort="xhigh"'], "stdin": b'{"synthetic":"initial/prelude/correction"}', "timeout_seconds": 90}
        return SimpleNamespace(**(fields | changes))

    async def test_import_and_missing_authorization_submit_nothing(self):
        with mock.patch("asyncio.create_subprocess_exec", side_effect=AssertionError("Import must be inert")), mock.patch("subprocess.check_output", side_effect=AssertionError("Import must be inert")), mock.patch.dict(os.environ, {"STORY_RUN_LIVE": ""}):
            importlib.reload(live)
            with self.assertRaises(live.AuthorizationError):
                live.load_authorization(None)
        with mock.patch.dict(os.environ, {"STORY_RUN_LIVE": "1"}), self.assertRaises(live.AuthorizationError):
            live.load_authorization(self.root / "missing.json")
        self.assertEqual(list(self.root.iterdir()), [])
        # A script launched with only W/src on PYTHONPATH cannot import tests.*.
        repository = Path(live.__file__).resolve().parents[1]
        script_path = [str(repository / 'tests'), str(repository / 'src')]
        script_path += [entry for entry in sys.path if entry not in ('', str(repository))]
        with mock.patch.object(sys, 'path', script_path), mock.patch.dict(sys.modules, {'tests': None}), mock.patch('asyncio.create_subprocess_exec', side_effect=AssertionError('Helper import must be inert')):
            helper = live.evidence_helper()
            self.assertEqual(Path(helper.__file__).resolve(), repository / 'tests/specialist_routing_live.py')

    async def test_case_and_release_caps_count_every_reserved_submission(self):
        calls = []
        async def native(request, **_kwargs):
            calls.append(request)
            return SimpleNamespace(returncode=0, stdout=b"actual", stderr=b"")
        with self.guard() as guard:
            for kind in ("initial", "prelude", "correction", "continuation"):
                await guard.invoke(native, self.request(stdin=kind.encode()))
            with self.assertRaisesRegex(live.AuthorizationError, "Case call cap"):
                await guard.invoke(native, self.request())
            self.assertEqual(len(calls), 4)
            self.assertEqual(json.loads((self.root / "release.json").read_bytes())["reserved"], 4)
            self.assertEqual([p["ordinal"] for p in guard.calls], [1, 2, 3, 4])
        ledger = json.loads((self.root / "release.json").read_bytes())
        ledger["reserved"] = 11
        live.save(self.root / "release.json", ledger)
        with self.guard("last-release-call") as guard:
            await guard.invoke(native, self.request())
            with self.assertRaisesRegex(live.AuthorizationError, "Release call cap"):
                await guard.invoke(native, self.request())
        ledger = json.loads((self.root / "release.json").read_bytes())
        ledger["reserved"] = 0
        live.save(self.root / "release.json", ledger)
        now = [0.0]
        with self.guard("case-time", monotonic=lambda: now[0]) as guard:
            now[0] = 355.1
            with self.assertRaisesRegex(live.AuthorizationError, "Case time cap"):
                guard.reserve(self.request())
            self.assertEqual(guard.calls, [])
        ledger = json.loads((self.root / "release.json").read_bytes())
        ledger["seconds"] = 1255.1
        live.save(self.root / "release.json", ledger)
        with self.guard("release-time") as guard:
            with self.assertRaisesRegex(live.AuthorizationError, "Release time cap"):
                guard.reserve(self.request())
        self.assertEqual(len(calls), 5)










if __name__ == "__main__":
    unittest.main()
