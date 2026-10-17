"""Create private Pi5 settings, never print credentials or overwrite a file."""

import argparse
import os
import secrets
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    template = Path(__file__).with_name("pi5.env.example").read_text(encoding="utf-8")
    key, relay_key, password = (secrets.token_urlsafe(48) for _ in range(3))
    template = template.replace("REPLACE_WITH_RANDOM_32_BYTE_OR_LONGER_KEY", key)
    template = template.replace(
        "REPLACE_WITH_DIFFERENT_RANDOM_32_BYTE_OR_LONGER_KEY", relay_key
    )
    template = template.replace("REPLACE_PASSWORD", password)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(template)
    print(
        "PRIVATE_PI5_CONFIG_CREATED: register device and set LOCAL_DEVICE_ID/LOCAL_TENANT_ID before starting edge"
    )


if __name__ == "__main__":
    main()
