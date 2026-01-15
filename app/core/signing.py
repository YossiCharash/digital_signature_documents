"""Digital signature implementation using PKCS#7/CMS."""

import json
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID

from app.core.security import (
    CertificateError,
    load_certificate,
    load_private_key,
    verify_certificate_chain,
)
from app.config import settings
from app.models.invoice import DigitalInvoice
from app.utils.logger import logger


class SigningError(Exception):
    """Raised when signing operations fail."""

    pass


class SignatureVerificationError(Exception):
    """Raised when signature verification fails."""

    pass


class DigitalSigner:
    """Digital signature service for invoices."""

    def __init__(
        self,
        cert_path: Optional[Path] = None,
        key_path: Optional[Path] = None,
        key_password: Optional[str] = None,
        algorithm: Optional[str] = None,
    ):
        """Initialize digital signer with certificate and key."""
        self.cert_path = cert_path or settings.get_signing_cert_path()
        self.key_path = key_path or settings.get_signing_key_path()
        self.key_password = key_password or settings.signing_key_password
        self.algorithm = algorithm or settings.signing_algorithm

        self._cert: Optional[x509.Certificate] = None
        self._private_key = None

        self._load_credentials()

    def _load_credentials(self) -> None:
        """Load certificate and private key."""
        try:
            self._cert = load_certificate(self.cert_path)
            password_bytes = (
                self.key_password.encode() if self.key_password else None
            )
            self._private_key = load_private_key(self.key_path, password_bytes)

            # Verify certificate validity
            if not verify_certificate_chain(self._cert):
                raise SigningError("Certificate validation failed")

            logger.info("Digital signer initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize digital signer: {e}")
            raise SigningError(f"Failed to initialize signer: {e}") from e

    def _get_hash_algorithm(self) -> hashes.HashAlgorithm:
        """Get hash algorithm object."""
        algo_map = {
            "SHA256": hashes.SHA256(),
            "SHA384": hashes.SHA384(),
            "SHA512": hashes.SHA512(),
        }
        if self.algorithm not in algo_map:
            raise SigningError(f"Unsupported algorithm: {self.algorithm}")
        return algo_map[self.algorithm]

    def _serialize_invoice(self, invoice: DigitalInvoice) -> bytes:
        """Serialize invoice to deterministic JSON bytes."""
        invoice_dict = invoice.to_json_dict()
        # Use separators to ensure deterministic output
        json_str = json.dumps(invoice_dict, sort_keys=True, separators=(",", ":"))
        return json_str.encode("utf-8")

    def sign_invoice(self, invoice: DigitalInvoice) -> dict:
        """Sign a digital invoice and return signature metadata."""
        try:
            # Serialize invoice to bytes
            invoice_bytes = self._serialize_invoice(invoice)

            # Compute hash
            hash_algo = self._get_hash_algorithm()
            hasher = hashes.Hash(hash_algo)
            hasher.update(invoice_bytes)
            digest = hasher.finalize()

            # Sign the hash
            signature = self._private_key.sign(
                digest,
                padding.PKCS1v15(),
                hash_algo,
            )

            # Get certificate info
            cert_subject = self._cert.subject
            issuer = self._cert.issuer

            # Build signature metadata
            signature_data = {
                "algorithm": self.algorithm,
                "signature": signature.hex(),
                "digest": digest.hex(),
                "certificate": {
                    "subject": {
                        "common_name": cert_subject.get_attributes_for_oid(NameOID.COMMON_NAME)[
                            0
                        ].value
                        if cert_subject.get_attributes_for_oid(NameOID.COMMON_NAME)
                        else None,
                        "organization": cert_subject.get_attributes_for_oid(
                            NameOID.ORGANIZATION_NAME
                        )[0].value
                        if cert_subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)
                        else None,
                    },
                    "issuer": {
                        "common_name": issuer.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
                        if issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
                        else None,
                    },
                    "serial_number": str(self._cert.serial_number),
                    "not_valid_before": self._cert.not_valid_before.isoformat(),
                    "not_valid_after": self._cert.not_valid_after.isoformat(),
                },
                "signed_at": invoice.created_at.isoformat(),
            }

            logger.info(f"Invoice {invoice.invoice_number} signed successfully")
            return signature_data

        except Exception as e:
            logger.error(f"Failed to sign invoice: {e}")
            raise SigningError(f"Signing failed: {e}") from e

    def verify_signature(
        self, invoice: DigitalInvoice, signature_data: dict
    ) -> bool:
        """Verify digital signature of an invoice."""
        try:
            # Serialize invoice
            invoice_bytes = self._serialize_invoice(invoice)

            # Recompute hash
            hash_algo = self._get_hash_algorithm()
            hasher = hashes.Hash(hash_algo)
            hasher.update(invoice_bytes)
            expected_digest = hasher.finalize()

            # Get signature from metadata
            signature_hex = signature_data.get("signature")
            if not signature_hex:
                raise SignatureVerificationError("Signature not found in metadata")

            signature = bytes.fromhex(signature_hex)

            # Get public key from certificate
            public_key = self._cert.public_key()

            # Verify signature
            try:
                public_key.verify(
                    signature,
                    expected_digest,
                    padding.PKCS1v15(),
                    hash_algo,
                )
                logger.info("Signature verification successful")
                return True
            except Exception as e:
                logger.warning(f"Signature verification failed: {e}")
                return False

        except Exception as e:
            logger.error(f"Signature verification error: {e}")
            raise SignatureVerificationError(f"Verification failed: {e}") from e

    def create_signed_invoice(self, invoice: DigitalInvoice) -> dict:
        """Create a complete signed invoice document."""
        signature_data = self.sign_invoice(invoice)
        invoice_dict = invoice.to_json_dict()

        signed_document = {
            "invoice": invoice_dict,
            "signature": signature_data,
            "version": "1.0",
            "format": "digital_invoice_israel",
        }

        return signed_document
