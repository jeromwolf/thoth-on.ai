# 캐글 실데이터 적용 결과 — THOTH-ON (fraud_oracle.csv)

데이터셋: Oracle *Vehicle Insurance Fraud Detection* (CC0). 자동차보험 청구
**15,420건**, 라벨 `FraudFound_P`(0/1), 33개 컬럼.

본 문서는 두 작업의 실측 결과를 정직하게 정리한다.
- **①** 캐글 실분포를 합성 generator 의 prior 로 반영 → 재측정.
- **②** 캐글 단건 청구를 격리된 이종 그래프로 변환(PoC) → 공유 허브-사기 연관 + 한계.

재현:
```
.venv/bin/python -m ingest.kaggle_analysis            # ① 분포 추출 → JSON + 자동표
.venv/bin/python -m ingest.synth_generator            # ① prior 반영 합성 재생성
.venv/bin/python -m thoth.db reset && .venv/bin/python -m ingest.loader load data/synthetic
.venv/bin/python -m detection.gds_pipeline run && .venv/bin/python -m detection.embedding run
.venv/bin/python -m detection.evaluate --threshold 50 --claim-summary --embedding --compare-embedding
.venv/bin/python -m ingest.kaggle_graph load          # ② 격리 그래프 적재 + 허브 분석
```
자동 생성된 컬럼별 상세 분포표는 [`kaggle_distributions_auto.md`](./kaggle_distributions_auto.md)
및 [`data/kaggle/real_distributions.json`](../data/kaggle/real_distributions.json) 참조.

---

## ① 실제 분포 추출 → 합성 반영

### 1. 추출한 실제 분포(핵심)

- **전체 사기율**: **5.99%** (사기 923건 / 전체 15,420건).
- **컬럼별 조건부 사기율**(사기 신호가 강한 축):

| 축 | 카테고리 | 비중(share) | 조건부 사기율 |
|---|---|---:|---:|
| **Fault** | Policy Holder(본인과실) | 72.8% | **7.9%** |
| | Third Party(제3자) | 27.2% | 0.9% |
| **BasePolicy** | All Perils | 22.7%* | **10.2%** |
| | Collision | 41.8%* | 7.3% |
| | Liability | 35.5%* | 0.7% |
| **VehicleCategory** | Utility | 2.5% | **11.2%** |
| | Sedan | 62.7% | 8.2% |
| | Sport | 34.8% | 1.6% |
| **AccidentArea** | Rural(농촌) | 10.4% | **8.3%** |
| | Urban(도시) | 89.6% | 5.7% |

\* BasePolicy 비중은 generator prior 로 쓰는 marginal(=PolicyType 파생 비중)을 사용해
정규화한 값이다(원시 컬럼 분포는 자동표 참조). 조건부 사기율 서열은 동일.

- **기타 유의미 신호**: `Age` 사기평균 39.6세 vs 정상 40.8세(사기는 약간 젊음),
  `PastNumberOfClaims` 무사고(none)군 7.8% > 다발군, `AgentType` Internal 1.7% vs
  External 6.0%, `AddressChange_Claim`/`Make`(Accura 12.5%) 등. 전체는 자동표 참조.

### 2. 합성 generator 에 prior 반영

`ingest/synth_generator.py` 가 `data/kaggle/real_distributions.json` 을 prior 로 로드한다
(파일 부재 시 BAKED 스냅샷 fallback — 테스트 재현성 보장).

- **속성 4축 부여**: 모든 합성 청구에 `vehicle_category / accident_area / fault /
  base_policy` 를 캐글 marginal(share)로 샘플링해 부착. 사기 청구는 본인과실(Policy
  Holder) 비중을 높여 실데이터 신호와 정합.
- **배경 단발성(비링) 사기 주입**: 한국 수법 5종 링(공모형)은 그대로 두고, 정상
  청구 일부를 그 청구 속성에 연동된 **캐글 실 조건부 사기확률**(naive-Bayes lift)로
  사기 라벨로 뒤집어 **청구 단위 전체 사기율을 6.00%(캐글 5.99%)로 현실화**.
- **격리된 전용 RNG**: 속성/배경사기 샘플링은 `seed+1` 전용 RNG 로 처리해 기존
  생성 스트림(링 구조·응집도)을 **전혀 교란하지 않는다**. seed 고정(42).

**검증 — 합성 산출 조건부 사기율이 캐글과 정합**(청구 단위, seed=42):

