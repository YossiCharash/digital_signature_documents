"""In-memory audit log for document operations."""

from datetime import datetime
from typing import Any

_audit_log: list[dict[str, Any]] = []


def log_operation(
    operation: str,
    document_hash: str | None = None,
    recipient: str | None = None,
    filename: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Log an operation to the audit log."""
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "operation": operation,
        "document_hash": document_hash,
        "recipient": recipient,
        "filename": filename,
        "metadata": metadata or {},
    }
    _audit_log.append(entry)


def get_audit_log(limit: int | None = None) -> list[dict[str, Any]]:
    """Get audit log entries, optionally limited."""
    if limit is None:
        return _audit_log.copy()
    return _audit_log[-limit:]


def clear_audit_log() -> None:
    """Clear the audit log (for testing)."""
    _audit_log.clear()
