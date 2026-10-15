"""Check the exported UI bundle without npm, Vite or project node_modules."""

import argparse
import os
import re
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        ["node", "--max-old-space-size=192", "scripts/serve-pi5.mjs"],
        cwd=args.bundle.resolve(),
        env={**os.environ, "UI_PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        for _ in range(80):
            if process.poll() is not None:
                raise RuntimeError(process.stderr.read().decode())
            try:
                with urlopen(f"http://127.0.0.1:{port}/login", timeout=3) as response:
                    html = response.read().decode()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("UI startup timed out")
        assert "<html" in html
        assets = re.findall(r'(?:src|href)="(/assets/[^" ]+)"', html)
        assert assets, "SSR returned no frontend assets"
        for path in assets[:4]:
            with urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
                assert response.status == 200 and response.read()
        print(f"PI5_EXPORTED_UI_PASS assets_checked={min(4, len(assets))}")
    finally:
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)
        process.stderr.close()


if __name__ == "__main__":
    main()
