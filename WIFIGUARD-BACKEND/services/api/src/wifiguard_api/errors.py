"""API 오류 포맷 — `{"detail": "메시지", "code": "EMAIL_TAKEN", "fields": {...}?}`.

레거시 main.py(FastAPI 기본 `detail`)와 호환되면서 프론트가 분기할 수 있는 `code` 를 추가한다.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str, fields: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.fields = fields

    def to_response(self) -> JSONResponse:
        body: dict[str, Any] = {"detail": self.detail, "code": self.code}
        if self.fields:
            body["fields"] = self.fields
        headers = {"WWW-Authenticate": "Bearer"} if self.status == 401 else None
        return JSONResponse(status_code=self.status, content=body, headers=headers)


def unauthorized(code: str = "UNAUTHORIZED", detail: str = "인증이 필요합니다.") -> ApiError:
    return ApiError(401, code, detail)


def forbidden(detail: str = "권한이 없습니다.") -> ApiError:
    return ApiError(403, "FORBIDDEN", detail)


def not_found(what: str = "리소스") -> ApiError:
    return ApiError(404, "NOT_FOUND", f"{what}를 찾을 수 없습니다.")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return exc.to_response()

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields: dict[str, str] = {}
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
            fields[loc or "_"] = err.get("msg", "invalid")
        return JSONResponse(
            status_code=422,
            content={"detail": "입력값이 올바르지 않습니다.", "code": "VALIDATION_ERROR", "fields": fields},
        )

    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "요청을 처리할 수 없습니다."
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": detail, "code": "HTTP_ERROR"},
            headers=getattr(exc, "headers", None),
        )
