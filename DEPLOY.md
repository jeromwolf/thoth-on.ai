# THOTH-ON 온프레미스/폐쇄망 배포 가이드

버전 0.2.0 | WP6-2

---

## 포트 맵

| 서비스       | 호스트 포트 | 컨테이너 포트 | 용도                         |
|------------|-----------|-------------|----------------------------|
| neo4j      | **7475**  | 7474        | Neo4j Browser (HTTP)       |
| neo4j      | **7688**  | 7687        | Bolt (클라이언트/api 내부)   |
| api        | **8468**  | 8468        | FastAPI REST (`/health` 등)|
| console    | **8469**  | 80          | React 조사관 콘솔 (nginx)   |

> 컨테이너 내부에서 api→neo4j 통신은 `bolt://neo4j:7687` (docker 내부 네트워크).  
> 브라우저에서 콘솔 `/api/*` 요청은 nginx 리버스 프록시가 api 컨테이너로 전달한다.

---

## 사전 요구사항

- Docker 24+ 및 Docker Compose v2 (`docker compose` 명령)
- 권장 호스트 사양: CPU 4코어 이상, RAM 8 GB 이상, 디스크 20 GB 이상
- 폐쇄망: 인터넷 차단 환경에서는 아래 [폐쇄망 이미지 오프라인 반입](#폐쇄망-이미지-오프라인-반입) 절차 선행

---

## 일반 배포 (인터넷 가능)

```bash
# 1. 저장소 클론
git clone <repo-url> thoth-on
cd thoth-on

# 2. 환경 변수 설정
cp .env.example .env
# .env 에서 NEO4J_PASSWORD, THOTH_PII_SALT 를 반드시 변경
vi .env

# 3. 단일 명령으로 전체 스택 기동
docker compose up -d

# 4. 헬스 확인 (모든 서비스 healthy 대기)
docker compose ps
```

---

## 초기 스키마·데이터 적재

컨테이너 기동 후 neo4j 가 healthy 상태가 되면 아래를 실행한다.

### 방법 A: 호스트 Python 환경 (개발/테스트)

```bash
# Python 가상환경 준비
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# NEO4J_URI 를 호스트→컨테이너 포트(7688)로 임시 지정
export NEO4J_URI=bolt://localhost:7688

make schema   # 스키마 제약·인덱스 (graph/01_schema.cypher)
make synth    # 합성 자동차보험 데이터 생성 (data/synthetic/*.csv)
make seed     # 멱등 적재 (엔티티 해소·가명처리 포함)
```

### 방법 B: 컨테이너 exec (호스트 Python 없을 때)

```bash
# api 컨테이너 내부에서 실행 (NEO4J_URI=bolt://neo4j:7687 이미 설정됨)
docker exec -it thoth-api python -m thoth.db apply graph/01_schema.cypher
docker exec -it thoth-api python -m ingest.synth_generator
docker exec -it thoth-api python -m ingest.loader load data/synthetic
```

### cypher-shell 직접 접속

```bash
docker exec -it thoth-neo4j cypher-shell \
    -u neo4j -p <NEO4J_PASSWORD>
```

---

## 헬스체크 확인

```bash
# 서비스 상태
docker compose ps

# API 헬스
curl http://localhost:8468/health

# 콘솔 접속
open http://localhost:8469    # 브라우저
# 또는: curl -I http://localhost:8469

# Neo4j Browser
open http://localhost:7475    # neo4j / <NEO4J_PASSWORD>
```

---

## 폐쇄망 이미지 오프라인 반입

인터넷이 차단된 온프레미스 환경에서는 인터넷 접근 가능한 머신에서 이미지를
`docker save` 로 추출하고 대상 서버로 전송 후 `docker load` 한다.

### 1단계 — 인터넷 머신에서 이미지 저장

```bash
# 기반 이미지 pull
docker pull neo4j:5.20-community
docker pull python:3.11-slim
docker pull node:20-alpine
docker pull nginx:1.27-alpine

# 애플리케이션 이미지 빌드
cd thoth-on
docker compose build

# 전체 이미지 저장 (단일 tar)
docker save \
    neo4j:5.20-community \
    python:3.11-slim \
    node:20-alpine \
    nginx:1.27-alpine \
    thoth-on-api:latest \
    thoth-on-console:latest \
  | gzip > thoth-on-images.tar.gz
```

> `docker compose build` 후 이미지 이름은 기본적으로  
> `<프로젝트폴더>-api`, `<프로젝트폴더>-console` 형태로 생성된다.  
> `docker images` 로 실제 이름을 확인하고 위 명령에 맞게 조정하라.

### 2단계 — 대상 서버로 전송

```bash
# scp, sftp, USB 등 보안 채널로 전송
scp thoth-on-images.tar.gz user@onprem-server:/data/
```

### 3단계 — 대상 서버에서 이미지 로드

```bash
# 대상 서버
gunzip -c /data/thoth-on-images.tar.gz | docker load

# 확인
docker images | grep -E 'neo4j|thoth|nginx|python|node'
```

### 4단계 — 소스 코드 전송 및 기동

```bash
# 소스를 tar 로 전송 (node_modules, .venv, data 제외 — .dockerignore 참고)
tar --exclude='.git' --exclude='node_modules' --exclude='.venv' \
    --exclude='data' --exclude='logs' \
    -czf thoth-on-src.tar.gz thoth-on/

scp thoth-on-src.tar.gz user@onprem-server:/opt/

# 대상 서버
cd /opt && tar xzf thoth-on-src.tar.gz
cd thoth-on
cp .env.example .env && vi .env   # 비밀번호·솔트 변경

# 이미지가 이미 로드되어 있으므로 빌드 없이 기동
docker compose up -d
```

---

## 운영 팁

### 로그 조회

```bash
docker compose logs -f             # 전체
docker compose logs -f api         # API 만
docker compose logs -f neo4j       # Neo4j 만
```

### 중지 / 재시작

```bash
docker compose stop         # 컨테이너 중지 (볼륨 유지)
docker compose down         # 컨테이너 삭제 (볼륨 유지)
docker compose down -v      # 컨테이너 + 볼륨 삭제 (데이터 전체 삭제 — 주의)
docker compose restart api  # api 서비스만 재시작
```

### 데이터 백업 (Neo4j)

```bash
# neo4j 중지 후 볼륨 백업
docker compose stop neo4j
docker run --rm \
    -v thoth_neo4j_data:/data \
    -v $(pwd)/backup:/backup \
    alpine tar czf /backup/neo4j-data-$(date +%Y%m%d).tar.gz /data
docker compose start neo4j
```

### 환경변수 변경 반영

`.env` 를 수정한 후:

```bash
docker compose up -d --force-recreate api   # api 만 재적용
docker compose up -d                        # 전체 재적용
```

---

## 폐쇄망 LLM (Ollama)

외부 LLM(Anthropic/OpenAI) 없이 온프레미스 LLM 을 쓸 경우:

1. 대상 서버에 [Ollama](https://ollama.com) 설치 (오프라인 패키지 제공)
2. 모델 파일(`.gguf`) 반입 후 `ollama import` 또는 `ollama pull` (로컬 레지스트리)
3. `.env` 설정:
   ```env
   THOTH_LLM_PROVIDER=ollama
   OLLAMA_BASE_URL=http://host.docker.internal:11434
   OLLAMA_MODEL=qwen2.5:14b
   ```
4. `docker compose up -d --force-recreate api`

LLM 없이 규칙 기반 설명만 필요하면 `THOTH_LLM_PROVIDER=mock` 유지.
