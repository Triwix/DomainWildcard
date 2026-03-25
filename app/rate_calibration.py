from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class StageMetrics:
    name: str
    interval_seconds: float
    duration_seconds: float
    total_requests: int = 0
    status_200: int = 0
    status_404: int = 0
    status_429: int = 0
    status_5xx: int = 0
    other_status: int = 0
    transport_errors: int = 0
    elapsed_seconds: float = 0.0
    latency_p95_ms: float = 0.0

    def effective_rps(self) -> float:
        if self.elapsed_seconds <= 0:
            return 0.0
        return float(self.total_requests) / float(self.elapsed_seconds)

    def instability_rate(self) -> float:
        if self.total_requests <= 0:
            return 1.0
        unstable = self.status_5xx + self.transport_errors
        return float(unstable) / float(self.total_requests)


@dataclass
class CalibrationDecision:
    winning_interval_seconds: Optional[float]
    winning_stage_name: Optional[str]
    reason: str


def evaluate_stage(metrics: StageMetrics, max_error_rate: float = 0.005) -> Tuple[bool, str]:
    if metrics.total_requests <= 0:
        return False, "no requests sent"

    if metrics.status_429 > 0:
        return False, f"received {metrics.status_429} HTTP 429 responses"

    if metrics.other_status > 0:
        return False, f"received {metrics.other_status} unexpected HTTP responses"

    instability = metrics.instability_rate()
    if instability > max_error_rate:
        return False, f"instability rate {instability:.3%} exceeds {max_error_rate:.3%}"

    return True, "passed"


def choose_winning_interval(
    stage_results: List[StageMetrics],
    validation_result: Optional[StageMetrics] = None,
    max_error_rate: float = 0.005,
) -> CalibrationDecision:
    passing: List[StageMetrics] = []
    for metrics in stage_results:
        ok, _ = evaluate_stage(metrics, max_error_rate=max_error_rate)
        if not ok:
            break
        passing.append(metrics)

    if not passing:
        return CalibrationDecision(
            winning_interval_seconds=None,
            winning_stage_name=None,
            reason="No calibration stage passed policy thresholds.",
        )

    winner = passing[-1]
    reason = f"Fastest passing stage is {winner.name}."

    if validation_result is not None:
        validation_ok, validation_reason = evaluate_stage(validation_result, max_error_rate=max_error_rate)
        if not validation_ok:
            if len(passing) >= 2:
                fallback = passing[-2]
                return CalibrationDecision(
                    winning_interval_seconds=fallback.interval_seconds,
                    winning_stage_name=fallback.name,
                    reason=f"Validation failed ({validation_reason}); falling back to {fallback.name}.",
                )
            return CalibrationDecision(
                winning_interval_seconds=None,
                winning_stage_name=None,
                reason=f"Validation failed ({validation_reason}) and no slower passing stage exists.",
            )

    return CalibrationDecision(
        winning_interval_seconds=winner.interval_seconds,
        winning_stage_name=winner.name,
        reason=reason,
    )
