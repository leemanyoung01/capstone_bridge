# 🍽️ TasteBridge (capstone_bridge)

리뷰 데이터로 음식점의 **맛 취향(taste axis)** 을 프로파일링하고, 사용자가 고른 취향에 맞는 음식점을 추천하는 시스템입니다. 텍스트 리뷰와 음식 이미지를 함께 분석하는 **멀티모달 추천**을 지원합니다.

---

## ✨ 주요 기능

- **리뷰 크롤링** — 네이버 플레이스 리뷰 수집 (`Naver_place_crawler.py`)
- **맛 프로파일링** — 규칙 기반 / 의미 기반(BERT) / 멀티모달(CLIP) 취향 축 추출
- **취향 기반 추천** — 사용자가 선택한 맛 축과 cosine 유사도로 음식점 추천 (텍스트·이미지 동적 fusion)
- **웹 프론트엔드** — React 기반 단계별 추천 UI
- **평가 도구** — 추천 랭킹 품질 평가 (`evaluation.py`, `eval/`)

---

## 🗂️ 디렉터리 구조

```
capstone_bridge/
├── app.py                    # Flask API 서버 (추천 엔드포인트)
├── db.py                     # PostgreSQL 접근 계층
├── s3_uploader.py            # 리뷰 이미지 S3 업로드 (선택)
│
├── Food_profiler.py          # 규칙 기반 맛 축 프로파일러
├── Multimodal_profiler.py    # CLIP 이미지 기반 멀티모달 프로파일러
├── semantic_text_profiler.py # 문장 임베딩(BERT) 기반 프로파일러
├── Naver_place_crawler.py    # 네이버 플레이스 리뷰 크롤러
├── text_augment_pipeline.py  # 크롤 → 프로파일 파이프라인 오케스트레이션
│
├── csv_to_json.py            # 데이터 적재 유틸
├── restore_all.py            # DB 복원 유틸
├── migrate_images_to_s3.py   # 이미지 S3 마이그레이션
│
├── evaluation.py             # 추천 랭킹 평가
├── eval_labeling_tool.py     # 평가 라벨링 도구
├── eval/                     # 평가 라벨 데이터 + 결과
│
├── stand/                    # 표준 축·렉시콘·규칙 정의 (런타임 사용)
├── schema.sql                # PostgreSQL 스키마
│
├── frontend/                 # React + Vite 웹 프론트엔드
├── scripts/                  # 배포(.sh) 및 보조 스크립트
│
├── Dockerfile                # 멀티스테이지 빌드 (frontend + python)
├── docker-compose.yml        # 로컬 DB
├── docker-compose.deploy.yml # 데모 배포(app + postgres)
├── appspec.yml / buildspec.yml  # AWS CodeDeploy / CodeBuild
└── docs/                     # 가이드 문서
```

> ⚠️ 대용량 크롤링 원본(`naver_reviews_*.csv`)과 생성 산출물(`*_vectors.json` 등)은
> 재생성 가능하므로 git에 포함하지 않습니다. (`.gitignore` 참고)

---

## 🚀 실행 방법

### 사전 준비
```bash
cp .env.example .env      # 환경변수 설정 (DATABASE_URL 등). git에 커밋 금지
```

### 1) 백엔드 (로컬)
```bash
# DB만 Docker로 띄우기
docker compose up -d

# 의존성 설치 후 서버 실행
pip install -r requirements-server.txt
python app.py
# 또는 gunicorn app:app -b 0.0.0.0:5000 --workers 3
```

### 2) 프론트엔드
```bash
cd frontend
npm install
npm run dev      # 개발 서버
npm run build    # 프로덕션 빌드 → frontend/dist
```

### 3) 전체 Docker 배포 (app + postgres 한 번에)
```bash
docker compose -f docker-compose.deploy.yml up -d --build
```

### 오프라인 추론·크롤링 도구 사용 시
```bash
pip install -r requirements-inference.txt   # torch, open_clip 등 포함
```

---

## 🔐 환경변수 (`.env`)

| 변수 | 설명 | 예시 |
|---|---|---|
| `DATABASE_URL` | PostgreSQL 접속 문자열 | `postgresql://postgres:1234@localhost:5432/tastebridge` |
| `FLASK_DEBUG` | Flask 디버그 모드 | `1`(개발) / `0`(운영) |
| `PORT` | 서버 포트 | `5000` |
| `DEFAULT_KEYWORD` | 기본 추천 키워드 | `삼겹살` |
| `UPLOAD_S3` | 이미지 S3 업로드 여부 | `false` |
| `S3_BUCKET` / `AWS_REGION` | S3 업로드 설정 (선택) | — |

자세한 항목은 `.env.example` 참고.

---

## 🛠️ 기술 스택

- **Backend**: Python 3.11, Flask, Gunicorn, PostgreSQL (psycopg2)
- **ML/추론**: NumPy, pandas, PyTorch, OpenCLIP, sentence-transformers
- **Frontend**: React, Vite
- **Infra**: Docker, AWS EC2 / CodeBuild / CodeDeploy, S3
