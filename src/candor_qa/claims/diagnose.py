"""Diagnosis rule tree for claims-to-network checks."""
from __future__ import annotations

from candor_qa.claims.exhibit_a import UNMAPPED


def diagnose(
    mapped_network: str,
    address_match: str,
    contract: str,
    agreement_type: str,
    *,
    assignment_npi: bool = False,
) -> tuple[str, str, bool]:
    """Return (diagnosis_code, explanation, flagged)."""
    contract = (contract or "").strip().lower()
    agreement_type = (agreement_type or "").strip().lower()

    if mapped_network == UNMAPPED:
        return (
            "mapping_gap",
            f"({contract}, {agreement_type}) has no network mapping in Exhibit A.",
            True,
        )

    if address_match == "NO_SERVICE_ADDRESS":
        return (
            "missing_service_address",
            "Claim has no service address — cannot match to a practice location.",
            True,
        )

    if address_match == "NO_REFERENCE":
        return (
            "reference_missing",
            "No cached NPPES/Candor data for this NPI — address not verified.",
            False,
        )

    if agreement_type == "billing" and contract == "partner":
        return (
            "wrong_agreement_type",
            "partner + billing maps to Bill, not Preferred.",
            True,
        )

    if address_match == "NO_MATCH":
        return (
            "address_mismatch",
            "Claim service address does not match Candor or NPPES practice locations.",
            True,
        )

    if mapped_network == "Preferred" and address_match == "MATCH" and assignment_npi:
        return (
            "likely_matching_bug",
            "Mapping and address both check out — provider absence suggests a matching defect.",
            True,
        )

    return ("ok", "Claim maps and address matches reference locations.", False)
