"""Antigravity native recovery evidence tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from story_writing_agents import antigravity_native_recovery
from story_writing_agents.agent_context import canonical_provider_json
from story_writing_agents.antigravity_adapter import AntigravityAdapter
from story_writing_agents.provider_adapter import ProviderInvocationError, ProviderResponseDiagnostic


class AntigravityRecoveryEvidenceTests(unittest.TestCase):
    """Exercise real no-follow native evidence reads with synthetic public facts."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.adapter = AntigravityAdapter(executable='unused', timeout_seconds=1, state_root=self.root, explicit_model=None, explicit_effort=None)
        self.provider = self.root / 'provider'
        self.provider.mkdir()
        fixtures = Path(__file__).parent / 'fixtures/antigravity_recovered_finish'
        self.terminal = json.loads((fixtures / 'terminal.json').read_text())
        self.prompt = (fixtures / 'request.json').read_text()
        self.steps = [json.loads(line) for line in (fixtures / 'transcript.jsonl').read_text().splitlines()]
        self.trace = self.provider / 'antigravity-cli/brain/synthetic-thread/.system_generated/logs/transcript_full.jsonl'

    def write_trace(self, steps=None, *, content=None) -> None:
        self.trace.parent.mkdir(parents=True, exist_ok=True)
        self.trace.write_bytes(content if content is not None else b''.join((canonical_provider_json(step) + b'\n' for step in (self.steps if steps is None else steps))))

    def accepts(self, baseline, *, terminal=None):
        value = self.terminal if terminal is None else terminal
        diagnostic = ProviderResponseDiagnostic(canonical_provider_json(value), b'diagnostic', 0)
        return self.adapter._provider_result(
            diagnostic.stdout,
            expected_thread_id=baseline.thread_id if baseline else None,
            expected_schema=self.terminal['json_schema'],
            response_diagnostic=diagnostic,
            completion_check=lambda thread, output, error: antigravity_native_recovery.has_current_finish(
                self.adapter, self.provider, baseline, thread, self.prompt, output, error,
            ),
        )

    def test_captured_shape_and_clean_current_finish_recover(self) -> None:
        for clean in (False, True):
            with self.subTest(clean=clean):
                if self.trace.exists():
                    self.trace.unlink()
                    import shutil
                    shutil.rmtree(self.trace.parents[2])
                baseline = antigravity_native_recovery.native_recovery_baseline(self.adapter, self.provider, None)
                steps = self.steps if not clean else [self.steps[0], self.steps[-2], self.steps[-1]]
                steps = json.loads(json.dumps(steps))
                for index, step in enumerate(steps):
                    step['step_index'] = index
                self.write_trace(steps)
                result = self.accepts(baseline)
                self.assertEqual(json.loads(result.response_text), self.terminal['structured_output'])
                self.assertIsNotNone(result.response_diagnostic)

    def test_reordered_finish_arguments_match_but_scalar_types_do_not(self) -> None:
        baseline = antigravity_native_recovery.native_recovery_baseline(self.adapter, self.provider, None)
        args = self.steps[-2]['tool_calls'][0]['args']
        self.steps[-2]['tool_calls'][0]['args'] = dict(reversed(list(args.items())))
        self.write_trace()
        self.assertIsNotNone(self.accepts(baseline))
        for expected, actual in ((True, 1), (1, True), (1, 1.0), ('1', 1)):
            with self.subTest(expected=expected, actual=actual):
                terminal = json.loads(json.dumps(self.terminal))
                terminal['structured_output']['action']['result']['typed'] = expected
                self.steps[-2]['tool_calls'][0]['args']['action']['result']['typed'] = actual
                self.write_trace()
                with self.assertRaises(ProviderInvocationError):
                    self.accepts(baseline, terminal=terminal)

    def test_resume_requires_unchanged_prefix_and_new_matching_request(self) -> None:
        self.write_trace()
        baseline = antigravity_native_recovery.native_recovery_baseline(self.adapter, self.provider, 'synthetic-thread')
        with self.assertRaises(ProviderInvocationError):
            self.accepts(baseline)
        resumed = json.loads(json.dumps(self.steps))
        for step in resumed:
            step['step_index'] += len(self.steps)
        self.write_trace(self.steps + resumed)
        self.assertIsNotNone(self.accepts(baseline))
        self.steps[0]['content'] += 'changed'
        self.write_trace(self.steps + resumed)
        with self.assertRaises(ProviderInvocationError):
            self.accepts(baseline)

    def test_missing_preexisting_wrong_request_or_replaced_directory_reject(self) -> None:
        baseline = antigravity_native_recovery.native_recovery_baseline(self.adapter, self.provider, None)
        with self.assertRaises(ProviderInvocationError):
            self.accepts(baseline)
        self.write_trace()
        preexisting = antigravity_native_recovery.native_recovery_baseline(self.adapter, self.provider, None)
        with self.assertRaises(ProviderInvocationError):
            self.accepts(preexisting)
        self.steps[0]['content'] = '<USER_REQUEST>\nother\n</USER_REQUEST>'
        self.write_trace()
        with self.assertRaises(ProviderInvocationError):
            self.accepts(baseline)
        self.provider.rename(self.root / 'old-provider')
        self.provider.mkdir()
        self.write_trace()
        with self.assertRaises(ProviderInvocationError):
            self.accepts(baseline)

    def test_unsafe_native_paths_and_bounded_trace_reject(self) -> None:
        import story_writing_agents.antigravity_adapter as module
        from story_writing_agents import antigravity_recovery_matching
        for kind in ('symlink', 'hardlink', 'fifo', 'directory', 'oversized', 'too_many_steps', 'long_line'):
            with self.subTest(kind=kind):
                if self.trace.exists() or self.trace.is_symlink():
                    if self.trace.is_dir():
                        self.trace.rmdir()
                    else:
                        self.trace.unlink()
                baseline = antigravity_native_recovery.native_recovery_baseline(self.adapter, self.provider, None)
                self.write_trace()
                baseline = replace(baseline, conversation_ids=frozenset())
                if kind == 'oversized':
                    with mock.patch.object(antigravity_native_recovery, '_NATIVE_TRACE_MAX_BYTES', 10):
                        with self.assertRaises(ProviderInvocationError):
                            self.accepts(baseline)
                elif kind == 'too_many_steps':
                    with mock.patch.object(antigravity_recovery_matching, '_NATIVE_TRACE_MAX_STEPS', 2):
                        with self.assertRaises(ProviderInvocationError):
                            self.accepts(baseline)
                elif kind == 'long_line':
                    with mock.patch.object(antigravity_recovery_matching, '_NATIVE_TRACE_MAX_LINE_BYTES', 8):
                        with self.assertRaises(ProviderInvocationError):
                            self.accepts(baseline)
                else:
                    self.trace.unlink()
                    outside = self.root / ('outside-' + kind)
                    if kind == 'symlink':
                        outside.write_text('outside')
                        self.trace.symlink_to(outside)
                    elif kind == 'hardlink':
                        outside.write_text('outside')
                        os.link(outside, self.trace)
                    elif kind == 'fifo':
                        os.mkfifo(self.trace)
                    else:
                        self.trace.mkdir()
                    with self.assertRaises(ProviderInvocationError):
                        self.accepts(baseline)
                    if outside.exists():
                        self.assertEqual(outside.read_text(), 'outside')
        if self.trace.exists() or self.trace.is_symlink():
            if self.trace.is_dir():
                self.trace.rmdir()
            else:
                self.trace.unlink()
        self.write_trace()
        brain = self.provider / 'antigravity-cli/brain'
        with mock.patch.object(antigravity_native_recovery, '_NATIVE_SNAPSHOT_MAX_ENTRIES', 0):
            self.assertIsNone(antigravity_native_recovery.native_recovery_baseline(self.adapter, self.provider, None))
        baseline = antigravity_native_recovery.native_recovery_baseline(self.adapter, self.provider, None)
        baseline = replace(baseline, conversation_ids=frozenset())
        real_read = os.read
        mutated = False

        def changing_read(fd, length):
            nonlocal mutated
            value = real_read(fd, length)
            if not mutated and value:
                mutated = True
                with self.trace.open('ab') as stream:
                    stream.write(b'\n')
            return value
        with mock.patch.object(module.os, 'read', side_effect=changing_read):
            with self.assertRaises(ProviderInvocationError):
                self.accepts(baseline)
        self.assertTrue(mutated)
        self.write_trace()
        original_bytes = self.trace.read_bytes()
        outside_brain = self.root / 'outside-brain'
        brain.rename(outside_brain)
        brain.symlink_to(outside_brain, target_is_directory=True)
        with self.assertRaises(ProviderInvocationError):
            self.accepts(baseline)
        outside_trace = outside_brain / 'synthetic-thread/.system_generated/logs/transcript_full.jsonl'
        self.assertEqual(outside_trace.read_bytes(), original_bytes)

    def test_incomplete_ambiguous_nonmonotonic_and_forged_native_steps_reject(self) -> None:
        baseline = antigravity_native_recovery.native_recovery_baseline(self.adapter, self.provider, None)
        original = json.loads(json.dumps(self.steps))
        variants = []

        def changed(index, key, value):
            steps = json.loads(json.dumps(original))
            steps[index][key] = value
            return steps
        variants += [original[:-1], original + [dict(original[-1], step_index=7)], changed(0, 'step_index', True), changed(2, 'source', 'MODEL'), changed(-2, 'status', 'ACTIVE'), changed(-1, 'type', 'ERROR_MESSAGE'), changed(2, 'content', 'Error: unrelated failure'), changed(4, 'content', original[2]['content']), changed(4, 'content', original[4]['content'][:-1] + '1'), changed(-1, 'created_at', '2025-01-01T00:00:00Z'), changed(-1, 'content', 'Task is complete.'), changed(-2, 'tool_calls', [{'name': 'search', 'args': {}}]), changed(-2, 'tool_calls', [{'name': 'finish', 'args': {}}])]
        for index, steps in enumerate(variants):
            with self.subTest(index=index):
                self.write_trace(steps)
                with self.assertRaises(ProviderInvocationError):
                    self.accepts(baseline)
        for content in (b'\xff\n', b'{}\n{}', b'{"step_index":0,"step_index":0}\n', b'{"step_index":NaN}\n'):
            with self.subTest(content=content):
                self.write_trace(content=content)
                with self.assertRaises(ProviderInvocationError):
                    self.accepts(baseline)

    def test_status_identity_schema_and_diagnostic_requirements_remain_strict(self) -> None:
        baseline = antigravity_native_recovery.native_recovery_baseline(self.adapter, self.provider, None)
        self.write_trace()
        for key, value in (('status', 'FAILED'), ('error', 'unrelated'), ('error', self.terminal['error'][:-1] + '9'), ('conversation_id', '../escape'), ('structured_output', []), ('json_schema', {})):
            with self.subTest(key=key, value=value):
                terminal = dict(self.terminal, **{key: value})
                with self.assertRaises(ProviderInvocationError):
                    self.accepts(baseline, terminal=terminal)
        reordered = dict(self.terminal, json_schema=dict(reversed(list(self.terminal['json_schema'].items()))))
        with self.assertRaises(ProviderInvocationError):
            self.accepts(baseline, terminal=reordered)
        with self.assertRaises(ProviderInvocationError):
            self.accepts(None)
        success = dict(self.terminal, status='SUCCESS')
        self.assertIsNone(self.accepts(None, terminal=success).response_diagnostic)
