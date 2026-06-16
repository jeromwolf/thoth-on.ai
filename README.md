# THOTH-ON

관계 중심 **보험 사기 탐지 지식그래프 플랫폼**. Neo4j + GDS 기반으로 조직형 사기를 탐지하고, "왜 의심인지"를 관계 경로 + 자연어로 소명한다.

> 제품 사양은 [`PRD.md`](./PRD.md) (v0.2) 참조.

## 빠른 시작

```bash
cp .env.example .env          # 필요시 비밀번호 수정
make install                  # .venv + 의존성
make up && make wait-neo4j    # Neo4j(+GDS) 기동 후 대기
make schema                   # 스키마 제약·인덱스 적용 (WP1)
make synth                    # 합성 자동차보험 데이터 생성 (사기 링 주입)
make seed                     # 멱등 적재 (엔티티 해소·가명처리 포함)
make test-smoke               # 외부 의존성 없는 검증
make test-int                 # Neo4j 통합 검증 (GDS 포함)
```

Neo4j 브라우저: http://localhost:7474 (neo4j / `.env`의 NEO4J_PASSWORD)

## 구조
| 디렉토리 | 역할 | 대응 FR |
|---|---|---|
| `ingest/` | 배치 적재·엔티티 해소·가명처리 | FR-1.x |
| `graph/` | cypher 스키마·온톨로지·시드 | FR-2.x |
| `detection/` | 탐지 쿼리·GDS·스코어링 | FR-3.x |
| `explain/` | LLM 설명(provider 추상화) | FR-5.x |
| `api/` | FastAPI REST | FR-8.x |
| `console/` | React + vis-network 뷰어 | FR-6.x |
| `core/security/` | RBAC·감사 | NFR |
| `tests/` | AC 기반 테스트 | — |

## 개발 워크플로우
각 WP 완료 시 테스트를 실행한다 (`make test`). 마커: `smoke`(빠름) / `integration`(Neo4j 필요).
