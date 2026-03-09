# CameraCodex Public Bundle


## 环境要求


- `uv` 或 Python3.12
- Node.js 20+
- PostgreSQL 14+插件 `pgvector`

## 安装

### 后端

```bash
cd backend
uv venv
UV_CACHE_DIR=/tmp/uv-cache uv pip install --python .venv/bin/python -r requirements.txt
```

### 前端

```bash
cd web
npm install
```

## 首次运行

回到项目根目录执行：

```bash
./scripts/dev_stack.sh start
```

然后访问：

```text
http://127.0.0.1:5173
```

如果系统尚未初始化，会自动进入 `/setup` 页面。  
在页面里填写数据库连接并创建首个管理员账号。

## 本地开发常用命令

```bash
./scripts/dev_stack.sh start
./scripts/dev_stack.sh status
./scripts/dev_stack.sh logs
./scripts/dev_stack.sh stop
./scripts/dev_stack.sh reconfigure
```

## 部署说明

- 后端默认监听 `8002`
- 前端开发默认监听 `5173`
- 生产环境前端可执行：

```bash
cd web
npm run build
```

- 构建产物位于 `web/dist`
- 可用 `Nginx` 或 `Caddy` 托管前端静态文件，并将 API 反代到 `127.0.0.1:8002`

## 发布前提醒

- `backend/config.example.yaml` 是示例，不要改成真实私有配置后再提交
- 摄像头和数据库真实密码应通过 `/setup` 或本地部署环境注入
- 运行后生成的 `.run/`、`backend/config.yaml`、本地抓拍图片都不应提交
