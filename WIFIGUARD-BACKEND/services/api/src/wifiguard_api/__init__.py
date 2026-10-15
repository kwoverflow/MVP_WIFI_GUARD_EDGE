"""wifiguard_api — WIFI-GUARD 서비스 API (FastAPI).

- app.py        : 엔트리포인트 (`uvicorn wifiguard_api.app:app`)
- health_app.py : 미들웨어 연결 확인 전용 앱 (클라우드 드라이런 호환)
- main.py       : 현행 로컬 백엔드의 REST 19종 + /ws/live 계약 원본 — 엣지 모듈을 import 하므로 실행 대상이 아니다
"""
