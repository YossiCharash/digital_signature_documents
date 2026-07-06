"""Generate an RSA signing key for the document-signing service.

Usage:
    python scripts/generate_keys.py            # writes certificates/signing_key.pem
    python scripts/generate_keys.py --out path/to/key.pem
    python scripts/generate_keys.py --bits 4096

Then point the app at it in .env:
    PRIVATE_KEY_PATH=./certificates/signing_key.pem
"""

import argparse
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate(out: Path, bits: int) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pem)
    print(f"Wrote {out} ({bits}-bit RSA, {len(pem)} bytes)")
    print("Set in .env:  PRIVATE_KEY_PATH=./" + out.as_posix())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate an RSA signing key (PEM).")
    parser.add_argument(
        "--out", type=Path, default=Path("certificates/signing_key.pem"),
        help="Output PEM path (default: certificates/signing_key.pem)",
    )
    parser.add_argument(
        "--bits", type=int, default=2048, help="RSA key size in bits (default: 2048)"
    )
    args = parser.parse_args()
    generate(args.out, args.bits)
