from wifiguard_local_edge.compare import aggregate, reduction_percent


def test_reduction_has_correct_sign_and_denominator():
    assert reduction_percent(50, 20) == 60
    assert reduction_percent(100, 120) == -19.999999999999996
    assert reduction_percent(0, 0) is None


def test_aggregate_pools_samples_and_weights_cpu_by_duration():
    def run(seconds, cpu, value):
        return {
            "samples": [
                {
                    field: value
                    for field in (
                        "feature_ms",
                        "inference_ms",
                        "total_ms",
                        "start_lateness_ms",
                    )
                }
            ],
            "resources": {
                "measurement_seconds": seconds,
                "process_cpu_percent_one_core_basis": cpu,
                "steady_rss_median_mib": 400,
                "sampled_peak_rss_mib": 450,
            },
            "startup_resources": {"sampled_peak_rss_mib": 500},
            "deadline_misses": 0,
        }

    result = aggregate([run(10, 100, 10), run(30, 50, 20)])
    assert result["cpu_percent_one_core_basis"] == 62.5
    assert result["feature_ms"]["median"] == 15
    assert result["windows"] == 2
    assert result["measurement_seconds"] == 40
