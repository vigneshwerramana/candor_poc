"""Delta engine: added / deleted / per-field changes between two prepared
snapshots. Dataset difference is NOT quality difference — this stage only
establishes WHAT changed; later stages decide whether changes are good.

Design points:
- Comparisons run on NORMALIZED values; raw values ride along ('<col>__raw')
  purely to count changes that normalization suppressed (format noise).
- Set fields (compare_as: set) compare as frozensets: order-only differences
  are suppressed and counted, not flagged.
- Change taxonomy: wiped (value->null) is its own kind because a wipe is
  almost never a legitimate update — it gets harsher routing downstream.
- Comparison masks are vectorized per field; only actual changes are
  materialized into FieldChange objects.
"""
from __future__ import annotations

import pandas as pd

from candor_qa.config import SchemaConfig
from candor_qa.contracts import ChangeKind, DeltaSummary, FieldChange, VolumeSummary
from candor_qa.stage2_compare.record_events import build_record_events


def _as_set(value, delimiter: str):
    if pd.isna(value):
        return value
    return frozenset(str(value).split(delimiter))


def compute_delta(
    baseline: pd.DataFrame,
    candidate: pd.DataFrame,
    schema: SchemaConfig,
    placeholders: dict[str, list[str]] | None = None,
) -> DeltaSummary:
    key = schema.key_field
    merged = baseline.merge(
        candidate, on=key, how="outer", suffixes=("__old", "__new"), indicator=True
    )
    both = merged[merged["_merge"] == "both"]

    changes: list[FieldChange] = []
    suppressed_norm = 0
    suppressed_set = 0
    changed_row_keys: set[str] = set()

    for field in schema.diff_fields:
        spec = schema.fields[field]
        old, new = both[f"{field}__old"], both[f"{field}__new"]
        raw_old, raw_new = both[f"{field}__raw__old"], both[f"{field}__raw__new"]

        # NA-safe equality: (old == new) yields <NA> when either side is null,
        # which would silently drop wiped/filled changes from the delta.
        both_null = old.isna() & new.isna()
        str_equal = pd.Series(old == new).fillna(False) | both_null
        raw_equal = pd.Series(raw_old == raw_new).fillna(False) \
            | (raw_old.isna() & raw_new.isna())

        if spec.compare_as == "set":
            old_cmp = old.map(lambda v: _as_set(v, schema.list_delimiter))
            new_cmp = new.map(lambda v: _as_set(v, schema.list_delimiter))
            equal = pd.Series(old_cmp == new_cmp).fillna(False) | both_null
            # sets match but normalized strings differ == order-only diff
            suppressed_set += int((equal & ~str_equal).sum())
            # strings match but raw differed == format noise
            suppressed_norm += int((str_equal & ~raw_equal).sum())
        else:
            equal = str_equal
            suppressed_norm += int((equal & ~raw_equal).sum())

        changed = both[~equal]
        for npi, old_v, new_v in zip(changed[key], changed[f"{field}__old"],
                                     changed[f"{field}__new"]):
            if pd.isna(new_v):
                kind = ChangeKind.wiped
            elif pd.isna(old_v):
                kind = ChangeKind.filled
            else:
                kind = ChangeKind.modified
            changes.append(FieldChange(
                npi=npi, field=field,
                old_value=None if pd.isna(old_v) else old_v,
                new_value=None if pd.isna(new_v) else new_v,
                change_kind=kind,
            ))
            changed_row_keys.add(npi)

    baseline_count, candidate_count = len(baseline), len(candidate)
    added = int((merged["_merge"] == "right_only").sum())
    deleted = int((merged["_merge"] == "left_only").sum())
    volume = VolumeSummary(
        baseline_count=baseline_count,
        candidate_count=candidate_count,
        added_count=added,
        deleted_count=deleted,
        updated_count=len(changed_row_keys),
        unchanged_count=len(both) - len(changed_row_keys),
        added_pct=added / baseline_count if baseline_count else 0.0,
        deleted_pct=deleted / baseline_count if baseline_count else 0.0,
    )
    return DeltaSummary(
        field_changes=changes,
        record_events=build_record_events(merged, schema, placeholders),
        volume=volume,
        suppressed_by_normalization=suppressed_norm,
        suppressed_by_set_compare=suppressed_set,
    )
