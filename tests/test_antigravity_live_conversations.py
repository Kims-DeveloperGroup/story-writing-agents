"""Reusable opt-in cases; omitted live execution is never a successful matrix."""
import os
from pathlib import Path
import unittest
from tests.antigravity_live_harness import run_case


@unittest.skipUnless(os.environ.get("STORY_RUN_LIVE") == "1", "Explicit live-provider opt-in required")
class LiveConversationTests(unittest.IsolatedAsyncioTestCase):
    """Each method requires its complete declared case, not just a native result."""

    async def test_l01_pro_high_pty(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("L01-PRO-HIGH-PTY", Path(root))
        self.assertTrue(result["passed"], result)

    async def test_l02_pro_low_one(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("L02-PRO-LOW-ONE", Path(root))
        self.assertTrue(result["passed"], result)

    async def test_l03_flash_three(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("L03-FLASH-THREE", Path(root))
        self.assertTrue(result["passed"], result)

    async def test_l04_sonnet_one(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("L04-SONNET-ONE", Path(root))
        self.assertTrue(result["passed"], result)

    async def test_l05_mixed(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("L05-MIXED", Path(root))
        self.assertTrue(result["passed"], result)

    async def test_l06a_question(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("L06A-QUESTION", Path(root))
        self.assertTrue(result["passed"], result)

    async def test_l06b_critique(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("L06B-CRITIQUE", Path(root))
        self.assertTrue(result["passed"], result)

    async def test_l06c_recommendation(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("L06C-RECOMMENDATION", Path(root))
        self.assertTrue(result["passed"], result)

    async def test_l07_search_read(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("L07-SEARCH-READ", Path(root))
        self.assertTrue(result["passed"], result)

    async def test_l08_decisions(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("L08-DECISIONS", Path(root))
        self.assertTrue(result["passed"], result)

    async def test_l09_second_round(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("L09-SECOND-ROUND", Path(root))
        self.assertTrue(result["passed"], result)

    async def test_f01_reference_drift(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("F01-REFERENCE-DRIFT", Path(root))
        self.assertTrue(result["passed"], result)

    async def test_f02_lost_authority(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("F02-LOST-AUTHORITY", Path(root))
        self.assertTrue(result["passed"], result)

    async def test_f03_cancellation(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("F03-CANCELLATION", Path(root))
        self.assertTrue(result["passed"], result)

    async def test_f04_failure_recovery(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("F04-FAILURE-RECOVERY", Path(root))
        self.assertTrue(result["passed"], result)

    async def test_f05_diagnostics_recovery(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("F05-DIAGNOSTICS-RECOVERY", Path(root))
        self.assertTrue(result["passed"], result)

    async def test_f06_budget_finalization(self):
        root = os.environ.get("STORY_LIVE_EVIDENCE_DIR")
        self.assertTrue(root, "STORY_LIVE_EVIDENCE_DIR is required")
        result = await run_case("F06-BUDGET-FINALIZATION", Path(root))
        self.assertTrue(result["passed"], result)
