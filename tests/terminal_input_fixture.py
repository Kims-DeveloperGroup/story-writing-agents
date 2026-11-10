"""Per-instance terminal mode state for input driver scenarios."""

class FakeTermios:
    """Model exact activation and restoration around one semantic read."""

    ICANON = 0x02
    ECHO = 0x08
    VEOF = 0
    VINTR = 1
    VMIN = 4
    VTIME = 5
    TCSANOW = 10
    TCSADRAIN = 11

    def __init__(self) -> None:
        self.original = [
            0,
            0,
            0,
            self.ICANON | self.ECHO | 0x40,
            0,
            0,
            [b"\x04", b"\x03", b"x", b"x", 9, 9],
        ]
        self.get_calls: list[int] = []
        self.set_calls: list[tuple[int, int, list[object]]] = []
        self.activation_failure: BaseException | None = None
        self.restoration_failure: BaseException | None = None

    def tcgetattr(self, descriptor: int) -> list[object]:
        self.get_calls.append(descriptor)
        attributes = list(self.original)
        attributes[6] = list(self.original[6])  # type: ignore[arg-type]
        return attributes

    def tcsetattr(
        self,
        descriptor: int,
        when: int,
        attributes: list[object],
    ) -> None:
        copied = list(attributes)
        copied[6] = list(attributes[6])  # type: ignore[arg-type]
        self.set_calls.append((descriptor, when, copied))
        if when == self.TCSANOW and self.activation_failure is not None:
            raise self.activation_failure
        if when == self.TCSADRAIN and self.restoration_failure is not None:
            raise self.restoration_failure


class RecordingOutputOperations:
    """Concrete fixtures retain ownership of every mutable output field."""

    def fileno(self) -> int:
        return 8

    def write(self, text: str) -> int:
        self.records.append(text)
        if self.write_failure is not None:
            raise self.write_failure
        return len(text)

    def flush(self) -> None:
        self.flush_calls += 1
        if self.flush_failure is not None:
            raise self.flush_failure
