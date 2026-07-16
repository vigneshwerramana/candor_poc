"""Whole-record events: rows that appeared (additions) or disappeared
(deletions) between snapshots. Called by compute_delta while the outer merge
is in scope — the merge is never recomputed.

Each event carries structural validity flags so obviously-junk additions
(bad NPI checksum, placeholder phone, near-empty record) can be rejected
without spending evidence lookups or judge budget on them."""
from __future__ import annotations

import pandas as pd

from candor_qa.config import SchemaConfig
from candor_qa.contracts import RecordEvent, RecordEventKind
from candor_qa.stage1_prepare.normalize import npi_checksum_ok

REQUIRED_COMPLETENESS = 0.5


def _build_event(row: pd.Series, suffix: str, kind: RecordEventKind,
                 schema: SchemaConfig, placeholders: dict[str, list[str]]) -> RecordEvent:
    record = {}
    for field in schema.fields:
        value = row.get(f"{field}{suffix}")
        record[field] = None if pd.isna(value) else value
    record[schema.key_field] = row[schema.key_field]

    data_fields = schema.diff_fields
    completeness = sum(record[f] is not None for f in data_fields) / len(data_fields)

    phone = record.get("phone")
    phone_ok = bool(phone) and phone.isdigit() and len(phone) == 10 \
        and phone not in set(placeholders.get("phone", []))
    return RecordEvent(
        npi=row[schema.key_field],
        kind=kind,
        record=record,
        completeness=round(completeness, 4),
        validity_flags={
            "npi_luhn_ok": npi_checksum_ok(record[schema.key_field]),
            "phone_ok": phone_ok,
            "required_ok": completeness >= REQUIRED_COMPLETENESS,
        },
    )


def build_record_events(
    merged: pd.DataFrame,
    schema: SchemaConfig,
    placeholders: dict[str, list[str]] | None = None,
) -> list[RecordEvent]:
    placeholders = placeholders or {}
    events: list[RecordEvent] = []
    for _, row in merged[merged["_merge"] == "right_only"].iterrows():
        events.append(_build_event(row, "__new", RecordEventKind.added,
                                   schema, placeholders))
    for _, row in merged[merged["_merge"] == "left_only"].iterrows():
        events.append(_build_event(row, "__old", RecordEventKind.deleted,
                                   schema, placeholders))
    return events
