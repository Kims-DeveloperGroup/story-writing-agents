"""Existing startup snapshot contract and renderer fixture values."""

from __future__ import annotations

from story_writing_agents.interactive_terminal._core import _STARTUP_WELCOME_SEQUENCE
from story_writing_agents.interactive_terminal._model import (
    CapabilityProfile,
    TerminalCapabilities,
    display_character_width,
)


def capability(
    profile: CapabilityProfile,
    *,
    width: int = 80,
) -> TerminalCapabilities:
    """Build one internally consistent render-capability value."""

    raw = profile is not CapabilityProfile.LINE
    return TerminalCapabilities(
        profile,
        width,
        input_tty=raw,
        output_tty=raw,
        raw_mode=raw,
        cursor_controls=raw,
        decorative_sgr=profile is CapabilityProfile.ANSI,
    )


def physical_width(text: str) -> int:
    """Measure sanitized unstyled text using the foundation cell rule."""

    return sum(display_character_width(character) for character in text)


def verify_plain_stage_snapshots_are_exact_prefix_free_and_legible(self) -> None:
    """Lock every cumulative meaning in every responsive plain tier."""

    for physical, expected_snapshots in self.PLAIN_STAGE_SNAPSHOTS.items():
        capabilities = capability(CapabilityProfile.LINE, width=physical)
        actual_snapshots: list[str] = []
        for index, welcome in enumerate(_STARTUP_WELCOME_SEQUENCE):
            with self.subTest(physical=physical, stage=welcome.stage.value):
                frame = self.render_startup_frame(
                    index,
                    capabilities,
                    physical,
                )
                expected = expected_snapshots[index]
                self.assertEqual(frame.text, expected)
                self.assertEqual(frame.line_count, len(expected.split("\n")))
                actual_snapshots.append(frame.text)
                for label in self.FORBIDDEN_WELCOME_CHROME:
                    self.assertNotIn(label, frame.text)
                self.assertNotRegex(frame.text, r"[-=]{20,}")
        self.assertEqual(tuple(actual_snapshots), expected_snapshots)

    final = self.render_startup_frame(
        3,
        capability(CapabilityProfile.LINE, width=80),
        80,
    )
    self.assertEqual(final.text, self.WIDE_FINAL)
    self.assertEqual(final.line_count, 9)
    self.assertEqual(len(final.text.split("\n")), 9)
    self.assertEqual(max(map(physical_width, final.text.split("\n"))), 44)
    self.assertTrue(
        all(
            character == "\n" or 32 <= ord(character) <= 126
            for character in final.text
        )
    )
