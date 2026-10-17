from __future__ import annotations

from typing import Any
import numpy as np


def _ratio(a: int, b: int) -> float:
    return float(a / b) if b else 0.0


def binary_metrics(truth: np.ndarray, pred: np.ndarray, method: str) -> dict[str, Any]:
    y = np.asarray(truth, dtype=np.int8)
    p = np.asarray(pred, dtype=np.int8)
    if y.shape != p.shape or not np.isin(y, [0, 1]).all() or not np.isin(p, [0, 1]).all():
        raise ValueError("truth and predictions must be matching binary arrays")
    tp, fp = int(np.sum((y == 1) & (p == 1))), int(np.sum((y == 0) & (p == 1)))
    tn, fn = int(np.sum((y == 0) & (p == 0))), int(np.sum((y == 1) & (p == 0)))
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    specificity = _ratio(tn, tn + fp)
    fall_f1 = _ratio(2 * precision * recall, precision + recall)
    nonfall_precision, nonfall_recall = _ratio(tn, tn + fn), specificity
    nonfall_f1 = _ratio(2 * nonfall_precision * nonfall_recall, nonfall_precision + nonfall_recall)
    return {"method": method, "n": len(y), "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "accuracy": _ratio(tp + tn, len(y)), "fall_precision": precision,
            "fall_recall": recall, "fall_f1": fall_f1, "nonfall_f1": nonfall_f1,
            "macro_f1": (fall_f1 + nonfall_f1) / 2, "specificity": specificity,
            "fpr": _ratio(fp, fp + tn)}


def segments(values: np.ndarray) -> list[tuple[int, int]]:
    x = np.asarray(values, dtype=np.int8)
    starts = np.flatnonzero((x == 1) & np.r_[True, x[:-1] == 0])
    ends = np.flatnonzero((x == 1) & np.r_[x[1:] == 0, True])
    return list(zip(starts.tolist(), ends.tolist()))


def event_metrics(truth: np.ndarray, pred: np.ndarray, times: np.ndarray, method: str,
                  fixed_delay: float) -> dict[str, Any]:
    truth_segments, pred_segments = segments(truth), segments(pred)
    hits, delays, tails, early = 0, [], [], 0
    overlapping_pred: set[int] = set()
    for ts, te in truth_segments:
        overlaps = [(i, ps, pe) for i, (ps, pe) in enumerate(pred_segments) if ps <= te and pe >= ts]
        if overlaps:
            hits += 1
            overlapping_pred.update(i for i, _, _ in overlaps)
            first = min(max(ps, ts) for _, ps, _ in overlaps)
            delays.append(float(times[first] - times[ts]))
            tails.append(max(0, max(pe for _, _, pe in overlaps) - te))
    if truth_segments:
        early = sum(pe < truth_segments[0][0] for _, pe in pred_segments)
    onset = min(delays) if delays else None
    return {"method": method, "truth_event_count": len(truth_segments),
            "predicted_event_count": len(pred_segments), "truth_event_hit_count": hits,
            "missed_truth_event_count": len(truth_segments) - hits,
            "false_alarm_segment_count": len(pred_segments) - len(overlapping_pred),
            "first_overlapping_trigger_delay_seconds": onset,
            "positive_tail_window_count": max(tails) if tails else 0,
            "early_alarm_segment_count": early, "model_fixed_delay_seconds": fixed_delay,
            "postprocess_onset_delay_seconds": onset,
            "total_alarm_delay_seconds": None if onset is None else fixed_delay + onset}
