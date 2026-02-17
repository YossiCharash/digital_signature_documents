"""Generate RSA key pair for document signing."""

import argparse
from pathlib import Path

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate_rsa_key_pair(key_size: int = 2048) -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    """Generate an RSA key pair."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=key_size,
        backend=default_backend(),
    )
    public_key = private_key.public_key()
    return private_key, public_key



def save_private_key(key: rsa.RSAPrivateKey, path: Path) -> None:
    """Save private key to file in PEM format."""
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path.write_bytes(pem)
    # Set restrictive permissions (Unix-like systems)
    try:
        import os
        os.chmod(path, 0o600)
    except (OSError, AttributeError):
        pass  # Windows doesn't support chmod the same way


def save_public_key(key: rsa.RSAPublicKey, path: Path) -> None:
    """Save public key to file in PEM format."""
    pem = key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    path.write_bytes(pem)


def main() -> None:
    """Generate RSA key pair and save to files."""
    parser = argparse.ArgumentParser(
        description="Generate RSA key pair for document signing"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="./certificates",
        help="Output directory for key files (default: ./certificates)",
    )
    parser.add_argument(
        "--key-size",
        type=int,
        default=2048,
        choices=[2048, 3072, 4096],
        help="RSA key size in bits (default: 2048)",
    )
    parser.add_argument(
        "--private-key-name",
        type=str,
        default="signing_key.pem",
        help="Private key filename (default: signing_key.pem)",
    )
    parser.add_argument(
        "--public-key-name",
        type=str,
        default="signing_public.pem",
        help="Public key filename (default: signing_public.pem)",
    )

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating {args.key_size}-bit RSA key pair...")
    private_key, public_key = generate_rsa_key_pair(key_size=args.key_size)

    private_key_path = output_dir / args.private_key_name
    public_key_path = output_dir / args.public_key_name

    print(f"Saving private key to {private_key_path}...")
    save_private_key(private_key, private_key_path)

    print(f"Saving public key to {public_key_path}...")
    save_public_key(public_key, public_key_path)

    print("\n✓ Key pair generated successfully!")
    print(f"\nPrivate key: {private_key_path}")
    print(f"Public key: {public_key_path}")
    print("\nTo use the private key, set in your .env file:")
    print(f"  PRIVATE_KEY_PATH={private_key_path}")


if __name__ == "__main__":
    main()
