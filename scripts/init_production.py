"""Generate independent production secrets without printing or overwriting them."""

import argparse
import os
from pathlib import Path
import re
import secrets


def generate_environment(image: str, output: Path) -> None:
    if not re.fullmatch(r"[a-z0-9][a-z0-9./_-]*(?::v\d+\.\d+\.\d+(?:-rc\.\d+)?|@sha256:[a-f0-9]{64})", image):
        raise ValueError("Use an explicit image version (:v1.2.3 or :v1.2.3-rc.1) or sha256 digest")
    template = Path(__file__).resolve().parents[1] / "deploy" / ".env.example"
    replacements = {
        "IPTABLE_IMAGE": image,
        "POSTGRES_PASSWORD": secrets.token_hex(32),
        "SECRET_KEY": secrets.token_hex(48),
        "INITIAL_ADMIN_PASSWORD": secrets.token_urlsafe(24),
    }
    lines = []
    for line in template.read_text(encoding="utf-8").splitlines():
        key, separator, _ = line.partition("=")
        lines.append(f"{key}={replacements[key]}" if separator and key in replacements else line)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, default=Path("deploy/.env"))
    args = parser.parse_args()
    try:
        generate_environment(args.image, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Cannot create production environment: {exc}\n")
    print(f"Created {args.output} with private permissions. Secrets were not printed.")


if __name__ == "__main__":
    main()
