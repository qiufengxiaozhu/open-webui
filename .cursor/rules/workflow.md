---
description: 项目开发工作流规范，适用于所有文件修改场景
globs: "**/*"
---

# 开发工作流

## 构建与部署

- 当前处于**开发阶段**，修改代码/文档后**不要自动执行** docker build 和远程部署
- 修改完成后，应主动询问用户是否需要 build 镜像并部署，由用户决定
- 只有用户明确要求时，才执行 docker compose build、docker save、scp 上传、远程 docker load 等操作

## 响应语言

- 所有回复使用**中文（简体）**