| 축 | 캐글(실) | 합성(재현) |
|---|---|---|
| Fault: Policy Holder / Third Party | 7.9% / 0.9% | 7.8% / 1.0% |
| VehicleCategory: Utility / Sedan / Sport | 11.2 / 8.2 / 1.6% | (서열 동일) |
| BasePolicy: All Perils / Collision / Liability | 10.2 / 7.3 / 0.7% | 9.0 / 7.1 / 2.0% |
| AccidentArea: Rural / Urban | 8.3 / 5.7% | (Rural>Urban 동일) |

### 3. 재측정 — 재생성·재적재 후 detection.evaluate

규모: Customer 6,050 · Claim 20,290 · ring 멤버 290명 / 30링(기존과 동일).
청구 단위 사기: **1,217건(6.00%)** = ring 290 + 배경 단발성 **927**.

**(a) 링 단위 룰 기반 탐지(임계 50)** — 현실화 전후가 동일:

| 지표 | 현실화 전(베이스라인) | 현실화 후 |
|---|---:|---:|
| recall | 0.914 | 0.914 |
| precision | 0.946 | 0.946 |
| F1 | 0.930 | 0.930 |
| FPR | 0.0026 | 0.0026 |

→ 전용 RNG 분리 덕에 **링 ground-truth 구조가 보존**되어 링 단위 수치는 불변.
즉 현실화는 "링 탐지 성능을 깎지 않으면서" 배경 분포만 현실화했다(정직한 추가).

**(b) 청구 단위 사기율 현실화의 진짜 영향 — 정밀도 압박(핵심):**

| 항목 | 값 |
|---|---:|
| 청구 단위 전체 사기 | 1,217건 (6.00%) |
| └ ring 공모형(그래프로 탐지 가능) | 290건 (23.8%) |
| └ 배경 단발성(opportunistic) | 927건 (76.2%) |
| 배경 사기 보유 '비링' 고객 | 849명 |
| 그 중 그래프 탐지가 적중한 수 | **2명 (recall 0.2%)** |

→ **현실의 자동차보험 사기 대다수(76%)는 공모 네트워크가 없는 개인 단발성
과장청구**다. 그래프 기반 탐지는 ring(공모형)에는 강하지만(recall 0.91) 이
배경 사기는 거의 못 잡는다(0.2%). 즉 사기율을 현실(6%)로 올리면 "구조적으로
그래프로 잡히지 않는 사기"가 다수가 되어, **청구 단위 관점의 재현율 상한이
구조적으로 낮아지고 정밀도 압박이 커진다.** 이는 그래프 탐지의 정직한 한계이며,
배경 사기에는 속성 기반 ML(예: `detection.ml_model`)이 보완재로 필요함을 시사한다.

---

## ② 캐글 단건 → 이종 그래프 변환 PoC

`ingest/kaggle_graph.py` — 캐글 각 행(청구)을 `:KaggleClaim` 노드로 만들고 범주형
컬럼을 **공유 엔티티 노드로 승격**해, 같은 Rep/Make/Agent/PolicyType 등을 공유하는
청구들을 묶는다.

### 격리(기존 한국형 그래프 절대 미훼손)

모든 노드 라벨에 `Kaggle` 접두어, 모든 관계 타입에 `K_` 접두어를 사용한다.
식별자·라벨이 전혀 겹치지 않아 MERGE 충돌이 없고, reset 없이 추가 적재된다.

| 검증 항목 | 값 |
|---|---:|
| 한국형 Customer | 6,050 (불변) |
| 한국형 Claim | 20,290 (불변) |
| 한국형(non-Kaggle) 노드 총수 | **55,865 (기존 스냅샷과 동일)** |
| Kaggle 노드 총수 | 15,474 |
| Kaggle ↔ 한국형 교차 엣지 | **0** |
| KaggleClaim 이 Claim/Customer 라벨 보유 | 0 |

### 적재 규모

| 노드 | 수 | | 엣지 | 수 |
|---|---:|---|---|---:|
| KaggleClaim | 15,420 | | K_HANDLED_BY (→Rep) | 15,420 |
| KaggleRep | 16 | | K_OF_MAKE | 15,420 |
| KaggleMake | 19 | | K_VIA_AGENT | 15,420 |
| KaggleAgent | 2 | | K_OF_POLICY_TYPE | 15,420 |
| KagglePolicyType | 9 | | K_OF_VEHICLE_CATEGORY | 15,420 |
| KaggleVehicleCategory | 3 | | K_IN_AREA | 15,420 |
| KaggleAccidentArea | 2 | | K_OF_BASE_POLICY | 15,420 |
| KaggleBasePolicy | 3 | | **엣지 합계** | **107,940** |

