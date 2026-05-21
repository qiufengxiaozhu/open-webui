---
name: 服务器故障排查
keywords: [服务器, 宕机, CPU, 内存, 磁盘, 负载, OOM, 超时, 延迟, "500错误", "503", 内存泄漏, 进程挂死, 连接超时]
description: 服务器常见故障的根因分析指南
---

# 服务器故障排查指南

## 1. CPU 高负载

### 常见原因
- 死循环或低效算法
- 并发请求过多，线程池耗尽
- GC（垃圾回收）频繁触发
- 恶意请求导致的资源滥用

### 排查步骤
1. `top -c` 查看进程 CPU 占用排名
2. `pidstat -p <PID> 1` 查看具体进程的 CPU 使用详情
3. 如果是 Java 应用：`jstack <PID>` 抓取线程堆栈，检查是否有死锁或大量 RUNNABLE 线程
4. 如果是 Python 应用：使用 `py-spy` 或 `cProfile` 分析热点函数
5. 检查最近的部署变更和代码提交

### 根因分类
- **代码缺陷**：死循环、正则回溯、无限递归
- **资源不足**：需要扩容或优化算法复杂度
- **外部依赖**：下游服务响应慢导致线程堆积

## 2. 内存不足（OOM）

### 常见原因
- 内存泄漏（对象未释放）
- 缓存未设上限
- 大文件加载到内存
- JVM 堆大小配置不当

### 排查步骤
1. `free -h` 查看系统内存
2. `ps aux --sort=-%mem | head -20` 查看内存占用 Top 20
3. 检查 `/var/log/messages` 或 `dmesg` 中的 OOM Killer 日志
4. Java 应用：分析 heap dump（`jmap -dump:format=b,file=heap.hprof <PID>`）
5. Python 应用：使用 `tracemalloc` 或 `objgraph` 追踪内存分配

## 3. 磁盘空间不足

### 常见原因
- 日志文件未轮转
- 临时文件未清理
- 数据库 binlog 堆积
- Docker 镜像/容器占用

### 排查步骤
1. `df -h` 查看磁盘使用率
2. `du -sh /* | sort -rh | head -20` 找到最大目录
3. `find / -type f -size +100M -exec ls -lh {} \;` 查找大文件
4. 检查 `/var/log/` 目录大小
5. `docker system df` 检查 Docker 资源占用

## 4. 网络连接超时

### 常见原因
- 防火墙规则变更
- DNS 解析故障
- 网络带宽饱和
- 连接池耗尽
- 下游服务不可用

### 排查步骤
1. `ping` / `telnet` / `curl` 测试基本连通性
2. `netstat -an | grep ESTABLISHED | wc -l` 查看连接数
3. `ss -s` 查看 socket 统计
4. 检查防火墙规则：`iptables -L -n`
5. 抓包分析：`tcpdump -i eth0 port <PORT> -w capture.pcap`

## 5. 服务 500/503 错误

### 常见原因
- 应用程序异常未捕获
- 数据库连接池耗尽
- 依赖服务不可用
- 配置错误（部署后）
- 资源限制（文件句柄、线程数）

### 排查步骤
1. 查看应用日志中的异常堆栈
2. 检查数据库连接状态
3. 检查依赖服务健康状态
4. 查看最近的部署记录和配置变更
5. 检查系统资源限制：`ulimit -a`
