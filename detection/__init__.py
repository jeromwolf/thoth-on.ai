"""WP2 탐지 코어 — 공유 엔티티·핫스팟·crash-for-cash 순환 탐지 + 리스크 스코어링.

모듈:
    detect    : Q1~Q3 탐지 쿼리 실행 래퍼
    scoring   : Q1~Q3 신호 가중합 리스크 스코어링 (설명 신호 부착)
    evaluate  : ground truth 기준 재현율/정밀도/분리도 측정 CLI
"""
from __future__ import annotations
