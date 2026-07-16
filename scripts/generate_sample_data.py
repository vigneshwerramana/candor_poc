"""Generate synthetic provider snapshots WITH ground truth.

Because the generator plants every change itself, it knows the answer key.
It emits per-scenario label files so (a) the mock judge answers from truth,
(b) the E2E tests assert detection precision/recall, and (c) reports show
judge accuracy. NPIs are Luhn-valid per the CMS spec (check digit over
"80840" + 9-digit base) so the addition validator's checksum gate is real.

Outputs (all emitted every run, deterministic seed):
  providers_v1.csv          yesterday's snapshot
  providers_v2.csv          today's snapshot with planted regressions
  providers_v2_clean.csv    today's snapshot with ONLY legitimate changes -> APPROVE
  dev_a.csv / dev_b.csv     two scraper outputs on the same frozen universe
  labels.json               ground truth for v1-vs-v2
  labels_clean.json         ground truth subset for v1-vs-v2_clean
  labels_compete.json       ground truth for dev_a-vs-dev_b
  source_evidence.json      evidence store (shared; keyed npi/field)

v2 planted regressions:            v2 false-positive traps:
- 15 wiped phones                  - 20 phone reformats (same digits)
- 8 placeholder phone overwrites   - 10 insurance order shuffles
- 2 placeholder fills of nulls     - 10 name reformats (SMITH, JOHN, MD)
- 3 bad-source phone changes       - 5 zip leading-zero strips
- 3 invalid additions              - 5 ""-vs-"N/A" null representations
- 10 duplicate NPI rows
- 16 silently dropped rows
- 60 stale last_updated rows
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SEED = 42
N_PROVIDERS = 400
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

FIRST_NAMES = ["John", "Maria", "Wei", "Aisha", "Carlos", "Emily", "Raj", "Sarah",
               "David", "Fatima", "James", "Elena", "Omar", "Grace", "Luis", "Priya"]
LAST_NAMES = ["Smith", "Garcia", "Chen", "Patel", "Johnson", "Nguyen", "Brown",
              "Khan", "Lee", "Martinez", "Wilson", "Okafor", "Silva", "Cohen"]
SPECIALTIES = ["cardiology", "family medicine", "pediatrics", "dermatology",
               "orthopedics", "internal medicine", "neurology", "oncology"]
INSURERS = ["Aetna", "BlueCross", "Cigna", "UnitedHealth", "Humana", "Kaiser",
            "Medicare", "Medicaid"]
STREETS = ["Main St", "Oak Ave", "Elm St", "Park Blvd", "Cedar Ln", "Maple Dr"]
CITIES_STATES_ZIPS = [
    ("Boston", "MA", "02115"), ("Cambridge", "MA", "02139"),
    ("Providence", "RI", "02903"), ("Hartford", "CT", "06103"),
    ("New York", "NY", "10001"), ("Chicago", "IL", "60601"),
    ("Austin", "TX", "78701"), ("Denver", "CO", "80202"),
    ("Seattle", "WA", "98101"), ("Atlanta", "GA", "30303"),
]
AREA_CODES = ["212", "310", "415", "617", "312", "512", "303", "206", "404", "401"]

TODAY = date.today()


def npi_check_digit(base9: str) -> str:
    """Luhn over '80840' + base, per CMS. Mirrors candor_qa normalize.py."""
    total = 0
    for i, ch in enumerate(reversed("80840" + base9)):
        digit = int(ch)
        if i % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def make_npi(base_int: int) -> str:
    base9 = str(base_int)
    assert len(base9) == 9
    return base9 + npi_check_digit(base9)


def break_npi(npi: str) -> str:
    """Flip the check digit -> structurally plausible but invalid NPI."""
    return npi[:9] + str((int(npi[9]) + 1) % 10)


def make_phone(rng: random.Random) -> str:
    return rng.choice(AREA_CODES) + "".join(str(rng.randint(0, 9)) for _ in range(7))


def format_phone(digits: str, style: int) -> str:
    if style == 0:
        return digits
    if style == 1:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def make_provider(rng: random.Random, npi: str, version: str,
                  updated: date | None = None) -> dict:
    city, state, zip5 = rng.choice(CITIES_STATES_ZIPS)
    return {
        "npi": npi,
        "name": f"Dr. {rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
        "phone": make_phone(rng),
        "address": f"{rng.randint(1, 999)} {rng.choice(STREETS)}, {city}",
        "zip": zip5, "state": state,
        "specialty": rng.choice(SPECIALTIES),
        "insurance_accepted": "|".join(rng.sample(INSURERS, rng.randint(2, 4))),
        "last_updated": (updated or TODAY - timedelta(days=rng.randint(1, 30))).isoformat(),
        "pipeline_version": version,
    }


def build_v1(rng: random.Random) -> pd.DataFrame:
    rows = []
    for i in range(N_PROVIDERS):
        # 85% fresh (within ~170 days), 15% already stale
        if i % 20 < 17:
            updated = TODAY - timedelta(days=rng.randint(1, 170))
        else:
            updated = TODAY - timedelta(days=rng.randint(200, 350))
        row = make_provider(rng, make_npi(100_000_000 + i), "v120", updated)
        row["phone"] = format_phone(row["phone"], rng.randint(0, 2))
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    rng = random.Random(SEED)
    v1 = build_v1(rng)

    labels_v2: list[dict] = []
    labels_compete: list[dict] = []
    additions_truth_v2: dict[str, str] = {}
    additions_truth_compete: dict[str, str] = {}
    evidence: dict[str, dict[str, list]] = {}

    def add_evidence(npi: str, field: str, candidates: list[tuple[str, str]]) -> None:
        entry = evidence.setdefault(npi, {}).setdefault(field, [])
        for source, value in candidates:
            entry.append({
                "source": source,
                "value": value,
                "observed_at": (TODAY - timedelta(days=rng.randint(1, 30))).isoformat(),
            })

    def label(bucket: list, npi: str, field: str, old, new, truth: str, note: str) -> None:
        bucket.append({"npi": npi, "field": field, "old": old, "new": new,
                       "truth_label": truth, "note": note})

    # Disjoint index partitions so no row gets two conflicting treatments.
    pool = list(range(N_PROVIDERS))
    rng.shuffle(pool)
    cursor = 0

    def next_indices(n: int) -> list[int]:
        nonlocal cursor
        chunk = pool[cursor:cursor + n]
        cursor += n
        return chunk

    dropped = next_indices(16)
    wiped = next_indices(15)
    placeholder = next_indices(8)
    valid_phone = next_indices(20)
    conflict_phone = next_indices(3)
    reformat_phone = next_indices(20)
    valid_address = next_indices(10)
    na_null_trap = next_indices(5)
    valid_insurance = next_indices(10)
    shuffle_insurance = next_indices(10)
    name_reformat = next_indices(10)
    zip_strip = next_indices(5)
    valid_specialty = next_indices(5)
    unlabeled_specialty = next_indices(4)   # no truth -> judge says uncertain
    null_phone = next_indices(3)            # v1 nulls; v2 fills them
    state_corrupt = next_indices(10)        # state no longer matches zip prefix
    stale_shift = next_indices(60)
    compete_b_wins = next_indices(30)       # dev_b has the corroborated value
    compete_a_wins = next_indices(5)        # dev_b has a wrong value

    # v1 pre-conditions the planted cases depend on
    for i in null_phone:
        v1.at[i, "phone"] = ""              # missing phone in yesterday's data
    for i in na_null_trap:
        v1.at[i, "address"] = ""            # null as empty string

    # ---------------------------------------------------------------- v2 dirty
    v2 = v1.copy()
    v2["pipeline_version"] = "v121"
    clean_changes: list[tuple[int, str, str]] = []   # (row, column, value) for v2_clean

    def change(i: int, column: str, value: str, clean: bool) -> None:
        v2.at[i, column] = value
        if clean:
            clean_changes.append((i, column, value))

    # --- planted regressions (dirty v2 only) --------------------------------
    for i in wiped:
        label(labels_v2, v1.at[i, "npi"], "phone", v1.at[i, "phone"], None,
              "regression", "phone wiped")
        change(i, "phone", "", clean=False)

    for i in placeholder:
        label(labels_v2, v1.at[i, "npi"], "phone", v1.at[i, "phone"], "9999999999",
              "regression", "placeholder overwrite")
        change(i, "phone", "9999999999", clean=False)

    filled_placeholder, filled_real = null_phone[:2], null_phone[2]
    for i in filled_placeholder:
        label(labels_v2, v1.at[i, "npi"], "phone", None, "9999999999",
              "regression", "placeholder fill of missing phone")
        change(i, "phone", "9999999999", clean=False)
    real_fill = make_phone(rng)
    add_evidence(v1.at[filled_real, "npi"], "phone",
                 [("npi_registry", real_fill), ("hospital_website", real_fill)])
    label(labels_v2, v1.at[filled_real, "npi"], "phone", None, real_fill,
          "valid_update", "missing phone filled from evidence")
    change(filled_real, "phone", real_fill, clean=True)

    for i in conflict_phone:
        npi, old = v1.at[i, "npi"], v1.at[i, "phone"]
        bad = make_phone(rng)
        add_evidence(npi, "phone", [("claims", old), ("web_scrape", bad)])
        label(labels_v2, npi, "phone", old, bad, "regression",
              "bad-source overwrite, evidence conflicts")
        change(i, "phone", bad, clean=False)

    for i in state_corrupt:
        npi, old = v1.at[i, "npi"], v1.at[i, "state"]
        wrong = rng.choice([s for s in ("CA", "TX", "FL", "OH") if s != old])
        label(labels_v2, npi, "state", old, wrong, "regression",
              "state corrupted: no longer matches the zip prefix")
        change(i, "state", wrong, clean=False)

    for i in stale_shift:
        change(i, "last_updated",
               (TODAY - timedelta(days=rng.randint(200, 350))).isoformat(), clean=False)

    # --- legitimate updates (also in v2_clean) -------------------------------
    for i in valid_phone:
        npi, old = v1.at[i, "npi"], v1.at[i, "phone"]
        new = make_phone(rng)
        add_evidence(npi, "phone", [("npi_registry", new), ("hospital_website", new)])
        label(labels_v2, npi, "phone", old, new, "valid_update",
              "real phone change, 2 sources agree")
        change(i, "phone", format_phone(new, rng.randint(0, 2)), clean=True)

    for i in valid_address:
        npi, old = v1.at[i, "npi"], v1.at[i, "address"]
        city, state, zip5 = rng.choice(CITIES_STATES_ZIPS)
        new = f"{rng.randint(1, 999)} {rng.choice(STREETS)}, {city}"
        for field, value in (("address", new), ("zip", zip5), ("state", state)):
            add_evidence(npi, field, [("npi_registry", value),
                                      ("state_medical_board", value)])
            change(i, field, value, clean=True)
        label(labels_v2, npi, "address", old, new, "valid_update",
              "provider moved, 2 sources agree")

    for i in valid_insurance:
        npi, old = v1.at[i, "npi"], v1.at[i, "insurance_accepted"]
        addition = rng.choice([x for x in INSURERS if x not in old.split("|")])
        new = old + "|" + addition
        add_evidence(npi, "insurance_accepted", [("insurance_directory", new)])
        label(labels_v2, npi, "insurance_accepted", old, new, "valid_update",
              "new insurer accepted, 1 source (weak evidence -> judged)")
        change(i, "insurance_accepted", new, clean=True)

    for i in valid_specialty:
        npi, old = v1.at[i, "npi"], v1.at[i, "specialty"]
        new = rng.choice([s for s in SPECIALTIES if s != old])
        label(labels_v2, npi, "specialty", old, new, "valid_update",
              "no evidence -> unverifiable -> judged")
        change(i, "specialty", new, clean=True)

    # Deliberately unlabeled: exercises the "uncertain" verdict + review queue.
    for i in unlabeled_specialty:
        old = v1.at[i, "specialty"]
        change(i, "specialty",
               rng.choice([s for s in SPECIALTIES if s != old]), clean=True)

    # --- false-positive traps (must be suppressed; also in v2_clean) ---------
    for i in reformat_phone:
        digits = "".join(c for c in v1.at[i, "phone"] if c.isdigit())[-10:]
        old_style = v1.at[i, "phone"]
        new_style = format_phone(digits, (len(old_style) % 3))
        if new_style == old_style:
            new_style = format_phone(digits, (len(old_style) + 1) % 3)
        label(labels_v2, v1.at[i, "npi"], "phone", old_style, new_style,
              "false_positive_trap", "same digits, different format")
        change(i, "phone", new_style, clean=True)

    for i in shuffle_insurance:
        items = v1.at[i, "insurance_accepted"].split("|")
        label(labels_v2, v1.at[i, "npi"], "insurance_accepted",
              "|".join(items), "|".join(items[::-1]),
              "false_positive_trap", "order shuffle, same set")
        change(i, "insurance_accepted", "|".join(items[::-1]), clean=True)

    for i in name_reformat:
        old = v1.at[i, "name"]                       # "Dr. John Smith"
        parts = old.replace("Dr. ", "").split()
        new = f"{parts[-1].upper()}, {' '.join(parts[:-1]).upper()}, MD"
        label(labels_v2, v1.at[i, "npi"], "name", old, new,
              "false_positive_trap", "name reformat")
        change(i, "name", new, clean=True)

    for i in zip_strip:
        old = v1.at[i, "zip"]
        if not old.startswith("0"):                  # force a leading-zero zip
            v1.at[i, "zip"] = "0" + old[1:]
            old = v1.at[i, "zip"]
        stripped = old.lstrip("0")
        label(labels_v2, v1.at[i, "npi"], "zip", old, stripped,
              "false_positive_trap", "leading zero stripped by candidate pipeline")
        change(i, "zip", stripped, clean=True)

    for i in na_null_trap:
        label(labels_v2, v1.at[i, "npi"], "address", None, None,
              "false_positive_trap", "'' vs 'N/A' null representations")
        change(i, "address", "N/A", clean=True)

    # --- additions: 6 valid + 3 invalid (labeled record events) --------------
    added_rows: list[dict] = []
    for j in range(4):                               # valid, evidence-backed
        row = make_provider(rng, make_npi(200_000_000 + j), "v121")
        add_evidence(row["npi"], "phone", [("npi_registry", row["phone"]),
                                           ("hospital_website", row["phone"])])
        add_evidence(row["npi"], "address", [("npi_registry", row["address"]),
                                             ("state_medical_board", row["address"])])
        additions_truth_v2[row["npi"]] = "valid_addition"
        added_rows.append(row)
    for j in range(4, 6):                            # valid, no evidence -> judged
        row = make_provider(rng, make_npi(200_000_000 + j), "v121")
        additions_truth_v2[row["npi"]] = "valid_addition"
        added_rows.append(row)

    bad_luhn = make_provider(rng, break_npi(make_npi(200_000_006)), "v121")
    bad_phone = make_provider(rng, make_npi(200_000_007), "v121")
    bad_phone["phone"] = "9999999999"
    near_empty = {**{k: "" for k in bad_phone}, "npi": make_npi(200_000_008),
                  "name": "Dr. Ghost Entry", "pipeline_version": "v121"}
    for row in (bad_luhn, bad_phone, near_empty):
        additions_truth_v2[row["npi"]] = "invalid_addition"
        added_rows.append(row)

    valid_added_rows = added_rows[:6]                # for v2_clean

    # --- v2 row-level events --------------------------------------------------
    dropped_npis = [v1.at[i, "npi"] for i in dropped]
    v2 = v2.drop(index=dropped).reset_index(drop=True)
    duplicated_npis = list(v2["npi"].iloc[:10])
    duplicate_rows = v2.iloc[:10].copy()
    null_npi_rows = pd.DataFrame([
        {**make_provider(rng, "", "v121"), "npi": "", "name": "Dr. Null Key"},
        {**make_provider(rng, "", "v121"), "npi": "N/A", "name": "Dr. Missing Key"},
    ])
    v2 = pd.concat([v2, pd.DataFrame(added_rows), duplicate_rows, null_npi_rows],
                   ignore_index=True)

    # ---------------------------------------------------------------- v2 clean
    v2_clean = v1.copy()
    v2_clean["pipeline_version"] = "v121"
    for i, column, value in clean_changes:
        v2_clean.at[i, column] = value
    v2_clean = pd.concat([v2_clean, pd.DataFrame(valid_added_rows)],
                         ignore_index=True)

    clean_labels = [l for l in labels_v2 if l["truth_label"] != "regression"]

    # ------------------------------------------------------------- competition
    # Two scrapers on the same frozen universe (v1's providers).
    dev_a = v1.copy()
    dev_a["pipeline_version"] = "scraper_a"
    dev_b = v1.copy()
    dev_b["pipeline_version"] = "scraper_b"

    for i in compete_b_wins:                         # B found the current phone
        npi, old = v1.at[i, "npi"], v1.at[i, "phone"]
        new = make_phone(rng)
        add_evidence(npi, "phone", [("npi_registry", new), ("hospital_website", new)])
        label(labels_compete, npi, "phone", old, new, "valid_update",
              "compete: B's value corroborated by 2 sources")
        dev_b.at[i, "phone"] = new
        dev_b.at[i, "last_updated"] = (TODAY - timedelta(days=rng.randint(1, 10))).isoformat()

    for i in compete_a_wins:                         # B scraped a wrong number
        npi, old = v1.at[i, "npi"], v1.at[i, "phone"]
        bogus = make_phone(rng)
        add_evidence(npi, "phone", [("claims", old), ("npi_registry", old)])
        label(labels_compete, npi, "phone", old, bogus, "regression",
              "compete: evidence supports A's value")
        dev_b.at[i, "phone"] = bogus

    compete_added = []
    for j in range(10):                              # B's extra coverage, all real
        row = make_provider(rng, make_npi(300_000_000 + j), "scraper_b")
        add_evidence(row["npi"], "phone", [("npi_registry", row["phone"]),
                                           ("hospital_website", row["phone"])])
        additions_truth_compete[row["npi"]] = "valid_addition"
        compete_added.append(row)
    dev_b = pd.concat([dev_b, pd.DataFrame(compete_added)], ignore_index=True)

    # --- write outputs --------------------------------------------------------
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "evaluation_history").mkdir(exist_ok=True)
    v1.to_csv(DATA_DIR / "providers_v1.csv", index=False)
    v2.to_csv(DATA_DIR / "providers_v2.csv", index=False)
    v2_clean.to_csv(DATA_DIR / "providers_v2_clean.csv", index=False)
    dev_a.to_csv(DATA_DIR / "dev_a.csv", index=False)
    dev_b.to_csv(DATA_DIR / "dev_b.csv", index=False)

    (DATA_DIR / "labels.json").write_text(json.dumps({
        "field_changes": labels_v2,
        "record_events": {"additions": additions_truth_v2},
        "row_events": {
            "dropped_npis": dropped_npis,
            "added_npis": [r["npi"] for r in added_rows],
            "duplicated_npis": duplicated_npis,
            "stale_shifted_count": len(stale_shift),
            "null_npi_rows": 2,
        },
    }, indent=2))
    (DATA_DIR / "labels_clean.json").write_text(json.dumps({
        "field_changes": clean_labels,
        "record_events": {"additions": {
            r["npi"]: "valid_addition" for r in valid_added_rows}},
        "row_events": {},
    }, indent=2))
    (DATA_DIR / "labels_compete.json").write_text(json.dumps({
        "field_changes": labels_compete,
        "record_events": {"additions": additions_truth_compete},
        "row_events": {},
    }, indent=2))
    (DATA_DIR / "source_evidence.json").write_text(json.dumps(evidence, indent=2))

    print(f"v1 {len(v1)} · v2 {len(v2)} (raw) · v2_clean {len(v2_clean)} · "
          f"dev_a {len(dev_a)} · dev_b {len(dev_b)}")
    print(f"labels: v2 {len(labels_v2)} field + {len(additions_truth_v2)} additions · "
          f"compete {len(labels_compete)} field + {len(additions_truth_compete)} additions")


if __name__ == "__main__":
    main()