### 공유 허브 ↔ 사기 연관 (어떤 공유 엔티티가 사기 신호?)

전체 base 사기율 5.99% 대비 lift(=축사기율/base) 상위 '공유 허브':

| 공유 엔티티 | 값 | 청구수 | 사기율 | lift |
|---|---|---:|---:|---:|
| KagglePolicyType | Sport - Collision | 348 | 13.8% | **2.30x** |
| KaggleMake | Accura | 472 | 12.5% | 2.09x |
| KagglePolicyType | Utility - All Perils | 340 | 12.1% | 2.01x |
| KaggleVehicleCategory | Utility | 391 | 11.2% | 1.88x |
| KaggleBasePolicy | All Perils | 4,449 | 10.2% | 1.70x |
| KagglePolicyType | Sedan - All Perils | 4,087 | 10.1% | 1.68x |
| KaggleAccidentArea | Rural | 1,598 | 8.3% | 1.39x |

**신호가 거의 없는 축**(공유 허브로 무의미):
- `KaggleRep`(보험 담당자 번호 16종): 모두 사기율 6~7%, lift ≈ 1.0~1.17 — **사기와
  무관**. 즉 "한 Rep 에 사기 청구가 집중"되는 공모 신호는 이 데이터에 없다.
- `KaggleAgent`(External/Internal): External 6.0%(lift 1.01), Internal 1.7%(lift 0.28).
  Internal(내부 직원 처리) 청구가 오히려 사기율이 낮다 — 정상 분별 신호.

**해석**: 이 데이터에서 사기와 연관되는 "공유 허브"는 **상품/차종 속성**
(PolicyType=Sport-Collision, VehicleCategory=Utility, BasePolicy=All Perils)이지,
**처리자/대리점(Rep/Agent) 같은 인적 허브가 아니다.** 즉 캐글 데이터는 *상품 위험*
신호는 담지만 *공모 네트워크* 신호는 담지 않는다.

### ⚠️ 한계 — 인위적 관계임을 분명히

이 그래프의 엣지(`K_OF_MAKE`, `K_HANDLED_BY` 등)는 **"같은 범주 값을 공유"한다는
의미일 뿐, 진짜 공모 네트워크가 아니다.**

- 예: "같은 Make(Honda)" 청구 수천 건이 하나의 `KaggleMake` 노드로 묶이지만, 이는
  단지 같은 제조사일 뿐 **같은 계좌·교차목격·동일 인물 같은 공모 증거가 전혀 아니다.**
- 따라서 위 "공유 허브 사기율/lift"는 **범주 속성과 사기의 약한 상관 신호**일 뿐,
  THOTH-ON 한국형 그래프가 잡는 **공모 링(같은 계좌 수령·상호 교차목격·브로커
  방사형)** 과는 본질이 다르다.
- 캐글 단건 데이터에는 청구 간 **실제 연결(공유 계좌/전화/차량/교차목격)** 정보가
  없으므로, 이 변환으로는 진짜 공모형 사기 탐지를 시연할 수 없다. 공모 탐지 시연은
  한국형 합성 그래프(이번에 보존)로 수행해야 한다.
- 요약: **① 의 가치는 "속성 분포·조건부 사기율의 현실화"이고, ② 의 가치는 "이종
  그래프 변환 PoC + 속성-사기 상관 관찰"이다. ② 를 공모 네트워크 탐지로 과대
  해석하면 안 된다.**

---

## 생성/수정 파일

- `ingest/kaggle_analysis.py` (신규) — 캐글 분포 추출 → JSON + 자동표.
- `ingest/kaggle_graph.py` (신규) — 캐글 → 격리 이종 그래프 PoC + 허브 분석.
- `data/kaggle/real_distributions.json` (신규) — 추출한 실분포.
- `docs/kaggle_distributions_auto.md` (신규) — 자동 생성 분포표(데이터 부록).
- `docs/kaggle_findings.md` (본 문서).
- `ingest/synth_generator.py` (수정) — 캐글 prior 반영 + 배경 단발성 사기 + 속성 4축.
- `ingest/loader.py` (수정) — Claim 에 vehicle_category/accident_area/fault/base_policy 적재.
- `detection/evaluate.py` (수정) — 청구 단위 사기 분포 + 배경 사기 탐지 한계 측정(`--claim-summary`).
