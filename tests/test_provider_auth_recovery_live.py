"""Explicitly authorized live cases; ordinary discovery never calls providers."""

import os
from pathlib import Path
import unittest
from unittest import mock

from tests.provider_auth_recovery_live_harness import execute


@unittest.skipUnless(os.environ.get("STORY_AUTH631_LIVE") == "authorized", "explicit live authorization required")
class ProviderAuthRecoveryLiveTests(unittest.TestCase):
    def case(self, name):
        evidence = os.environ.get("STORY_AUTH631_EVIDENCE")
        self.assertTrue(
            evidence,
            "Set STORY_AUTH631_EVIDENCE to the prepared evidence directory "
            "before running authorized live provider tests.",
        )
        root = Path(evidence)
        self.assertTrue((root / "ledger.json").is_file())
        self.assertEqual(execute(root, name)["outcome"], "passed")

    def test_pro_auth_pty(self):
        self.case("L631-PRO-AUTH-PTY")

    def test_flash_mixed(self):
        self.case("L631-FLASH-MIXED")

    def test_sonnet_mixed(self):
        self.case("L631-SONNET-MIXED")

    def test_pro_low_mixed_no_auth(self):
        self.case("L631-PRO-MIXED-NOAUTH")


class ProviderAuthRecoveryLiveConfigurationTests(unittest.TestCase):
    def test_missing_evidence_env_fails_before_harness_execution(self):
        with mock.patch.dict(os.environ, {"STORY_AUTH631_LIVE": "authorized"}, clear=True):
            with mock.patch(__name__ + ".execute") as execute_harness:
                case = ProviderAuthRecoveryLiveTests("test_pro_auth_pty")
                with self.assertRaisesRegex(AssertionError, "Set STORY_AUTH631_EVIDENCE"):
                    case.case("L631-PRO-AUTH-PTY")
                execute_harness.assert_not_called()
