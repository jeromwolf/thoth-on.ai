# THOTH-ON

관계 중심 **보험 사기 탐지 지식그래프 플랫폼**. Neo4j + GDS 기반으로 조직형 사기를 탐지하고, "왜 의심인지"를 관계 경로 + 자연어로 소명한다.

> 제품 사양은 [`PRD.md`](./PRD.md) (v0.2) 참조. PoC 데모 시나리오는 [`docs/DEMO.md`](./docs/DEMO.md) 참조.

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

API 서버 기동:
```bash
.venv/bin/uvicorn api.main:app --port 8468 --reload
# → http://localhost:8468/docs (OpenAPI)
```

React 콘솔 기동:
```bash
cd console && npm run dev
# → http://localhost:5173
```

## 전체 아키텍처

```
[합성 데이터 (CSV)]
        │ ingest/ (엔티티 해소·가명처리)
        ▼
[Neo4j 5 + GDS 지식그래프]
   ├── graph/01_schema.cypher   # 제약·인덱스
   ├── graph/02_seed.cypher     # 사기 링 주입
   └── detection/               # Q1~Q5 탐지·스코어링
           │
           ├── WP3: GDS 파이프라인 (WCC/Louvain/PageRank/Degree)
           │
           └── detection/scoring.py  →  리스크 스코어 (0~100)
                        │
                        ▼
              [SQLite 케이스 저장소 (core/cases.py)]
                케이스 큐 · 상태 머신 · 판정 피드백
                        │
              ┌─────────┴──────────┐
              ▼                    ▼
   [FastAPI REST (api/)]    [explain/ LLM 소명문]
   /cases  /graph  /kpi      provider 추상화
              │                    │
              ▼                    ▼
   [React 콘솔 (console/)]   환각 가드 (FR-5.2)
   케이스 큐 · 관계망 · KPI 대시보드
```

**스택:** Python 3.10+ / FastAPI / Neo4j 5 + GDS / SQLite(케이스 PoC) / React 18 + Vite + vis-network / RBAC + 감사 (`core/security/`)

## 구조
| 디렉토리 | 역할 | 대응 FR |
|---|---|---|
| `ingest/` | 배치 적재·엔티티 해소·가명처리 | FR-1.x |
| `graph/` | cypher 스키마·온톨로지·시드 | FR-2.x |
| `detection/` | 탐지 쿼리·GDS·스코어링 | FR-3.x |
| `explain/` | LLM 설명(provider 추상화) | FR-5.x |
| `api/` | FastAPI REST (RBAC 미들웨어 포함) | FR-8.x |
| `console/` | React + vis-network 뷰어 | FR-6.x |
| `core/security/` | RBAC·감사 | NFR |
| `tests/` | AC 기반 테스트 | — |
| `docs/` | 운영 문서·데모 시나리오 | — |

## 탐지 실행 (WP2)
```bash
python -m detection.evaluate   # 주입 링 재현율·정밀도·점수 분리도 측정
python -m detection.feedback   # 조사관 판정→운영 라벨 재학습(피드백 루프) + provenance 리포트
```
> 피드백 루프: 조사관 판정(FRAUD/NORMAL)을 운영 라벨로 환원해 모델을 재학습한다(`POST /detection/retrain`, 콘솔 재학습 패널). **라벨(y)만 조작·피처(X) 불변으로 누수 차단**, baseline(ground truth)과 feedback(판정 라벨)은 서로 다른 라벨 집합이라 delta는 참고치임을 명시한다.

## 진행 현황
- ✅ **WP0** 부트스트랩 (인프라·RBAC/감사 골격·테스트 러너)
- ✅ **WP1** 데이터 코어 (스키마·합성데이터·멱등적재·엔티티해소·가명처리)
- ✅ **WP2** 탐지 코어 (공유엔티티·핫스팟·crash-for-cash·리스크 스코어링) — 합성데이터 기준 재현율 1.0 / 오탐 0
- ✅ **WP3** GDS 파이프라인 (WCC/Louvain 커뮤니티 + Degree/PageRank 중심성) — 사기 링 15/15 단일 커뮤니티 응집
- ✅ **WP4** 케이스 관리 + 설명가능성 (큐·상태전이·경로 첨부·자연어 소명문·환각 가드, LLM provider 추상화) + **판정 피드백 재학습**(조사관 판정→운영 라벨→재학습 루프)
- ✅ **WP5** API/콘솔/시각화 (FastAPI REST + React + vis-network 관계망 뷰어)
- ✅ **WP6** 운영화 (보안·감사·배포·경영 KPI 대시보드) — 적발률·절감액·처리량 KPI 확장 완료

### 탐지 성능 (현실적 합성데이터 기준)
룰 단독의 한계를 그래프·ML로 메우는 과정을 수치로 검증:

| 단계 | F1 | AUC | 비고 |
|---|---|---|---|
| 룰 단독 | 0.15 | 0.88 | 출발점(오탐 426명) |
| + 룰 정교화 | 0.75 | — | 정밀도 0.91, 오탐 7명 |
| + GDS 임베딩 | 0.81 | 0.90 | 은밀한 수법(weak 0.14→0.77) |
| **+ ML 앙상블** | **0.90** | **0.975** | 자동 가중치, 피처중요도 1위=PageRank |

**한국 실제 수법 기반(금감원·KIRI 적발 패턴):** 허위입원 조직형·고의충돌 공모·정비비 과다청구·운전자 교체·설계사 개입 5종을 합성에 주입. 구조가 명확한 실제 수법은 룰만으로도 F1 0.93, 은밀한 운전자 교체(driver_swap)는 GDS 임베딩으로 0.21→1.0.

> ⚠️ 합성데이터 기준 수치 — 실데이터 검증은 NDA 파일럿 단계 필요.

### 듀얼 레이어 — 조직형 + 개인형 사기 모두 탐지
현실 사기의 76%는 공모 네트워크 없는 개인 단발 사기 → 그래프만으론 못 잡음(0.2%). 속성 ML 레이어로 보완:

| 사기 유형 | 그래프 단독 | 속성 ML | **듀얼 결합** |
|---|---|---|---|
| 조직형(공모) | 0.97 | 0.65 | **0.98** |
| 개인형(단발) | 0.01 | 0.92 | **0.92** |
| 통합 | 0.24 | 0.86 | **0.94** |

- **개인형 속성 ML은 캐글 실데이터(15,420건)로 검증** — ROC-AUC **0.822**(무작위 3.8배), 누수차단 5-fold OOF.
- 핵심 신호: 배상책임·본인과실·주소변경 직후 청구·자기부담금.
- 유형 라벨(ORGANIZED/INDIVIDUAL/BOTH)로 케이스 구분. precision(오탐)이 다음 개선 과제.

## 개발 워크플로우
각 WP 완료 시 테스트를 실행한다 (`make test`). 마커: `smoke`(빠름) / `integration`(Neo4j 필요).
