# THOTH-ON PoC 데모 시나리오

이 문서는 **조사관 1건 처리** 흐름을 단계별 명령과 함께 설명하는 데모 스크립트다.
영업·기술 데모 발표, 내부 검증, 신규 팀원 온보딩에 활용한다.

---

## 사전 준비

```bash
# 1. 환경 변수 설정
cp .env.example .env           # 필요시 NEO4J_PASSWORD 수정

# 2. 의존성 설치
make install                   # Python .venv 생성 + pip install

# 3. Neo4j + GDS 기동
make up && make wait-neo4j     # Docker Compose로 Neo4j 5 + GDS 기동

# 4. 스키마 적용
make schema                    # 유니크 제약·인덱스 생성

# 5. 합성 데이터 생성 + 적재
make synth                     # 자동차보험 합성 데이터 + 사기 링 주입 (~5,000 고객, 15개 링)
make seed                      # 멱등 적재 (엔티티 해소·가명처리 포함)
```

---

## 데모 흐름: 조사관 1건 처리

```
데이터 적재 → 탐지·스코어링 → 케이스 큐 → 소명문 → 관계망 → 판정 → KPI
```

---

### Step 1. 탐지 성능 확인

```bash
.venv/bin/python -m detection.evaluate
```

예상 출력:
```
============================================================
 THOTH-ON WP2 탐지 성능 평가 (주입 링 재현율)
============================================================
  점수 임계치              : 50.0
  ground truth 링 멤버     : 75명
  ground truth 링 개수     : 15개
------------------------------------------------------------
  탐지된 링 멤버 (TP)      : 75명
  오탐 정상 고객 (FP)      : 0명
  적발된 링                : 15/15개
------------------------------------------------------------
  재현율(recall)           : 1.000
  정밀도(precision)        : 1.000
  F1                       : 1.000
  링 단위 재현율           : 1.000
------------------------------------------------------------
  링 멤버 평균 점수        : 88.0 이상
  정상 고객 평균 점수      : 0.0
  점수 분리도(링평균-정상평균): 88.0 이상
============================================================
```

> PoC 합성 데이터 기준: 재현율 1.0 / 오탐 0 / 정상 고객 점수 0

---

### Step 2. API 서버 기동

```bash
.venv/bin/uvicorn api.main:app --port 8468 --reload &
# 또는 포그라운드로 실행 후 별도 터미널에서 나머지 명령 실행
```

헬스체크:
```bash
curl -s http://localhost:8468/health | python3 -m json.tool
```

---

### Step 3. 케이스 큐 생성 (고위험 고객 → 케이스)

```bash
# 케이스 큐 새로고침 (Neo4j 스코어링 후 SQLite에 케이스 생성)
curl -s -X POST http://localhost:8468/cases/refresh \
  -H "X-Role: CLAIMS_ADJUSTER" \
  -H "X-Actor: demo-user" \
  | python3 -m json.tool
```

---

### Step 4. 케이스 큐 조회

```bash
curl -s "http://localhost:8468/cases?limit=5&threshold=50" \
  -H "X-Role: CLAIMS_ADJUSTER" \
  | python3 -m json.tool
```

주요 응답 필드:
- `total`: 고위험 케이스 총수
- `items[].case_id`: 케이스 ID (예: `CASE-C0001`)
- `items[].score`: 리스크 스코어 (0~100)
- `items[].signal_summary`: 탐지 신호 요약 (예: `["SHARED_ACCOUNT", "CROSS_WITNESS"]`)

---

### Step 5. 케이스 상세 + 소명문 조회

```bash
CASE_ID="CASE-C0001"   # Step 4에서 확인한 케이스 ID로 변경

curl -s "http://localhost:8468/cases/${CASE_ID}" \
  -H "X-Role: CLAIMS_ADJUSTER" \
  | python3 -m json.tool
```

응답 주요 필드:
- `score`: 리스크 스코어
- `signals`: 기여 신호 목록 (유형·가중치·공유 키)
- `paths`: 의심 관계 경로 (노드·엣지)
- `explanation.text`: 자연어 소명문 (LLM 생성)
- `explanation.accepted`: 환각 가드 통과 여부

소명문 예시:
```
고객 C0001은 계좌 ACC-042를 C0003, C0007과 공유하며, 사고 현장에서
C0003의 차량을 교차 목격하였습니다. 동일 계좌 공유(+45점) 및 교차 목격(+45점)
신호가 결합되어 리스크 스코어 90점에 도달하였습니다.
```

---

### Step 6. 관계망(그래프) 조회

