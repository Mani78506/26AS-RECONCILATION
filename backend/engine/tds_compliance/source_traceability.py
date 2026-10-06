"""Source-evidence metadata for governed TDS rules.

The helpers deliberately distinguish missing evidence from verified evidence.
They do not research, infer, or approve statutory content.
"""
from __future__ import annotations

from datetime import date
import re
from urllib.parse import urlparse


def source_traceability_errors(record: dict) -> list[str]:
    """Return errors only for supplied malformed metadata, not absent evidence."""
    errors: list[str] = []
    source_url = str(record.get("source_url") or "").strip()
    if source_url:
        parsed = urlparse(source_url)
        if parsed.scheme != "https" or not parsed.netloc:
            errors.append("source_url must be an absolute https URL")
    source_hash = str(record.get("source_document_sha256") or "").strip()
    if source_hash and not re.fullmatch(r"[A-Fa-f0-9]{64}", source_hash):
        errors.append("source_document_sha256 must be a SHA-256 hex digest")
    for field in ("source_retrieved_at", "source_verified_at"):
        value = str(record.get(field) or "").strip()
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                errors.append(f"{field} must use YYYY-MM-DD")
    return errors


def source_traceability_snapshot(record: dict) -> dict:
    """Build the immutable evidence view retained with a governed rule."""
    authority = record.get("source_authority") or record.get("source")
    provision = (
        record.get("source_provision_reference")
        or record.get("provision_reference")
        or record.get("section_reference")
    )
    fields = {
        "source_authority": authority,
        "official_url": record.get("source_url"),
        "source_document_filename": record.get("source_document_filename"),
        "source_document_sha256": record.get("source_document_sha256"),
        "source_page_reference": record.get("source_page_reference"),
        "document_title": record.get("source_document_title"),
        "legal_provision_reference": provision,
        "effective_from": record.get("effective_from"),
        "effective_to": record.get("effective_to"),
        "retrieved_at": record.get("source_retrieved_at"),
        "verification_evidence": record.get("source_verification_evidence"),
        "verified_at": record.get("source_verified_at"),
    }
    required = (
        "source_authority", "document_title",
        "legal_provision_reference", "effective_from", "effective_to",
        "retrieved_at", "verification_evidence", "verified_at",
    )
    errors = source_traceability_errors(record)
    missing = [key for key in required if not str(fields.get(key) or "").strip()]
    if not str(fields["official_url"] or "").strip() and not (
        str(fields["source_document_filename"] or "").strip()
        and str(fields["source_document_sha256"] or "").strip()
    ):
        missing.append("source_locator")
    approval_metadata = record.get("approval_metadata") or {}
    approval_evidence_reference = approval_metadata.get("approval_reference") if isinstance(approval_metadata, dict) else None
    approval_authority = approval_metadata.get("approval_authority") if isinstance(approval_metadata, dict) else None
    approval_recorded = (
        record.get("lifecycle") in {"APPROVED", "ACTIVE"}
        and bool(record.get("approved_at"))
        and bool(record.get("approved_by"))
        and bool(approval_evidence_reference)
        and bool(approval_authority)
    )
    return {
        **fields,
        "status": "VERIFIED" if not missing and not errors else "VERIFICATION_REQUIRED",
        "missing_fields": missing,
        "validation_errors": errors,
        "ca_approval_status": "CA_APPROVAL_RECORDED" if approval_recorded else "CA_APPROVAL_NOT_RECORDED",
        "approval_authority": approval_authority,
        "approval_evidence_reference": approval_evidence_reference,
    }


def catalog_activation_errors(record: dict) -> list[str]:
    """Return evidence-gate errors for an ACTIVE global catalog row.

    A global statutory catalog is a source-controlled configuration channel.
    Its activation is justified by complete, verified source evidence; it does
    not manufacture or require an organisation-specific CA approval record.
    """
    traceability = source_traceability_snapshot(record)
    return [] if traceability["status"] == "VERIFIED" else ["source_verification_required"]


def governed_activation_errors(record: dict) -> list[str]:
    """Return evidence-gate errors for the manual/client-specific rule lifecycle."""
    traceability = source_traceability_snapshot(record)
    errors: list[str] = []
    if traceability["status"] != "VERIFIED":
        errors.append("source_verification_required")
    if traceability["ca_approval_status"] != "CA_APPROVAL_RECORDED":
        errors.append("approval_evidence_required")
    return errors
