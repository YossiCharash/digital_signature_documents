"""Document signing service using RSA and SHA-256."""

import base64
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from endesive import pdf
from endesive.pdf import cms
from PIL import Image

from app.config import settings
from app.utils.logger import logger


class SigningError(Exception):
    """Raised when signing operations fail."""

    pass


class SigningService:
    """Service for signing documents with RSA and SHA-256."""

    def __init__(self):
        self._private_key = self._load_private_key()
        self._certificate = self._create_self_signed_certificate()

    def _load_private_key(self) -> rsa.RSAPrivateKey:
        """Load RSA private key from file path or environment variable."""
        # Try loading from file path first (more reliable for multi-line PEM)
        if settings.private_key_path:
            try:
                key_path = Path(settings.private_key_path)
                if not key_path.exists():
                    raise SigningError(f"Private key file not found: {key_path}")
                with open(key_path, "rb") as f:
                    private_key_bytes = f.read()
            except Exception as e:
                raise SigningError(
                    f"Failed to read private key file {settings.private_key_path}: {e}"
                ) from e
        elif settings.private_key_pem:
            # Load from environment variable
            private_key_pem = settings.private_key_pem

            # Normalize the PEM string: handle literal \n and actual newlines
            # Replace literal \n with actual newlines if needed
            if "\\n" in private_key_pem and "\n" not in private_key_pem:
                private_key_pem = private_key_pem.replace("\\n", "\n")

            # Ensure proper PEM format with newlines
            private_key_pem = private_key_pem.strip()

            # If the PEM doesn't have proper headers, it might be malformed
            if not private_key_pem.startswith("-----BEGIN"):
                raise SigningError(
                    "Private key PEM does not start with '-----BEGIN'. "
                    "Ensure the key is in PEM format."
                )

            if not private_key_pem.endswith("-----"):
                raise SigningError(
                    "Private key PEM does not end with '-----'. "
                    "Ensure the key is complete and in PEM format."
                )

            # Encode to bytes for cryptography library
            private_key_bytes = private_key_pem.encode("utf-8")
        else:
            raise SigningError("Either PRIVATE_KEY_PEM or PRIVATE_KEY_PATH must be set")

        try:
            private_key = serialization.load_pem_private_key(
                private_key_bytes,
                password=None,
                backend=default_backend(),
            )
            if not isinstance(private_key, rsa.RSAPrivateKey):
                raise SigningError("Private key is not RSA")
            return private_key
        except SigningError:
            raise
        except Exception as e:
            logger.error(f"Failed to load private key: {e}")
            raise SigningError(f"Failed to load private key: {e}") from e

    def sign_document(self, document: bytes) -> dict[str, Any]:
        """
        Sign a document using SHA-256 hash and RSA signature.

        Returns dict with:
        - hash: SHA-256 hash (hex)
        - signature: RSA signature (base64)
        - algorithm: "RSA-SHA256"
        """
        try:
            # Calculate SHA-256 hash
            document_hash = hashlib.sha256(document).digest()
            hash_hex = hashlib.sha256(document).hexdigest()

            # Sign the hash with RSA
            signature = self._private_key.sign(
                document_hash,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )

            # Encode signature as base64
            signature_b64 = base64.b64encode(signature).decode("utf-8")

            return {
                "hash": hash_hex,
                "signature": signature_b64,
                "algorithm": "RSA-SHA256",
            }
        except Exception as e:
            logger.error(f"Document signing failed: {e}")
            raise SigningError(f"Document signing failed: {e}") from e

    def _create_self_signed_certificate(self) -> x509.Certificate:
        """Create a self-signed certificate from the private key."""
        try:
            subject = issuer = x509.Name(
                [
                    x509.NameAttribute(NameOID.COUNTRY_NAME, "IL"),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Digital Signature Service"),
                    x509.NameAttribute(NameOID.COMMON_NAME, "Document Signing Certificate"),
                ]
            )

            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(self._private_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.utcnow())
                .not_valid_after(datetime.utcnow() + timedelta(days=3650))  # 10 years
                .add_extension(
                    x509.SubjectAlternativeName(
                        [
                            x509.DNSName("localhost"),
                        ]
                    ),
                    critical=False,
                )
                .sign(self._private_key, hashes.SHA256(), default_backend())
            )

            return cert
        except Exception as e:
            logger.error(f"Failed to create self-signed certificate: {e}")
            raise SigningError(f"Failed to create certificate: {e}") from e

    def _add_visual_signature(self, pdf_content: bytes) -> bytes:
        """
        Add visual signature stamp to PDF at configured position.

        Returns PDF bytes with visual signature stamp added.
        """
        try:
            # Open PDF
            pdf_doc = fitz.open(stream=pdf_content, filetype="pdf")

            # Load signature image (resolve path so it works from CWD or from project root / Docker)
            signature_path = Path(settings.signature_image_path)
            if not signature_path.is_absolute() and not signature_path.exists():
                # Try project root: parent of app package (e.g. /app when running in Docker)
                _app_root = Path(__file__).resolve().parent.parent.parent
                fallback = _app_root / settings.signature_image_path
                if fallback.exists():
                    signature_path = fallback
            if not signature_path.exists():
                logger.warning(
                    f"Signature image not found at {signature_path}, skipping visual signature"
                )
                return pdf_content

            # Open and get image dimensions
            img = Image.open(signature_path)
            img_width, img_height = img.size

            # Calculate signature dimensions (in points, 72 DPI)
            # Convert pixels to points (assuming 72 DPI for PDF)
            signature_width = settings.signature_width or (
                img_width * 72 / 96
            )  # Default 96 DPI to 72 DPI
            signature_height = settings.signature_height or (img_height * 72 / 96)

            # Determine which pages to add signature to
            if settings.signature_page == -1:
                # Add to all pages
                pages_list: list[int] = list(range(len(pdf_doc)))
            else:
                # Add to specific page
                if settings.signature_page >= len(pdf_doc):
                    logger.warning(
                        f"Signature page {settings.signature_page} exceeds PDF pages, using last page"
                    )
                    pages_list = [len(pdf_doc) - 1]
                else:
                    pages_list = [settings.signature_page]

            # Add signature to each page
            for page_num in pages_list:
                page = pdf_doc[page_num]

                # Get page dimensions
                page_rect = page.rect

                # Calculate position (PDF coordinates: bottom-left is origin)
                # X is from left, Y is from bottom
                x0 = settings.signature_position_x
                y0 = page_rect.height - settings.signature_position_y - signature_height
                x1 = x0 + signature_width
                y1 = y0 + signature_height

                # Create rectangle for image placement
                image_rect = fitz.Rect(x0, y0, x1, y1)

                # Insert image
                page.insert_image(image_rect, filename=str(signature_path))

            # Save to bytes
            pdf_bytes: bytes = pdf_doc.tobytes()
            pdf_doc.close()

            logger.info(
                f"Visual signature stamp added at position ({settings.signature_position_x}, {settings.signature_position_y})"
            )
            return pdf_bytes

        except Exception as e:
            logger.error(f"Failed to add visual signature: {e}")
            # Return original PDF if visual signature fails
            return pdf_content

    def sign_pdf(self, pdf_content: bytes) -> tuple[bytes, dict[str, Any]]:
        """
        Sign a PDF document and embed the signature directly into the PDF.
        First adds visual signature stamp, then applies digital signature.
        The signed PDF will have the same number of pages and content as the original.

        Returns tuple of:
        - signed_pdf_bytes: The PDF with embedded digital signature and visual stamp
        - signature_data: Dict with hash, signature, algorithm
        """
        try:
            # First, add visual signature stamp to PDF
            pdf_with_stamp = self._add_visual_signature(pdf_content)

            # Create the cryptographic signature for metadata (based on PDF with stamp)
            signature_data = self.sign_document(pdf_with_stamp)

            # Prepare TSA configuration if available
            # TSA provides trusted timestamping to verify when the document was signed
            # List of fallback TSA URLs to try if primary fails
            tsa_urls_to_try: list[str] = []
            timestampcredentials = None

            if settings.tsa_url:
                # Add primary TSA URL
                tsa_urls_to_try.append(settings.tsa_url)
                # Add common fallback TSA URLs (free, no auth required)
                fallback_urls = [
                    "http://timestamp.sectigo.com",
                    "http://timestamp.globalsign.com/tsa/r6advanced1",
                    "https://timestamp.digicert.com",  # Try HTTPS version
                ]
                # Only add fallbacks that are different from primary
                for fallback_url in fallback_urls:
                    if fallback_url not in tsa_urls_to_try and fallback_url != settings.tsa_url:
                        tsa_urls_to_try.append(fallback_url)

                # Build credentials dict if username/password are provided
                if settings.tsa_username or settings.tsa_password:
                    timestampcredentials = {}
                    if settings.tsa_username:
                        timestampcredentials["username"] = settings.tsa_username
                    if settings.tsa_password:
                        timestampcredentials["password"] = settings.tsa_password

                logger.info(f"Will try TSA servers in order: {tsa_urls_to_try}")

            # Prepare signature dictionary for endesive
            # Using invisible signature - embedded in PDF but not visible
            # aligned: size in hex bytes (not regular bytes) for signature placeholder
            # TSA adds timestamp token which increases signature size, so we need more space
            # 8192 hex bytes = 4096 regular bytes (enough for signature without TSA)
            # 16384 hex bytes = 8192 regular bytes (enough for signature with TSA)
            aligned_size = 16384 if tsa_urls_to_try else 8192

            dct = {
                "aligned": aligned_size,  # Byte alignment for PDF signature (larger if TSA is used)
                "sigflags": 3,  # Signature flags: 1=signatures_exist, 2=append_only
                "sigflagsft": 132,  # Additional signature flags
                "sigpage": -1,  # -1 for invisible signature (no visual element)
                "contact": "digital-signature@service.local",
                "location": "Digital Signature Service",
                "reason": "Document authentication and integrity verification",
                "signingdate": datetime.utcnow().strftime("D:%Y%m%d%H%M%S+00'00'"),
                "signature": "Digitally Signed Document",
            }

            # Sign the PDF using endesive
            # This embeds the signature into the PDF without modifying content or adding pages
            # cms.sign returns only the signature data, which needs to be appended to the original PDF
            signature_data_bytes = None
            tsa_success = False
            tsa_url_used: str | None = None

            # Try TSA servers in order if configured
            if tsa_urls_to_try:
                for tsa_url_attempt in tsa_urls_to_try:
                    try:
                        logger.info(f"Attempting TSA timestamping with: {tsa_url_attempt}")
                        signature_data_bytes = cms.sign(
                            pdf_with_stamp,
                            dct,
                            self._private_key,
                            self._certificate,
                            [self._certificate],  # CA certificate chain (self-signed)
                            "sha256",  # SHA-256 algorithm
                            timestampurl=tsa_url_attempt,  # TSA URL for trusted timestamping
                            timestampcredentials=timestampcredentials,  # Optional TSA credentials dict with username/password
                        )
                        tsa_success = True
                        tsa_url_used = tsa_url_attempt
                        logger.info(
                            f"Successfully signed with TSA timestamping from: {tsa_url_attempt}"
                        )
                        break
                    except Exception as tsa_error:
                        import traceback

                        error_details = traceback.format_exc()
                        logger.warning(
                            f"TSA timestamping failed with {tsa_url_attempt}: {tsa_error}\n{error_details}"
                        )
                        # Continue to next TSA URL
                        continue

                # If all TSA attempts failed, try signing without TSA
                if not tsa_success:
                    logger.warning(
                        "All TSA servers failed, attempting to sign without TSA timestamping"
                    )
                    try:
                        signature_data_bytes = cms.sign(
                            pdf_with_stamp,
                            dct,
                            self._private_key,
                            self._certificate,
                            [self._certificate],
                            "sha256",
                            timestampurl=None,  # Disable TSA
                            timestampcredentials=None,
                        )
                        logger.info("PDF signed successfully without TSA timestamping (fallback)")
                    except Exception as fallback_error:
                        import traceback

                        error_details = traceback.format_exc()
                        logger.error(
                            f"Signing without TSA also failed: {fallback_error}\n{error_details}"
                        )
                        raise SigningError(
                            f"PDF signing failed with all TSA servers and without TSA. Last error: {fallback_error}"
                        ) from fallback_error
            else:
                # No TSA configured, sign without TSA
                signature_data_bytes = cms.sign(
                    pdf_with_stamp,
                    dct,
                    self._private_key,
                    self._certificate,
                    [self._certificate],
                    "sha256",
                    timestampurl=None,
                    timestampcredentials=None,
                )

            # Append signature to PDF content with visual stamp
            if signature_data_bytes is None:
                raise SigningError("PDF signing produced no output")
            signed_pdf_bytes = pdf_with_stamp + signature_data_bytes

            # If TSA worked, also add a DocTimeStamp (RFC3161) signature.
            # Many PDF viewers use DocTimeStamp to treat signing time as trusted.
            if tsa_success and tsa_url_used and getattr(settings, "tsa_add_doctimestamp", True):
                try:
                    ts_dct = {
                        "aligned": 16384,  # reserve enough space for RFC3161 token
                        "sigflags": 3,
                        "sigflagsft": 132,
                        "sigpage": -1,  # invisible
                        # endesive's timestamp() path expects signingdate when use_signingdate=True (default)
                        "signingdate": datetime.utcnow().strftime("D:%Y%m%d%H%M%S+00'00'"),
                    }
                    logger.info(f"Adding DocTimeStamp using TSA: {tsa_url_used}")
                    ts_bytes = cms.timestamp(
                        signed_pdf_bytes,
                        ts_dct,
                        algomd="sha256",
                        timestampurl=tsa_url_used,
                        timestampcredentials=timestampcredentials,
                    )
                    signed_pdf_bytes = signed_pdf_bytes + ts_bytes
                    logger.info("DocTimeStamp added successfully")
                except Exception as e:
                    # Don't fail signing if timestamping fails; keep the signed PDF.
                    import traceback

                    logger.warning(f"Failed to add DocTimeStamp: {e}\n{traceback.format_exc()}")

            logger.info(
                f"PDF signed successfully with embedded signature. Hash: {signature_data['hash'][:16]}..."
            )
            return signed_pdf_bytes, signature_data

        except Exception as e:
            import traceback

            error_details = traceback.format_exc()
            logger.error(f"PDF signing failed: {e}\n{error_details}")
            raise SigningError(f"PDF signing failed: {e}") from e

    def verify_signature(self, document: bytes, signature: str, hash_value: str) -> bool:
        """
        Verify document signature.

        Returns True if signature is valid, False otherwise.
        """
        try:
            # Calculate hash
            document_hash = hashlib.sha256(document).digest()
            calculated_hash = hashlib.sha256(document).hexdigest()

            # Verify hash matches
            if calculated_hash != hash_value:
                return False

            # Decode signature
            signature_bytes = base64.b64decode(signature)

            # Get public key
            public_key = self._private_key.public_key()

            # Verify signature
            public_key.verify(
                signature_bytes,
                document_hash,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
            return True
        except Exception:
            return False

    def verify_pdf_signature(self, pdf_content: bytes) -> dict[str, Any]:
        """
        Verify PDF digital signature embedded in the PDF file.

        Returns dict with:
        - valid: bool - True if signature is valid
        - hash_ok: bool - True if hash matches
        - signature_ok: bool - True if signature is valid
        - cert_ok: bool - True if certificate is valid
        - message: str - Status message
        """
        try:
            # Get certificate in PEM format for verification
            cert_pem = self._certificate.public_bytes(serialization.Encoding.PEM).decode("utf-8")

            # Verify PDF signature using endesive
            # certificates parameter expects tuple of (cert, chain) or list of certs
            certificates = (cert_pem, cert_pem)  # Self-signed, so cert is also the chain

            hash_ok, signature_ok, cert_ok = pdf.verify(pdf_content, certificates)

            valid = hash_ok and signature_ok and cert_ok

            if valid:
                message = "PDF signature is valid and verified"
            else:
                issues = []
                if not hash_ok:
                    issues.append("hash mismatch")
                if not signature_ok:
                    issues.append("signature invalid")
                if not cert_ok:
                    issues.append("certificate invalid")
                message = f"PDF signature verification failed: {', '.join(issues)}"

            return {
                "valid": valid,
                "hash_ok": hash_ok,
                "signature_ok": signature_ok,
                "cert_ok": cert_ok,
                "message": message,
            }
        except Exception as e:
            logger.error(f"PDF signature verification failed: {e}")
            return {
                "valid": False,
                "hash_ok": False,
                "signature_ok": False,
                "cert_ok": False,
                "message": f"Verification error: {str(e)}",
            }
