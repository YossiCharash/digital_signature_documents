"""Security utilities and certificate management."""

import hashlib
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from app.config import settings
from app.utils.logger import logger


class CertificateError(Exception):
    """Raised when certificate operations fail."""

    pass


def load_certificate(cert_path: Path) -> x509.Certificate:
    """Load X.509 certificate from file."""
    try:
        with open(cert_path, "rb") as f:
            cert_data = f.read()
        cert = x509.load_pem_x509_certificate(cert_data, default_backend())
        logger.info(f"Loaded certificate from {cert_path}")
        return cert
    except Exception as e:
        logger.error(f"Failed to load certificate from {cert_path}: {e}")
        raise CertificateError(f"Failed to load certificate: {e}") from e


def load_private_key(key_path: Path, password: Optional[bytes] = None) -> rsa.RSAPrivateKey:
    """Load RSA private key from file."""
    try:
        with open(key_path, "rb") as f:
            key_data = f.read()

        if password:
            password_bytes = password if isinstance(password, bytes) else password.encode()
        else:
            password_bytes = None

        key = serialization.load_pem_private_key(
            key_data, password=password_bytes, backend=default_backend()
        )

        if not isinstance(key, rsa.RSAPrivateKey):
            raise CertificateError("Private key must be RSA")

        logger.info(f"Loaded private key from {key_path}")
        return key
    except Exception as e:
        logger.error(f"Failed to load private key from {key_path}: {e}")
        raise CertificateError(f"Failed to load private key: {e}") from e


def compute_hash(data: bytes, algorithm: str = "SHA256") -> bytes:
    """Compute hash of data using specified algorithm."""
    algo_map = {
        "SHA256": hashes.SHA256(),
        "SHA384": hashes.SHA384(),
        "SHA512": hashes.SHA512(),
    }

    if algorithm not in algo_map:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}")

    digest = hashlib.new(algorithm.lower(), data).digest()
    return digest


def verify_certificate_chain(cert: x509.Certificate) -> bool:
    """Verify certificate is valid (basic checks)."""
    try:
        # Check if certificate is expired
        from datetime import datetime

        now = datetime.utcnow()
        if cert.not_valid_after < now:
            logger.warning("Certificate has expired")
            return False
        if cert.not_valid_before > now:
            logger.warning("Certificate is not yet valid")
            return False

        logger.info("Certificate validity check passed")
        return True
    except Exception as e:
        logger.error(f"Certificate verification failed: {e}")
        return False
