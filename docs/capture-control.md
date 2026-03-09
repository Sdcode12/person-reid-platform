# Hikvision Capture Control (Web + API)

## 1. 组件关系

- 采集脚本：`hikvision_local_capture/capture_vmd_photos.py`
- 后端控制服务：`backend/app/services/capture_control_service.py`
- 后端接口：`/api/v1/capture/*`
- 前端页面：`web/src/pages/CapturePage.tsx`

流程：

1. Web 调用后端 `capture` API。
2. 后端控制服务启动/停止采集脚本进程，读取并写入 `hikvision_local_capture/config.yaml`。
3. 采集脚本输出日志并写入照片、sidecar JSON、`metadata.jsonl`。
4. Web 读取运行状态、日志、最近抓拍。

## 2. Capture API

- `GET /api/v1/capture/status`：采集进程状态
- `POST /api/v1/capture/start`：启动采集
- `POST /api/v1/capture/stop`：停止采集
- `GET /api/v1/capture/config`：读取采集配置
- `PUT /api/v1/capture/config`：写入采集配置（整体配置）
- `GET /api/v1/capture/logs?limit=220`：读取日志
- `GET /api/v1/capture/recent?limit=80`：读取最近抓拍元数据
- `GET /api/v1/capture/query?...`：按条件查询抓拍（优先读数据库）
- `GET /api/v1/capture/photo?image_path=...&track_id=...`：读取抓拍图片二进制（优先按 `track_id` 查询数据库）
- `POST /api/v1/capture/sync-db?scan_limit=5000`：将 `metadata.jsonl` 同步入 PostgreSQL `capture_metadata`

## 3. Web 可调关键参数

### 抓拍策略

- `camera.burst_count`：每事件目标保存张数
- `camera.max_capture_attempts`：每事件最大尝试抓拍次数
- `camera.burst_interval_ms`：连拍间隔
- `camera.cooldown_seconds`：事件冷却时间

### 检测与质量

- `detector.min_person_confidence`
- `detector.min_person_area_ratio`
- `quality.min_laplacian_var`
- `quality.min_brightness`
- `quality.max_brightness`
- `quality.min_contrast_std`

### 去重与同目标抑制

- `dedup.hamming_threshold`：事件内去重
- `reliability.min_consecutive_vmd_active`
- `reliability.active_window_seconds`
- `reliability.same_target_suppress_seconds`：跨事件同目标抑制窗口
- `reliability.same_target_embedding_similarity`
- `reliability.same_target_hash_hamming_threshold`
- `reliability.same_target_area_ratio_delta`

### 颜色与输出

- `color.enable_normalization`
- `color.target_brightness`
- `color.night_brightness_threshold`
- `output.save_sidecar_json`
- `output.save_metadata_jsonl`
- `logging.verbose_events`

## 4. 调参建议

平衡档（建议默认）：

- `burst_count=4`
- `max_capture_attempts=36`
- `same_target_suppress_seconds=120`

召回优先（怕漏人）：

- `burst_count=5`
- `max_capture_attempts=56`
- `min_person_confidence` 下调
- `same_target_suppress_seconds` 下调到 `60-90`

存储优先（控量）：

- `burst_count=3`
- `max_capture_attempts=24-30`
- `same_target_suppress_seconds` 提高到 `180-300`

## 5. 常见日志解释

- `[person] ... saved=x/y ...`：本次事件已保存 x 张，目标 y 张
- `[event-summary] ... reasons=no_person:.. low_quality:.. same_target_recent:..`：未凑够时的主要原因

重点看：

- `no_person` 高：优先调检测阈值、抓拍尝试次数、补光
- `low_quality` 高：优先调质量阈值、快门/补光
- `same_target_recent` 高：说明抑制策略生效，可按业务缩短或延长窗口
