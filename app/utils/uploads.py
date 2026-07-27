"""Bounded, validated reading of uploaded PDFs.

Starlette imposes no limit on ``UploadFile.read()``: an unbounded call loads
the whole body into memory, so a single large upload is enough to exhaust the
worker. Reading in chunks with a hard cap keeps the failure mode a 413 instead
of an OOM kill, and the magic-byte check rejects non-PDFs before they reach the
signing service.
"""

from fastapi import HTTPException, UploadFile, status

from app.utils.logger import logger

# %PDF- is the required header of every PDF (ISO 32000-1, §7.5.2).
PDF_MAGIC = b"%PDF-"

CHUNK_SIZE = 64 * 1024


async def read_pdf_upload(file: UploadFile, max_bytes: int) -> bytes:
    """Read *file* fully, enforcing a size cap and the PDF magic bytes.

    Raises:
        HTTPException: 400 when the file is empty or is not a PDF,
            413 when it exceeds *max_bytes*, 500 when the stream cannot be read.
    """
    chunks: list[bytes] = []
    total = 0

    try:
        while chunk := await file.read(CHUNK_SIZE):
            total += len(chunk)
            if total > max_bytes:
                # Stop reading immediately - the point of the cap is to avoid
                # buffering the rest of the body.
                logger.warning(
                    "Rejected upload '%s': exceeded %d bytes", file.filename, max_bytes
                )
                raise HTTPException(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File exceeds the {max_bytes // (1024 * 1024)} MB limit",
                )
            chunks.append(chunk)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to read upload '%s': %s", file.filename, e)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to read uploaded file"
        ) from e

    content = b"".join(chunks)

    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")

    if not content.startswith(PDF_MAGIC):
        logger.warning("Rejected upload '%s': not a PDF", file.filename)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Uploaded file is not a PDF"
        )

    return content
