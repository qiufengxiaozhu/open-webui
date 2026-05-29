# RCA Open WebUI — 镜像构建与运维指南

## 镜像体系

本项目采用**两层镜像**结构，加速日常构建：

```
┌──────────────────────────────────┐
│  rca-open-webui:latest           │  ← 应用镜像（~1.4GB）
│  ┌────────────────────────────┐  │
│  │ 前端构建产物 (build/)      │  │
│  │ 后端 Python 代码           │  │
│  │ Skills 文档                │  │
│  └────────────────────────────┘  │
│  基于 ↓                          │
├──────────────────────────────────┤
│  rca-base:latest                 │  ← 基础镜像（~1.3GB）
│  ┌────────────────────────────┐  │
│  │ Python 3.11                │  │
│  │ Node.js 22                 │  │
│  │ 系统工具 (git, curl, jq)   │  │
│  │ 全部 Python 依赖           │  │
│  └────────────────────────────┘  │
└──────────────────────────────────┘
```

## 一、什么时候需要/不需要构建

| 修改内容 | 需要的操作 | 耗时 |
|----------|-----------|------|
| `docs/skills/*.md` 技能文档 | **不需要构建**，重启容器即可 | ~30s |
| `rca.env` 环境变量 | **不需要构建**，重启容器即可 | ~30s |
| Python 后端代码 (`backend/`) | 构建应用镜像 | ~2min |
| 前端代码 (`src/`) | 构建应用镜像 | ~2min |
| `Dockerfile` 自身 | 构建应用镜像 | ~2min |
| `requirements-rca.txt` Python 依赖 | 先构建基础镜像，再构建应用镜像 | ~10min |
| `Dockerfile.base` 系统依赖 | 先构建基础镜像，再构建应用镜像 | ~10min |

## 二、日常操作命令

### 场景 1：只改了技能文档 / 环境变量（不需要构建）

```bash
# 修改完 docs/skills/*.md 或 rca.env 后，重启容器即可
docker compose restart

# 查看日志确认加载了新技能
docker compose logs -f --tail=20
```

### 场景 2：改了 Python/前端代码（构建应用镜像）

```bash
# 一条命令搞定：构建 + 重启
docker compose up -d --build

# 构建完成后清理旧镜像（可选）
docker image prune -f
```

### 场景 3：改了 Python 依赖（构建基础镜像 + 应用镜像）

```bash
# 第一步：构建基础镜像（耗时较长，但很少需要）
docker build -f Dockerfile.base -t rca-base:latest .

# 第二步：构建应用镜像并启动
docker compose up -d --build

# 清理旧镜像
docker image prune -f
```

## 三、常用运维命令

```bash
# 启动（不构建）
docker compose up -d

# 停止
docker compose down

# 查看实时日志
docker compose logs -f

# 查看容器状态
docker compose ps

# 进入容器内部调试
docker compose exec open-webui bash

# 查看应用日志（宿主机直接看）
tail -f logs/app.log

# 过滤 RCA 链路日志
grep '\[RCA:' logs/app.log

# 查看当前镜像列表
docker images
```

## 四、目录挂载说明

以下目录通过 `docker-compose.yaml` 挂载，数据保存在宿主机上：

```
./data/              → /app/backend/data    数据库、上传文件、缓存（必须挂载）
./logs/              → /app/logs            应用日志，每日轮转（必须挂载）
./docs/skills/       → /app/docs/skills     技能文档，支持热更新（建议挂载）
```

## 五、配置管理

所有配置项集中在 `rca.env` 文件中管理：

```bash
# 查看当前配置
cat rca.env

# 配置模板（包含所有可配置项的说明）
cat rca.env.example

# 修改配置后重启生效
vim rca.env
docker compose restart
```

## 六、磁盘清理

```bash
# 清理悬空镜像（每次 build 后建议执行）
docker image prune -f

# 更激进的清理（删除所有未使用的镜像、网络等）
docker system prune -f

# WSL2 环境下回收磁盘空间（需要在 Windows PowerShell 管理员中执行）
# wsl --shutdown
# Optimize-VHD -Path "$env:LOCALAPPDATA\Docker\wsl\disk\docker_data.vhdx" -Mode Full
```

## 七、故障排查

```bash
# 容器启动失败？查看完整日志
docker compose logs --no-log-prefix

# 健康检查失败？手动测试
curl http://localhost:9000/health

# Python 依赖问题？进容器检查
docker compose exec open-webui pip list | grep <package>

# 端口被占用？
lsof -i :9000
```
