"""Bounded lookbehind for final-text stop markers; no tokenization or usage inference."""


class StopFilter:
    def __init__(self, sequences: tuple[str, ...]) -> None:
        if any(not s for s in sequences):
            raise ValueError("Stop sequences must be nonempty")
        self.sequences = sequences
        self.pending = ""
        self.matched: str | None = None

    def feed(self, text: str) -> str:
        if self.matched is not None:
            return ""
        self.pending += text
        matches = [(self.pending.index(s), len(s), s) for s in self.sequences if s in self.pending]
        if matches:
            offset, _, self.matched = min(matches)
            output, self.pending = self.pending[:offset], ""
            return output
        # Keep only a suffix that could still become a marker in the next chunk.
        keep = max(
            (
                n
                for s in self.sequences
                for n in range(1, min(len(s), len(self.pending) + 1))
                if self.pending.endswith(s[:n])
            ),
            default=0,
        )
        cut = len(self.pending) - keep
        output, self.pending = self.pending[:cut], self.pending[cut:]
        return output

    def finish(self) -> str:
        output, self.pending = self.pending, ""
        return output
