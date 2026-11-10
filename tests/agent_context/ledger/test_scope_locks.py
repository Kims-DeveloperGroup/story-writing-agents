'Existing LedgerConcurrencyTests scenarios, moved without new cases.'
from __future__ import annotations
import os
import subprocess
import sys
import threading
import unittest
from dataclasses import replace
from unittest import mock
from story_writing_agents.agent_context.ledger import atomic_files as owner_atomic_files
from story_writing_agents.agent_context.ledger import checkpoint_ledger as owner_checkpoint_ledger
from story_writing_agents.agent_context.ledger import record_validation as owner_record_validation
from story_writing_agents.agent_context.ledger import record_values as owner_record_values
from tests.agent_context.ledger import ledger_fixture as fixture_ledger_fixture


class LedgerConcurrencyTests(fixture_ledger_fixture.LedgerTestCase):
    def test_two_independent_writers_serialize_without_lost_update_or_deadlock(self,
    ) -> None:
        ledger_a = owner_checkpoint_ledger.ContextLedger(self.workspace, clock=fixture_ledger_fixture.LedgerClock())
        ledger_b = owner_checkpoint_ledger.ContextLedger(self.workspace, clock=fixture_ledger_fixture.LedgerClock())
        plan_a = self.plan
        plan_b = replace(self.plan, attempt_id="attempt-competing")
        ready = threading.Barrier(3)
        results: list[tuple[str, object]] = []

        def reserve(name: str, ledger: owner_checkpoint_ledger.ContextLedger, plan: owner_record_values.ContextLedgerAttemptPlan
        ) -> None:
            ready.wait(timeout=5)
            try:
                results.append((
                        name, ledger.reserve_attempt(
                            self.key, expected_generation=0,
                            plan=plan,),
                    ))
            except owner_record_validation.ContextLedgerError as error:
                results.append((name, error.code))

        threads = (threading.Thread(target=reserve, args=("a", ledger_a, plan_a)),
            threading.Thread(target=reserve, args=("b", ledger_b, plan_b)),)
        for thread in threads:
            thread.start()
        ready.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
        successes = [value
            for _name, value in results
            if isinstance(value, owner_record_values.ContextLedgerInspection)]
        failures = [value
            for _name, value in results
            if isinstance(value, owner_record_validation.ContextLedgerErrorCode)]
        self.assertEqual(len(successes), 1, results)
        self.assertEqual(failures, [owner_record_validation.ContextLedgerErrorCode.INVALID_TRANSITION])
        observed = self.ledger.inspect(self.key)
        self.assertEqual(observed, successes[0])
        self.assertIn(observed.record.attempt.plan, {plan_a, plan_b})

    def test_lock_free_reader_over_atomic_replace_returns_complete_successor(self,
    ) -> None:
        stale = self.ledger.invalidate(self.key, expected_generation=0)
        read_started = threading.Event()
        release_read = threading.Event()
        results: list[object] = []
        real_read = owner_atomic_files.os.read

        def controlled_read(descriptor: int, size: int) -> bytes:
            chunk = real_read(descriptor, size)
            if (threading.current_thread().name == "ledger-reader"
                and not read_started.is_set()):
                read_started.set()
                if not release_read.wait(timeout=5):
                    raise AssertionError("reader release timed out")
            return chunk

        def inspect() -> None:
            try:
                results.append(self.ledger.inspect(self.key))
            except BaseException as error:
                results.append(error)

        with mock.patch.object(owner_atomic_files.os, "read", side_effect=controlled_read):
            reader = threading.Thread(target=inspect, name="ledger-reader")
            reader.start()
            self.assertTrue(read_started.wait(timeout=5))
            successor = self.ledger.reserve_attempt(self.key,
                expected_generation=stale.generation,
                plan=self.plan,)
            release_read.set()
            reader.join(timeout=5)
            self.assertFalse(reader.is_alive())
        self.assertEqual(results, [successor])
        self.assertIs(results[0].record.state, owner_record_values.ContextLedgerRecordState.INCOMPLETE)

    def test_repeated_atomic_replacement_retries_are_bounded_at_eight(self) -> None:
        observed = self.ledger.invalidate(self.key, expected_generation=0)
        payload = self.record_path().read_bytes()
        with mock.patch.object(
            owner_atomic_files, '_read_payload_once',
            return_value=(True, payload, True),) as read_once:
            replay = self.ledger.inspect(self.key)
        self.assertEqual(replay, observed)
        self.assertEqual(read_once.call_count, 8)

    def test_in_place_mutation_during_read_is_storage_unsafe(self) -> None:
        self.ledger.invalidate(self.key, expected_generation=0)
        path = self.record_path()
        real_read = owner_atomic_files.os.read
        mutated = False

        def mutate_after_read(descriptor: int, size: int) -> bytes:
            nonlocal mutated
            chunk = real_read(descriptor, size)
            if not mutated and chunk:
                mutated = True
                target = os.open(path, os.O_RDWR)
                try:
                    st = os.fstat(target)
                    os.pwrite(target, b" ", path.stat().st_size - 1)
                    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
                    os.fsync(target)
                finally:
                    os.close(target)
            return chunk

        with mock.patch.object(
            owner_atomic_files.os, "read",
            side_effect=mutate_after_read,):
            self.assert_ledger_error(
                lambda: self.ledger.inspect(self.key),
                owner_record_validation.ContextLedgerErrorCode.STORAGE_UNSAFE,)
        self.assertTrue(mutated)

    @unittest.skipUnless(owner_atomic_files._fcntl is not None, "requires POSIX flock")
    def test_cross_process_lock_contention_times_out_then_recovers(self) -> None:
        stale = self.ledger.invalidate(self.key, expected_generation=0)
        script = ("import fcntl, os, sys; "
            "fd=os.open(sys.argv[1], os.O_RDWR); "
            "fcntl.flock(fd, fcntl.LOCK_EX); print('locked', flush=True); "
            "sys.stdin.read(1); fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)")
        process = subprocess.Popen(
            [sys.executable, "-B", "-W", "error", "-c", script, str(self.lock_path())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,)

        def cleanup() -> None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

        self.addCleanup(cleanup)
        assert process.stdout is not None
        self.assertEqual(process.stdout.readline(), "locked\n")
        with mock.patch.object(
            owner_atomic_files, 'CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS',
            0.05,):
            self.assert_ledger_error(
                lambda: self.ledger.reserve_attempt(
                    self.key,
                    expected_generation=stale.generation,
                    plan=self.plan,),
                owner_record_validation.ContextLedgerErrorCode.LOCK_TIMEOUT,)
        assert process.stdin is not None
        process.stdin.write("x")
        process.stdin.flush()
        process.wait(timeout=5)
        stderr = process.stderr.read() if process.stderr is not None else ""
        self.assertEqual(process.returncode, 0, stderr)
        recovered = self.ledger.reserve_attempt(self.key,
            expected_generation=stale.generation,
            plan=self.plan,)
        self.assertIs(recovered.record.state, owner_record_values.ContextLedgerRecordState.INCOMPLETE)
