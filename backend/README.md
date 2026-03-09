# Backend (FastAPI)

## Quick Start
1. Create venv and install:
   - `uv venv`
   - `UV_CACHE_DIR=/tmp/uv-cache uv pip install --python .venv/bin/python -r requirements.txt`
2. First install:
   - 推荐从前端 `/setup` 页面完成数据库连接测试、schema 初始化和首个管理员创建
   - 只有在不走 `/setup` 时，才手动复制 `config.example.yaml` 为本地 `config.yaml`
3. Run DB migrations (recommended before first run):
   - `UV_CACHE_DIR=/tmp/uv-cache uv run --python .venv/bin/python python scripts/run_db_migrations.py`
4. Run API:
   - `uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8002`
5. Run ingestion stub:
   - `uv run python ingestion.py`
6. Sync capture metadata to DB (optional manual trigger):
   - `uv run python scripts/sync_capture_to_db.py --scan-limit 5000 --show-count`
   - default behavior deletes local image/sidecar files after successful DB write
   - keep local files: `uv run python scripts/sync_capture_to_db.py --scan-limit 5000 --keep-local-images`
7. Run offline search evaluation baseline:
   - edit `data/search_eval/queries.template.json`
   - `UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/evaluate_search.py --dataset data/search_eval/queries.template.json`
   - optional full JSON report: `UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/evaluate_search.py --dataset data/search_eval/queries.template.json --output-json data/search_eval/report.json`
8. Backfill search rerank features for historical rows:
   - `UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/backfill_search_features.py --batch-size 100 --limit 1000`
   - dry run first: `UV_CACHE_DIR=/tmp/uv-cache uv run python scripts/backfill_search_features.py --dry-run`

## Phase-1 Ready Features
- `/api/v1/auth/login` (database users)
- `/api/v1/status` (JWT + RBAC)
- `/api/v1/search` (JWT + multipart contract)
- `/api/v1/search/{query_id}/feedback`
- `/api/v1/alerts`
- `/api/v1/cameras` (camera runtime status)
- `/api/v1/cameras/configs` (camera config CRUD-like replace)
- `/api/v1/cameras/{camera_id}/test`
- `/api/v1/cameras/{camera_id}/recognize`
- `/api/v1/cameras/{camera_id}/snapshot?draw_boxes=true`
- `/api/v1/capture/status|start|stop|config|logs|recent|query|photo|sync-db` (capture process + config console + metadata db sync/query + photo preview)
  - `POST /api/v1/capture/sync-db?scan_limit=5000&purge_local_images=true`

## CORS (Web Dev)
- Configure `app.cors_allow_origins` in `config.yaml` (see `config.example.yaml`)
- Default allows `http://127.0.0.1:5173` and `http://localhost:5173`

## Account Bootstrap
- 登录只认数据库账号。
- 首次接入可执行：
  - `UV_CACHE_DIR=/tmp/uv-cache uv run --python .venv/bin/python python scripts/bootstrap_users.py --username <用户名> --password '<密码>' --role admin`

## Release hygiene
- `config.yaml` 是本地部署配置，不应携带真实数据库地址、密码或 JWT 密钥进入公开仓库
- 发布前先在项目根目录运行：
  - `./scripts/dev_stack.sh audit-release`
  - `./scripts/dev_stack.sh sanitize-release`
- `sanitize-release` 只清理日志、metadata 和采集配置审计，不会改动本地图片或 `backend/config.yaml`
- `backend/data/capture_runtime_configs/*.yaml` 现在仅保留脱敏副本；采集 worker 的运行时配置通过 stdin 下发，不再把摄像头账号密码落盘

## DB Schema
- SQL file: `app/db/schema.sql`
- Requires PostgreSQL + pgvector extension.
- Startup now auto-runs SQL migrations from:
  - `app/db/schema.sql`
  - `app/db/migrations/*.sql`

## Camera recognition notes
- Current detector baseline uses OpenCV HOG for fast bring-up on CPU.
- Replace `app/core/detector.py` with YOLO ONNX implementation in next iteration without changing API contract.

## Offline Search Evaluation
- dataset template: `data/search_eval/queries.template.json`
- supported query sources:
  - `meta_id`
  - `image_path`
  - local `file` path relative to the dataset JSON file
- each case can define:
  - query source
  - optional search filters
  - expected `target_keys` and/or `track_ids`
- report summary includes:
  - `top1_hit_rate`
  - `top5_hit_rate`
  - `top10_hit_rate`
  - `mean_reciprocal_rank`
  - `avg_latency_ms`
  - `p95_latency_ms`

## Search Feature Backfill
- script: `scripts/backfill_search_features.py`
- purpose:
  - fill historical `capture_metadata` rows with:
    - `upper_embedding`
    - `lower_embedding`
    - `face_embedding`
    - `face_confidence`
    - `upper_color_conf`
    - `lower_color_conf`
- recommended order:
  1. run DB migrations
  2. backfill historical rows
  3. run offline evaluation baseline
