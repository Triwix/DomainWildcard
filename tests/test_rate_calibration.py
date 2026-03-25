from app.rate_calibration import StageMetrics, choose_winning_interval, evaluate_stage


def make_metrics(
    name: str,
    interval: float,
    total: int,
    status_429: int = 0,
    status_5xx: int = 0,
    transport_errors: int = 0,
) -> StageMetrics:
    metrics = StageMetrics(
        name=name,
        interval_seconds=interval,
        duration_seconds=120.0,
        total_requests=total,
        status_404=max(0, total - status_429 - status_5xx - transport_errors),
        status_429=status_429,
        status_5xx=status_5xx,
        transport_errors=transport_errors,
        elapsed_seconds=120.0,
    )
    return metrics


def test_choose_winning_interval_picks_fastest_zero_429_stage():
    stages = [
        make_metrics("stage-0.500s", 0.5, total=100),
        make_metrics("stage-0.400s", 0.4, total=120),
        make_metrics("stage-0.330s", 0.33, total=140, status_429=1),
    ]
    decision = choose_winning_interval(stages)
    assert decision.winning_interval_seconds == 0.4
    assert decision.winning_stage_name == "stage-0.400s"


def test_choose_winning_interval_falls_back_on_failed_validation():
    stages = [
        make_metrics("stage-0.500s", 0.5, total=100),
        make_metrics("stage-0.400s", 0.4, total=120),
    ]
    validation = make_metrics("validation-0.400s", 0.4, total=120, status_429=1)

    decision = choose_winning_interval(stages, validation_result=validation)
    assert decision.winning_interval_seconds == 0.5
    assert decision.winning_stage_name == "stage-0.500s"


def test_evaluate_stage_fails_when_instability_exceeds_threshold():
    unstable = make_metrics(
        "stage-0.250s",
        0.25,
        total=1000,
        status_5xx=3,
        transport_errors=3,
    )
    passed, reason = evaluate_stage(unstable, max_error_rate=0.005)
    assert passed is False
    assert "instability rate" in reason


def test_evaluate_stage_fails_on_unexpected_http_statuses():
    metrics = make_metrics("stage-0.100s", 0.1, total=100)
    metrics.status_404 = 95
    metrics.other_status = 5

    passed, reason = evaluate_stage(metrics, max_error_rate=0.005)
    assert passed is False
    assert "unexpected HTTP responses" in reason
