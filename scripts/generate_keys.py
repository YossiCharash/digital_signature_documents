"""Script to generate X.509 certificate and private key for digital signing."""

import argparse
from datetime import datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def generate_key_pair(key_size: int = 2048) -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    """Generate RSA key pair."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
    public_key = private_key.public_key()
    return private_key, public_key


def create_self_signed_certificate(
    private_key: rsa.RSAPrivateKey,
    subject_name: str,
    organization: str,
    country: str,
    validity_days: int = 365,
) -> x509.Certificate:
    """Create a self-signed certificate."""
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, country),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
            x509.NameAttribute(NameOID.COMMON_NAME, subject_name),
        ]
    )

    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow())
        .not_valid_after(datetime.utcnow() + timedelta(days=validity_days))
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=False,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )

    return certificate


def save_private_key(private_key: rsa.RSAPrivateKey, path: Path, password: str | None = None) -> None:
    """Save private key to file."""
    encryption = (
        serialization.BestAvailableEncryption(password.encode()) if password else serialization.NoEncryption()
    )

    key_data = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=encryption,
    )

    with open(path, "wb") as f:
        f.write(key_data)

    print(f"Private key saved to {path}")


def save_certificate(certificate: x509.Certificate, path: Path) -> None:
    """Save certificate to file."""
    cert_data = certificate.public_bytes(serialization.Encoding.PEM)

    with open(path, "wb") as f:
        f.write(cert_data)

    print(f"Certificate saved to {path}")


def main():
    """Main function."""
    parser = argparse.ArgumentParser(description="Generate X.509 certificate and private key for signing")
    parser.add_argument("--output-dir", type=str, default="./certificates", help="Output directory")
    parser.add_argument("--subject-name", type=str, default="Invoice Digitalization", help="Subject name")
    parser.add_argument("--organization", type=str, default="Invoice Digitalization Ltd", help="Organization")
    parser.add_argument("--country", type=str, default="IL", help="Country code")
    parser.add_argument("--key-size", type=int, default=2048, help="RSA key size")
    parser.add_argument("--validity-days", type=int, default=365, help="Certificate validity in days")
    parser.add_argument("--password", type=str, help="Private key password (optional)")

    args = parser.parse_args()

    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate key pair
    print("Generating RSA key pair...")
    private_key, public_key = generate_key_pair(key_size=args.key_size)

    # Create certificate
    print("Creating self-signed certificate...")
    certificate = create_self_signed_certificate(
        private_key=private_key,
        subject_name=args.subject_name,
        organization=args.organization,
        country=args.country,
        validity_days=args.validity_days,
    )

    # Save files
    key_path = output_dir / "signing_key.pem"
    cert_path = output_dir / "signing_cert.pem"

    save_private_key(private_key, key_path, args.password)
    save_certificate(certificate, cert_path)

    print("\nCertificate generation complete!")
    print(f"\nCertificate: {cert_path}")
    print(f"Private Key: {key_path}")
    if args.password:
        print(f"Key Password: {args.password}")
    print("\n⚠️  WARNING: Keep the private key secure and never commit it to version control!")


if __name__ == "__main__":
    main()
