"""Stage 2 — COMPARE: what changed (delta) and how healthy each snapshot is."""
from candor_qa.stage2_compare.delta import compute_delta
from candor_qa.stage2_compare.snapshot_metrics import compute_snapshot_metrics

__all__ = ["compute_delta", "compute_snapshot_metrics"]
