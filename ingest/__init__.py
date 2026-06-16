"""ingest — 배치 적재 파이프라인 (FR-1.x).

서브모듈:
    normalize        엔티티 해소 정규화 규칙 (FR-1.2)
    pii              가명처리(salt 해시) 헬퍼 (FR-1.3)
    synth_generator  자동차보험 합성 데이터 생성기 + 사기 링 주입 (WP1-3)
    loader           CSV/Parquet → Neo4j MERGE 멱등 적재 (WP1-4)
"""
from __future__ import annotations
