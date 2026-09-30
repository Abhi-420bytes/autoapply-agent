"""Admin commands.

    python -m app.cli gen-key       # print a new Fernet master key
    python -m app.cli rotate-keys   # re-encrypt every secret under the current MASTER_KEY

Rotation procedure: move the old key into MASTER_KEY_PREVIOUS, put a new key in
MASTER_KEY, restart, run `rotate-keys`, then remove the old key from MASTER_KEY_PREVIOUS.
"""

from __future__ import annotations

import argparse
import sys

from cryptography.fernet import Fernet
from sqlalchemy import Engine, Text, select, type_coerce, update

from app.db.session import get_engine
from app.db.types import EncryptedJSON, EncryptedText
from app.models import Base
from app.security import crypto


def rotate_all(engine: Engine) -> int:
    """Re-encrypt all encrypted columns in place. Returns the number of values rotated."""
    rotated = 0
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            enc_cols = [
                c for c in table.columns if isinstance(c.type, EncryptedText | EncryptedJSON)
            ]
            if not enc_cols:
                continue
            pk = list(table.primary_key.columns)
            for col in enc_cols:
                # type_coerce(..., Text) reads/writes the raw ciphertext, bypassing decryption.
                raw = type_coerce(col, Text).label("raw")
                rows = conn.execute(select(*pk, raw).where(col.is_not(None))).all()
                for row in rows:
                    new_ct = crypto.rotate(row.raw)
                    where = [c == row._mapping[c.name] for c in pk]
                    conn.execute(
                        update(table).where(*where).values({col.name: type_coerce(new_ct, Text)})
                    )
                    rotated += 1
    return rotated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gen-key", help="print a new Fernet master key")
    sub.add_parser("rotate-keys", help="re-encrypt all secrets with the current MASTER_KEY")
    args = parser.parse_args(argv)

    if args.cmd == "gen-key":
        print(Fernet.generate_key().decode())
    elif args.cmd == "rotate-keys":
        print(f"rotated {rotate_all(get_engine())} encrypted value(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