```bash
CUSTOMER_ID="C0001"   # 케이스의 customer_id

curl -s "http://localhost:8468/graph/customer/${CUSTOMER_ID}" \
  -H "X-Role: CLAIMS_ADJUSTER" \
  | python3 -m json.tool
```

응답 주요 필드:
- `nodes`: 고객·계좌·차량·병원 등 노드 (suspicious 플래그 포함)
- `edges`: 관계 엣지 (의심 경로 하이라이트)
- `node_count` / `edge_count`: 그래프 규모

콘솔(React)에서는 vis-network로 시각화 — `http://localhost:5173`에서 케이스 선택 후 "관계망" 탭 클릭.

---

### Step 7. 담당자 배정 (미배정 → 조사중)

```bash
curl -s -X POST "http://localhost:8468/cases/${CASE_ID}/assign" \
  -H "Content-Type: application/json" \
  -H "X-Role: CLAIMS_ADJUSTER" \
  -H "X-Actor: inspector-01" \
  -d '{"assignee": "inspector-01", "note": "1순위 검토"}' \
  | python3 -m json.tool
```

---

### Step 8. 판정 입력 (조사중 → 사기 확정)

```bash
curl -s -X POST "http://localhost:8468/cases/${CASE_ID}/verdict" \
  -H "Content-Type: application/json" \
  -H "X-Role: CLAIMS_ADJUSTER" \
  -H "X-Actor: inspector-01" \
  -d '{"verdict": "FRAUD", "comment": "계좌 공유 + 교차 목격 패턴 확인. 링 C-ring-01 소속"}' \
  | python3 -m json.tool
```

---

### Step 9. 경영 KPI 조회

```bash
curl -s "http://localhost:8468/kpi" \
  -H "X-Role: RISK_MANAGER" \
  | python3 -m json.tool
```

주요 응답 필드 (실측 + 추정):
```json
{
  "total_cases": 75,
  "high_risk_cases": 75,
  "fraud_verdicts": 1,
  "suspected_rings": 15,
  "score_separation": 88.2,
  "detection_rate_pct": 100.0,
  "daily_throughput_estimate": 12,
  "estimated_savings_krw": 5000000,
  "savings_assumption": "사기판정 1건 × 평균 청구액 500만 원 가정(PoC 합성 데이터 추정)..."
}
```

> **추정 지표 가정**: `daily_throughput_estimate`는 케이스당 20분·1일 4시간 기준.
> `estimated_savings_krw`는 사기 판정 건 × 500만 원(PoC 합성 기준). 실운영 시 재보정 필요.

React 콘솔 KPI 탭에서 그래프·게이지로 시각화 확인.

---

## 전체 명령 요약 (원스톱 실행)

```bash
# 환경 준비 (최초 1회)
cp .env.example .env
make install
make up && make wait-neo4j
make schema && make synth && make seed

# 탐지 성능 확인
.venv/bin/python -m detection.evaluate

# API 기동 (백그라운드)
.venv/bin/uvicorn api.main:app --port 8468 &

# 케이스 큐 생성 → 조회 → 상세 → 판정 → KPI
curl -X POST http://localhost:8468/cases/refresh -H "X-Role: CLAIMS_ADJUSTER" -H "X-Actor: demo"
curl "http://localhost:8468/cases?limit=3" -H "X-Role: CLAIMS_ADJUSTER"
curl "http://localhost:8468/cases/CASE-C0001" -H "X-Role: CLAIMS_ADJUSTER"
curl -X POST "http://localhost:8468/cases/CASE-C0001/verdict" \
  -H "Content-Type: application/json" -H "X-Role: CLAIMS_ADJUSTER" -H "X-Actor: demo" \
  -d '{"verdict":"FRAUD","comment":"링 확인"}'
curl "http://localhost:8468/kpi" -H "X-Role: RISK_MANAGER"

# React 콘솔
cd console && npm run dev   # http://localhost:5173
```

---

## 테스트 실행

```bash
# smoke (Neo4j 불필요)
.venv/bin/pytest -m smoke -q

# 통합 (Neo4j 가동 필요)
.venv/bin/pytest -m integration -q

# 전체
.venv/bin/pytest -q
```

---

## 데이터 규모 (PoC 합성 기준)

| 항목 | 규모 |
|---|---|
| 고객 | ~5,000명 |
| 청구 건수 | ~20,000건 |
| 사기 링 | 15개 (링당 5명) |
| 링 멤버 | 75명 |
| 전체 노드 | ~50,000 |
| 재현율 (임계치 50) | 1.000 |
| 오탐 (FP) | 0 |
| 점수 분리도 | ~88점 |
