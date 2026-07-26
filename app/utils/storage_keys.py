"""S3 object-key construction.

Object keys are derived from a filename supplied by the caller, so they are
built here rather than inline: a raw filename is both a path-traversal vector
(``../``) and a collision risk (two callers sending ``contract.pdf`` would
overwrite each other's signed document). Every key gets a UUID segment, which
also stops anyone from guessing another caller's key.
"""

import re
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath

# Leaves letters, digits and a small punctuation set. Applied after the path
# components are stripped, so it only has to guard the name itself.
_UNSAFE_CHARS = re.compile(r"[^\w.\- ]", re.UNICODE)
_COLLAPSE = re.compile(r"[\s_]+")

# S3 keys may be up to 1024 bytes. The cap here is on the human-readable tail
# only, well under the limit once the prefix and UUID are added.
_MAX_NAME_LENGTH = 80

DEFAULT_NAME = "document"


def sanitize_filename(original_filename: str | None) -> str:
    """Reduce a caller-supplied filename to a safe, extension-less base name.

    Strips directory components using both POSIX and Windows separators: the
    callers are Windows machines, so ``..\\..\\evil`` is as likely as its
    forward-slash counterpart, and ``PurePosixPath`` alone does not split on
    backslashes.
    """
    name = (original_filename or "").strip()
    if not name:
        return DEFAULT_NAME

    # Strip directory components under both separator conventions.
    name = PureWindowsPath(PurePosixPath(name).name).name

    # Drop the extension; callers always get ".pdf" appended by build_object_key.
    stem = name.rsplit(".", 1)[0] if "." in name else name

    # Normalise so visually identical names collapse to the same key, and drop
    # control characters that would otherwise survive the character filter.
    stem = unicodedata.normalize("NFC", stem)
    stem = "".join(ch for ch in stem if unicodedata.category(ch)[0] != "C")

    stem = _UNSAFE_CHARS.sub("_", stem)
    stem = _COLLAPSE.sub("_", stem).strip("._-")

    if not stem:
        return DEFAULT_NAME
    return stem[:_MAX_NAME_LENGTH]


def build_object_key(original_filename: str | None, prefix: str = "documents") -> str:
    """Return a unique, traversal-safe S3 key for a signed PDF.

    Shape: ``documents/2026/07/26/<uuid4>/<safe-name>.pdf``

    The date prefix keeps the bucket browsable and makes lifecycle rules easy
    to express; the UUID guarantees uniqueness and unguessability. The
    sanitized original name is kept as the final segment purely so a human
    reading the bucket can tell what a key holds.
    """
    now = datetime.now(UTC)
    return (
        f"{prefix}/{now:%Y/%m/%d}/{uuid.uuid4()}/"
        f"{sanitize_filename(original_filename)}.pdf"
    )
