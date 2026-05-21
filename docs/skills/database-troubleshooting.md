---
name: 数据库故障排查
keywords: [数据库, MySQL, PostgreSQL, Redis, MongoDB, 慢查询, 死锁, 主从, 复制延迟, 连接池, 索引, SQL, 查询超时, 数据库宕机]
description: 数据库常见故障的根因分析指南
---

# 数据库故障排查指南

## 1. 慢查询

### 常见原因
- 缺少索引或索引失效
- 查询未优化（全表扫描、笛卡尔积）
- 数据量增长超出预期
- 锁等待导致的阻塞

### 排查步骤
1. 开启慢查询日志（MySQL: `slow_query_log=ON`）
2. 使用 `EXPLAIN` / `EXPLAIN ANALYZE` 分析执行计划
3. 检查索引使用情况：`SHOW INDEX FROM <table>`
4. 监控锁等待：`SHOW ENGINE INNODB STATUS`
5. 分析 Query Profile：`SET profiling = 1; SHOW PROFILE`

### 优化建议
- 添加合适的组合索引
- 避免 `SELECT *`，只查需要的列
- 使用分页查询代替全量拉取
- 考虑读写分离和分库分表

## 2. 死锁

### 常见原因
- 事务顺序不一致
- 长事务持有锁时间过长
- 间隙锁（Gap Lock）冲突
- 批量更新顺序不一致

### 排查步骤
1. `SHOW ENGINE INNODB STATUS` 查看最近死锁信息
2. 分析死锁日志中的两个事务
3. 检查事务隔离级别
4. 审查涉及的 SQL 语句和索引
5. 确认加锁顺序是否一致

## 3. 连接池耗尽

### 常见原因
- 连接泄漏（未正确关闭连接）
- 慢查询占用连接时间过长
- 连接池大小配置不当
- 突发流量

### 排查步骤
1. `SHOW PROCESSLIST` 查看当前连接
2. `SHOW STATUS LIKE 'Threads%'` 查看线程状态
3. 检查应用端连接池监控指标
4. 审查代码中的连接获取和释放逻辑

## 4. 主从复制延迟

### 常见原因
- 从库硬件性能不足
- 大事务导致从库重放慢
- 网络带宽瓶颈
- 从库存在大量查询

### 排查步骤
1. `SHOW SLAVE STATUS` 查看 `Seconds_Behind_Master`
2. 检查从库的 IO Thread 和 SQL Thread 状态
3. 对比主从的 binlog 位置
4. 监控从库的 CPU/IO/网络使用情况

## 5. Redis 问题

### 常见问题
- 内存使用过高（达到 maxmemory）
- 大 key 导致的阻塞
- 热 key 导致的单点压力
- 持久化（RDB/AOF）导致的延迟抖动

### 排查步骤
1. `INFO memory` 查看内存使用
2. `INFO clients` 查看客户端连接
3. `SLOWLOG GET 10` 查看慢命令
4. `redis-cli --bigkeys` 扫描大 key
5. `MONITOR` 实时观察命令（注意：生产慎用）
