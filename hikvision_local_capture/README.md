# Hikvision Local Person Photo Capture

订阅海康 `alertStream`，当收到 `VMD active` 时抓拍 JPEG，先做人体检测，只有检测到人时才保存图片。

## 1. 准备配置

修改 `hikvision_local_capture/config.yaml` 里的：
- `camera.host`
- `camera.username`
- `camera.password`
- `detector.*`（可选，控制人体检测阈值）
- `detector.reid_mode`（`auto/onnx/hist`，人体特征提取模式）
- `detector.reid_model_path`（ReID 模型路径，支持 `.onnx` 或 OpenVINO `.xml`）
- `detector.reid_input_width` / `detector.reid_input_height`（ReID 输入尺寸）
- `quality.*`（模糊/亮度/对比度过滤）
- `dedup.hamming_threshold`（相似图去重）
- `reliability.*`（最小连续事件命中门槛）
- `reliability.same_target_*`（同一目标短时间重复保存抑制）
- `color.*`（颜色归一化、夜间阈值）
- `output.save_sidecar_json`（每张图旁边写同名 `.json`）
- `output.save_metadata_jsonl`（是否写全局索引 JSONL）
- `output.metadata_jsonl_name`（全局索引文件名）
- `output.save_to_db`（抓拍后直接写入 PostgreSQL）
- `output.save_local_image`（是否保留本地 JPG）
- `output.local_fallback_on_db_error`（DB异常时回退本地保存）
- `logging.verbose_events`（默认 `false`，只在识别到人时输出）
- `camera.burst_count`（每次 VMD 触发连拍张数）
- `camera.max_capture_attempts`（为了凑够 `burst_count`，最多尝试抓拍次数）
- `camera.burst_interval_ms`（连拍间隔毫秒）

## 2. 运行

```bash
cd backend
UV_CACHE_DIR=/tmp/uv-cache uv run --python .venv/bin/python ../hikvision_local_capture/capture_vmd_photos.py --config ../hikvision_local_capture/config.yaml
```

## 3. 输出

默认保存到（相对于 `config.yaml`）：

```text
hikvision_local_capture/photos/YYYY-MM-DD/<camera_name>_<ip>_ch<channel>/*.jpg
```

同时可输出：

```text
hikvision_local_capture/photos/metadata.jsonl
hikvision_local_capture/photos/YYYY-MM-DD/<...>/*.json
```

## 说明

- 当前只处理 `eventType=VMD` 且 `eventState=active`。
- 非 `VMD active` 事件（例如 `videoloss inactive`）只打印，不保存。
- 没有人体检测结果时不保存。
- ReID 特征提取：优先加载配置模型（支持 ONNX 或 OpenVINO IR），模型不可用时自动回退到颜色直方图特征。
- 质量不过关（模糊/过暗/过亮/低对比）不保存。
- 相似照片（dHash 海明距离阈值内）不保存。
- 事件需满足最小连续命中（`min_consecutive_vmd_active`）才触发抓拍。
- 同一目标在抑制窗口内会被跳过（基于 `dHash + embedding + area_ratio`，仅跨事件抑制）。
- 文件名包含日期和特征：`YYYYMMDD_HHMMSS_xxxxxx_pN_u<upper>_l<lower>_h<head>_hat<yes|no>.jpg`
- 元数据包含：`bbox`、`person_area_ratio`、`quality_score`、`light_level/is_night`、`dedup_group_id`、颜色置信度、`pose_hint`、`body_embedding`。
- 若单次事件未凑够 `burst_count`，会输出一行 `[event-summary]`，显示失败原因统计（`no_person/low_quality/duplicate/...`）。
- 默认写数据库，不保存视频。
- 若 `save_local_image=false`，本地不保留 JPG（可配置 DB 异常时本地回退）。
- `burst_count` 默认 4（目标保存人像张数），`max_capture_attempts` 默认 36（最多抓拍尝试次数）。
