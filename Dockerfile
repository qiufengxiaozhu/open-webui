# ============================================================
#  RCA Open WebUI — 应用镜像
#  基于 rca-base 基础镜像，只做前端构建 + 代码拷贝
#  前置条件: 先构建 base 镜像
#    docker build -f Dockerfile.base -t rca-base:latest .
#  构建应用:
#    docker build -t rca-open-webui:latest .
# ============================================================
# syntax=docker/dockerfile:1
ARG RCA_BASE_IMAGE=rca-base:latest
ARG BUILD_HASH=dev-build

######## 阶段一：构建前端 ########
FROM ${RCA_BASE_IMAGE} AS frontend-build
ARG BUILD_HASH

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --force

COPY . .
ENV APP_BUILD_HASH=${BUILD_HASH}
RUN npm run build

######## 阶段二：组装应用镜像 ########
FROM ${RCA_BASE_IMAGE}

ENV ENV=prod \
    PORT=9000 \
    PYTHONUNBUFFERED=1

ENV OPENAI_API_BASE_URL="" \
    OPENAI_API_KEY="" \
    WEBUI_SECRET_KEY="" \
    SCARF_NO_ANALYTICS=true \
    DO_NOT_TRACK=true \
    ANONYMIZED_TELEMETRY=false \
    VECTOR_DB="" \
    ENABLE_SKILLS_GATE=true

WORKDIR /app/backend

# 拷贝前端构建产物
COPY --from=frontend-build /app/build /app/build
COPY --from=frontend-build /app/package.json /app/package.json

# 拷贝 Skills Gate 技能文档（同时备份到 .default，防止 bind mount 覆盖后丢失）
COPY ./docs/skills/ /app/docs/skills/
COPY ./docs/skills/ /app/docs/skills.default/

# 拷贝后端代码
COPY ./backend .

EXPOSE 9000

HEALTHCHECK CMD curl --silent --fail http://localhost:${PORT:-9000}/health | jq -ne 'input.status == true' || exit 1

ARG BUILD_HASH
ENV WEBUI_BUILD_VERSION=${BUILD_HASH}
ENV DOCKER=true

CMD [ "bash", "start.sh"]
