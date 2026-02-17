import base64
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import fitz
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from endesive import pdf
from endesive.pdf import cms
from PIL import Image

from app.config import settings
from app.utils.logger import logger


class SigningError(Exception):
    pass


class SigningService:
    def __init__(self):
        self._private_key = self._load_private_key()
        self._certificate = self._create_self_signed_certificate()

    def _load_private_key(self) -> rsa.RSAPrivateKey:
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
            private_key_pem = settings.private_key_pem

            # Normalize the PEM string: handle literal \n and actual newlines
            if "\\n" in private_key_pem and "\n" not in private_key_pem:
                private_key_pem = private_key_pem.replace("\\n", "\n")

            private_key_pem = private_key_pem.strip()

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

        try:
            document_hash = hashlib.sha256(document).digest()
            hash_hex = hashlib.sha256(document).hexdigest()

            signature = self._private_key.sign(
                document_hash,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )

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
            signer_name = (settings.signer_name or "").strip()
            signer_email = (settings.signer_email or "").strip()
            signer_company = (settings.signer_company or "").strip()
            subject = issuer = x509.Name(
                [
                    x509.NameAttribute(NameOID.COUNTRY_NAME, "IL"),
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, signer_company),
                    x509.NameAttribute(NameOID.COMMON_NAME, signer_name),
                    x509.NameAttribute(NameOID.EMAIL_ADDRESS, signer_email),
                ]
            )

            builder = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(self._private_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.utcnow() - timedelta(minutes=5))
                .not_valid_after(datetime.utcnow() + timedelta(days=3650))
            )

            builder = builder.add_extension(x509.BasicConstraints(ca=False, path_length=None), True)

            builder = builder.add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=True,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )

            builder = builder.add_extension(
                x509.ExtendedKeyUsage(
                    [
                        ExtendedKeyUsageOID.EMAIL_PROTECTION,
                    ]
                ),
                critical=False,
            )

            builder = builder.add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.RFC822Name(signer_email),
                        x509.DNSName("localhost"),
                    ]
                ),
                critical=False,
            )

            builder = builder.add_extension(
                x509.SubjectKeyIdentifier.from_public_key(self._private_key.public_key()),
                critical=False,
            )
            builder = builder.add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(self._private_key.public_key()),
                critical=False,
            )

            cert = builder.sign(self._private_key, hashes.SHA256(), default_backend())

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
            pdf_doc = fitz.open(stream=pdf_content, filetype="pdf")

            signature_path = Path(settings.signature_image_path)
            if not signature_path.is_absolute() and not signature_path.exists():
                _app_root = Path(__file__).resolve().parent.parent.parent
                fallback = _app_root / settings.signature_image_path
                if fallback.exists():
                    signature_path = fallback
            if not signature_path.exists():
                logger.warning(
                    f"Signature image not found at {signature_path}, skipping visual signature"
                )
                return pdf_content

            img = Image.open(signature_path)
            img_width, img_height = img.size

            signature_width = settings.signature_width or (img_width * 72 / 96)
            signature_height = settings.signature_height or (img_height * 72 / 96)

            if settings.signature_page == -1:
                pages_list: list[int] = list(range(len(pdf_doc)))
            else:
                if settings.signature_page >= len(pdf_doc):
                    logger.warning(
                        f"Signature page {settings.signature_page} exceeds PDF pages, using last page"
                    )
                    pages_list = [len(pdf_doc) - 1]
                else:
                    pages_list = [settings.signature_page]

            for page_num in pages_list:
                page = pdf_doc[page_num]

                page_rect = page.rect

                x0 = settings.signature_position_x
                y0 = page_rect.height - settings.signature_position_y - signature_height
                x1 = x0 + signature_width
                y1 = y0 + signature_height

                image_rect = fitz.Rect(x0, y0, x1, y1)

                page.insert_image(image_rect, filename=str(signature_path))

            pdf_bytes: bytes = pdf_doc.tobytes()
            pdf_doc.close()

            logger.info(
                f"Visual signature stamp added at position ({settings.signature_position_x}, {settings.signature_position_y})"
            )
            return pdf_bytes

        except Exception as e:
            logger.error(f"Failed to add visual signature: {e}")
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
            pdf_with_stamp = self._add_visual_signature(pdf_content)

            signature_data = self.sign_document(pdf_with_stamp)

            tsa_urls_to_try: list[str] = []
            timestampcredentials = None

            if settings.tsa_url:
                tsa_urls_to_try.append(settings.tsa_url)
                fallback_urls = [
                    "http://timestamp.sectigo.com",
                    "http://timestamp.globalsign.com/tsa/r6advanced1",
                    "https://timestamp.digicert.com",  # Try HTTPS version
                ]
                for fallback_url in fallback_urls:
                    if fallback_url not in tsa_urls_to_try and fallback_url != settings.tsa_url:
                        tsa_urls_to_try.append(fallback_url)

                if settings.tsa_username or settings.tsa_password:
                    timestampcredentials = {}
                    if settings.tsa_username:
                        timestampcredentials["username"] = settings.tsa_username
                    if settings.tsa_password:
                        timestampcredentials["password"] = settings.tsa_password

                logger.info(f"Will try TSA servers in order: {tsa_urls_to_try}")

            aligned_size = 16384 if tsa_urls_to_try else 8192

            dct = {
                "aligned": aligned_size,  # Byte alignment for PDF signature (larger if TSA is used)
                "sigflags": 3,  # Signature flags: 1=signatures_exist, 2=append_only
                "sigflagsft": 132,  # Additional signature flags
                "sigpage": -1,  # -1 for invisible signature (no visual element)
                "contact": (settings.signature_contact or settings.signer_email),
                "location": settings.signature_location,
                "reason": settings.signature_reason,
                "signingdate": datetime.utcnow().strftime("D:%Y%m%d%H%M%S+00'00'"),
                "signature": "Digitally Signed Document",
            }

            signature_data_bytes = None
            tsa_success = False
            tsa_url_used: str | None = None

            if tsa_urls_to_try:
                for tsa_url_attempt in tsa_urls_to_try:
                    try:
                        logger.info(f"Attempting TSA timestamping with: {tsa_url_attempt}")
                        signature_data_bytes = cms.sign(
                            pdf_with_stamp,
                            dct,
                            self._private_key,
                            self._certificate,
                            [self._certificate],
                            "sha256",
                            timestampurl=tsa_url_attempt,
                            timestampcredentials=timestampcredentials,
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
                        continue

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
                            timestampurl=None,
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

            if signature_data_bytes is None:
                raise SigningError("PDF signing produced no output")
            signed_pdf_bytes = pdf_with_stamp + signature_data_bytes

            if tsa_success and tsa_url_used and getattr(settings, "tsa_add_doctimestamp", True):
                try:
                    ts_dct = {
                        "aligned": 16384,
                        "sigflags": 3,
                        "sigflagsft": 132,
                        "sigpage": -1,
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
            document_hash = hashlib.sha256(document).digest()
            calculated_hash = hashlib.sha256(document).hexdigest()

            if calculated_hash != hash_value:
                return False

            signature_bytes = base64.b64decode(signature)

            public_key = self._private_key.public_key()

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
            cert_pem = self._certificate.public_bytes(serialization.Encoding.PEM).decode("utf-8")

            certificates = (cert_pem, cert_pem)

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
