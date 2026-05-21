# syntax=docker/dockerfile:1
ARG BUILD_HASH=dev-build
ARG UID=0
ARG GID=0

######## WebUI frontend ########
FROM --platform=$BUILDPLATFORM node:22-alpine3.20 AS build
ARG BUILD_HASH

WORKDIR /app

RUN apk add --no-cache git

COPY package.json package-lock.json ./
RUN npm ci --force

COPY . .
ENV APP_BUILD_HASH=${BUILD_HASH}
RUN npm run build

######## WebUI backend ########
FROM python:3.11-slim-bookworm AS base

ARG UID
ARG GID

ENV PYTHONUNBUFFERED=1

ENV ENV=prod \
    PORT=8080

ENV OPENAI_API_BASE_URL="" \
    OPENAI_API_KEY="" \
    WEBUI_SECRET_KEY="" \
    SCARF_NO_ANALYTICS=true \
    DO_NOT_TRACK=true \
    ANONYMIZED_TELEMETRY=false \
    VECTOR_DB=""

WORKDIR /app/backend

ENV HOME=/root
RUN if [ $UID -ne 0 ]; then \
    if [ $GID -ne 0 ]; then \
    addgroup --gid $GID app; \
    fi; \
    adduser --uid $UID --gid $GID --home $HOME --disabled-password --no-create-home app; \
    fi

RUN mkdir -p $HOME/.cache
RUN chown -R $UID:$GID /app $HOME

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git build-essential gcc netcat-openbsd curl jq \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies using trimmed requirements
COPY --chown=$UID:$GID ./backend/requirements-rca.txt ./requirements.txt

ENV UV_LINK_MODE=copy

RUN pip3 install --no-cache-dir uv && \
    uv pip install --system -r requirements.txt --no-cache-dir && \
    mkdir -p /app/backend/data && \
    chown -R $UID:$GID /app/backend/data/

# Copy built frontend files
COPY --chown=$UID:$GID --from=build /app/build /app/build
COPY --chown=$UID:$GID --from=build /app/package.json /app/package.json

# Copy backend files
COPY --chown=$UID:$GID ./backend .

EXPOSE 8080

HEALTHCHECK CMD curl --silent --fail http://localhost:${PORT:-8080}/health | jq -ne 'input.status == true' || exit 1

USER $UID:$GID

ARG BUILD_HASH
ENV WEBUI_BUILD_VERSION=${BUILD_HASH}
ENV DOCKER=true

CMD [ "bash", "start.sh"]
