"""스키마 스냅샷 2종 → 프론트 코드젠 입력.

    packages/contracts/openapi.json          REST  (`bun run api:types`)
    packages/contracts/realtime.schema.json  /ws/live 프레임

    uv run python tools/export_openapi.py            # 둘 다 생성
    uv run python tools/export_openapi.py --check    # 커밋본과 다르면 종료코드 1 (CI 용)

OpenAPI 는 WebSocket 을 표현하지 못하므로 실시간 프레임은 **별도 JSON Schema** 로 낸다.
이게 없으면 프론트가 `src/lib/backend.ts` 처럼 손으로 타입을 미러링하게 되고, 서버 계약과
조용히 갈라진다 — 실제로 그렇게 갈라져 있었다(재실 필드가 명세는 필수인데 구현은 전부 optional).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "packages" / "contracts"
DEFAULT_OUT = CONTRACTS / "openapi.json"
DEFAULT_RT_OUT = CONTRACTS / "realtime.schema.json"


def _rest_spec() -> str:
    from wifiguard_api.app import app

    return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _realtime_spec() -> str:
    """서버→클라이언트 이벤트와 클라이언트→서버 프레임을 한 문서에 담는다."""
    from pydantic import TypeAdapter

    from wifiguard_contracts import realtime as rt

    server = TypeAdapter(rt.ServerEvent).json_schema(ref_template="#/$defs/{model}")
    client = TypeAdapter(rt.ClientFrame).json_schema(ref_template="#/$defs/{model}")
    defs: dict = {**server.pop("$defs", {}), **client.pop("$defs", {})}

    doc = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "WIFI-GUARD /ws/live",
        "description": (
            "WebSocket 프레임 계약. protocol_version "
            f"{rt.PROTOCOL_VERSION}. "
            "presence/fall 블록의 부재는 '감지 미동작'이며 '이상 없음'이 아니다."
        ),
        "x-protocol-version": rt.PROTOCOL_VERSION,
        "x-close-codes": {
            "unauthorized": rt.WS_CLOSE_UNAUTHORIZED,
            "token_expired": rt.WS_CLOSE_TOKEN_EXPIRED,
        },
        "$defs": defs,
        "properties": {"serverEvent": server, "clientFrame": client},
    }
    return json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _emit(out: Path, text: str, label: str, check: bool) -> int:
    if check:
        current = out.read_text(encoding="utf-8") if out.exists() else ""
        if current != text:
            print(f"{label} 이 최신이 아닙니다: {out}", file=sys.stderr)
            return 1
        print(f"{out.name} up to date")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--realtime-out", type=Path, default=DEFAULT_RT_OUT)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    rest, realtime = _rest_spec(), _realtime_spec()
    rc = _emit(args.out, rest, "openapi.json", args.check)
    rc |= _emit(args.realtime_out, realtime, "realtime.schema.json", args.check)
    if not args.check and rc == 0:
        spec = json.loads(rest)
        paths = len(spec.get("paths", {}))
        schemas = len(spec.get("components", {}).get("schemas", {}))
        frames = len(json.loads(realtime)["$defs"])
        print(f"wrote {args.out} ({paths} paths, {schemas} schemas)")
        print(f"wrote {args.realtime_out} ({frames} frame types)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
