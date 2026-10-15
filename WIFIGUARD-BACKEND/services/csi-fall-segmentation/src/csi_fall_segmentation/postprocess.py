from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class ABTriggerConfig:
    markov_p01: float = 0.08
    markov_p10: float = 0.10
    temperature: float = 1.7
    markov_threshold: float = 0.40
    initial_fall_posterior: float = 0.01
    probability_epsilon: float = 1e-7
    ema_alpha: float = 0.30
    ema_threshold: float = 0.475
    b_probability_threshold: float = 0.25
    b_k: int = 4
    b_n: int = 4

    def __post_init__(self) -> None:
        probabilities = (
            self.markov_p01, self.markov_p10, self.markov_threshold,
            self.initial_fall_posterior, self.probability_epsilon,
            self.ema_alpha, self.ema_threshold, self.b_probability_threshold,
        )
        if not all(math.isfinite(x) and 0.0 <= x <= 1.0 for x in probabilities):
            raise ValueError("All probability-like config values must be finite and in [0, 1]")
        if not math.isfinite(self.temperature) or self.temperature <= 0:
            raise ValueError("temperature must be finite and > 0")
        if not (0 < self.probability_epsilon < 0.5):
            raise ValueError("probability_epsilon must be in (0, 0.5)")
        if self.b_n < 1 or not (1 <= self.b_k <= self.b_n):
            raise ValueError("B trigger requires 1 <= b_k <= b_n")

    @classmethod
    def from_json(cls, path: Path | str) -> "ABTriggerConfig":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        unknown = set(data).difference(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Unknown A/B config keys: {sorted(unknown)}")
        return cls(**data)

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


class ABTriggerState:
    def __init__(self, config: ABTriggerConfig | None = None):
        self.config = config or ABTriggerConfig()
        self.reset()

    def reset(self) -> None:
        self._q_fall = float(self.config.initial_fall_posterior)
        self._ema: float | None = None
        self._b_history: deque[int] = deque(maxlen=self.config.b_n)

    def update(self, fall_probability: float) -> dict[str, float | int]:
        p = float(fall_probability)
        if not math.isfinite(p):
            raise ValueError("fall_probability must be finite")
        if not 0.0 <= p <= 1.0:
            raise ValueError("fall_probability must be in [0, 1]")

        c = self.config
        clipped = min(max(p, c.probability_epsilon), 1.0 - c.probability_epsilon)
        logit = math.log(clipped / (1.0 - clipped)) / c.temperature
        obs_fall = 1.0 / (1.0 + math.exp(-logit))
        prior = self._q_fall * (1.0 - c.markov_p10) + (1.0 - self._q_fall) * c.markov_p01
        numerator = prior * obs_fall
        denominator = numerator + (1.0 - prior) * (1.0 - obs_fall)
        self._q_fall = numerator / max(denominator, 1e-12)
        markov = int(self._q_fall >= c.markov_threshold)

        self._ema = p if self._ema is None else c.ema_alpha * p + (1.0 - c.ema_alpha) * self._ema
        ema_rescue = int(self._ema >= c.ema_threshold)
        evidence = int(p >= c.b_probability_threshold)
        self._b_history.append(evidence)
        recent_count = sum(self._b_history)
        kofn = int(len(self._b_history) == c.b_n and recent_count >= c.b_k)
        return {
            "markov_posterior": self._q_fall,
            "markov_trigger": markov,
            "ema_state": self._ema,
            "ema_rescue": ema_rescue,
            "a_trigger": int(markov or ema_rescue),
            "b_evidence": evidence,
            "b_recent_positive_count": recent_count,
            "kofn_rescue": kofn,
            "b_trigger": int(markov or kofn),
        }


def apply_ab_triggers(probabilities: np.ndarray, config: ABTriggerConfig | None = None) -> dict[str, np.ndarray]:
    values = np.asarray(probabilities, dtype=float)
    if values.ndim != 1:
        raise ValueError("probabilities must be a one-dimensional array")
    state = ABTriggerState(config)
    rows = [state.update(value) for value in values]
    keys = (
        "markov_posterior", "markov_trigger", "ema_state", "ema_rescue", "a_trigger",
        "b_evidence", "b_recent_positive_count", "kofn_rescue", "b_trigger",
    )
    float_keys = {"markov_posterior", "ema_state"}
    return {
        key: np.asarray([row[key] for row in rows], dtype=float if key in float_keys else np.int8)
        for key in keys
    }
