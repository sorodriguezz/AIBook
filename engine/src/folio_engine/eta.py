"""Time-remaining estimation that converges as real throughput is observed.

Work is planned in two currencies — prompt tokens (read) and generated tokens
(written) — because local hardware processes them at very different speeds.
Each completed request refines both speeds with an exponential moving average,
and a calibration factor corrects our cheap token estimates.
"""

import time
from dataclasses import dataclass, field


@dataclass
class Work:
    prompt: float = 0.0
    gen: float = 0.0

    def __iadd__(self, other: "Work") -> "Work":
        self.prompt += other.prompt
        self.gen += other.gen
        return self


@dataclass
class EtaTracker:
    prompt_tps: float = 300.0  # defaults until the first measurement
    gen_tps: float = 15.0
    alpha: float = 0.25
    calibration: float = 1.0  # real tokens / estimated tokens
    remaining: Work = field(default_factory=Work)
    total: Work = field(default_factory=Work)
    started_at: float = field(default_factory=time.monotonic)
    _measured: bool = False

    def seed(self, prompt_tps: float | None, gen_tps: float | None) -> None:
        """Start from a benchmark result instead of generic defaults."""
        if prompt_tps:
            self.prompt_tps = prompt_tps
        if gen_tps:
            self.gen_tps = gen_tps

    def plan(self, work: Work) -> None:
        self.remaining = Work(work.prompt, work.gen)
        self.total = Work(work.prompt, work.gen)

    def add(self, work: Work) -> None:
        self.remaining += work
        self.total += work

    def record(
        self,
        planned: Work,
        prompt_tokens: int,
        gen_tokens: int,
        prompt_seconds: float,
        gen_seconds: float,
    ) -> None:
        """Account for one finished request."""
        self.remaining.prompt = max(0.0, self.remaining.prompt - planned.prompt)
        self.remaining.gen = max(0.0, self.remaining.gen - planned.gen)
        a = self.alpha if self._measured else 1.0
        if prompt_tokens > 50 and prompt_seconds > 0.01:
            self.prompt_tps = (1 - a) * self.prompt_tps + a * (prompt_tokens / prompt_seconds)
        if gen_tokens > 5 and gen_seconds > 0.01:
            self.gen_tps = (1 - a) * self.gen_tps + a * (gen_tokens / gen_seconds)
            self._measured = True
        est = planned.prompt + planned.gen
        if est > 0 and prompt_tokens + gen_tokens > 0:
            ratio = (prompt_tokens + gen_tokens) / est
            self.calibration = 0.85 * self.calibration + 0.15 * min(3.0, max(0.33, ratio))

    def skip(self, planned: Work) -> None:
        """Work that turned out to be unnecessary (cached, resumed)."""
        self.remaining.prompt = max(0.0, self.remaining.prompt - planned.prompt)
        self.remaining.gen = max(0.0, self.remaining.gen - planned.gen)

    def seconds_left(self) -> float:
        r = self.remaining
        return self.calibration * (r.prompt / self.prompt_tps + r.gen / self.gen_tps)

    def fraction_done(self) -> float:
        total_t = self.total.prompt / self.prompt_tps + self.total.gen / self.gen_tps
        if total_t <= 0:
            return 0.0
        left_t = self.remaining.prompt / self.prompt_tps + self.remaining.gen / self.gen_tps
        return max(0.0, min(1.0, 1 - left_t / total_t))

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def snapshot(self) -> dict:
        return {
            "eta_seconds": round(self.seconds_left()),
            "elapsed_seconds": round(self.elapsed()),
            "progress": round(self.fraction_done(), 4),
            "prompt_tps": round(self.prompt_tps, 1),
            "gen_tps": round(self.gen_tps, 1),
        }


class ProgressRateEta:
    """ETA from a 0..1 progress signal, for stages we don't control (BabelDOC)."""

    def __init__(self, alpha: float = 0.15) -> None:
        self.alpha = alpha
        self.rate: float | None = None  # progress per second
        self._last: tuple[float, float] | None = None
        self.started_at = time.monotonic()
        self.progress = 0.0

    def update(self, progress: float) -> None:
        now = time.monotonic()
        if self._last is not None:
            dt = now - self._last[0]
            dp = progress - self._last[1]
            if dt >= 1.0 and dp >= 0:
                inst = dp / dt
                self.rate = inst if self.rate is None else (1 - self.alpha) * self.rate + self.alpha * inst
                self._last = (now, progress)
        else:
            self._last = (now, progress)
        self.progress = progress

    def seconds_left(self) -> float | None:
        if not self.rate or self.rate <= 0:
            return None
        return (1 - self.progress) / self.rate
