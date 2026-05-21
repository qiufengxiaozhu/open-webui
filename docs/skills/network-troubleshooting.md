---
name: 网络故障排查
keywords: [网络, DNS, 防火墙, 丢包, 延迟, TCP, HTTP, HTTPS, 证书, SSL, TLS, 路由, 网关, VPN, 带宽, 负载均衡, Nginx, CDN]
description: 网络常见故障的根因分析指南
---

# 网络故障排查指南

## 1. DNS 解析故障

### 常见原因
- DNS 服务器不可用
- DNS 缓存污染
- 域名过期或配置错误
- DNSSEC 验证失败

### 排查步骤
1. `nslookup <domain>` 或 `dig <domain>` 测试解析
2. `dig @8.8.8.8 <domain>` 使用公共 DNS 对比
3. 检查 `/etc/resolv.conf` 配置
4. `systemd-resolve --status` 查看本地 DNS 状态
5. 检查域名注册商的 DNS 记录配置

## 2. 丢包与高延迟

### 常见原因
- 网络拥塞
- 硬件故障（网卡、交换机、线缆）
- MTU 不匹配
- QoS 策略限制
- ISP 线路问题

### 排查步骤
1. `ping -c 100 <target>` 检测丢包率
2. `traceroute <target>` 或 `mtr <target>` 跟踪路由跳数
3. `ethtool <interface>` 检查网卡状态和错误计数
4. `sar -n DEV 1 10` 查看网络流量
5. 检查交换机端口状态和日志

## 3. SSL/TLS 证书问题

### 常见原因
- 证书过期
- 证书链不完整
- 域名与证书不匹配
- 中间证书缺失
- 协议版本不兼容

### 排查步骤
1. `openssl s_client -connect <host>:443` 检查证书链
2. `openssl x509 -in cert.pem -noout -dates` 查看有效期
3. 在线工具检查：SSL Labs (ssllabs.com)
4. 检查服务器配置（Nginx/Apache）的证书路径
5. 验证中间证书是否正确配置

## 4. 负载均衡问题

### 常见原因
- 后端节点健康检查失败
- 会话保持（Session Sticky）配置问题
- 权重分配不合理
- 连接排空（Drain）未正确处理

### 排查步骤
1. 检查 LB 健康检查日志
2. `curl -v` 多次请求，确认分发到不同后端
3. 检查后端节点的服务状态
4. 审查 Nginx upstream 配置
5. 检查 keepalive 和 timeout 设置

## 5. 防火墙/安全组规则

### 常见原因
- 新部署的服务端口未开放
- 规则变更影响了已有服务
- 安全组规则冲突
- iptables 规则链顺序问题

### 排查步骤
1. `iptables -L -n -v` 查看规则和命中计数
2. `firewall-cmd --list-all` (firewalld)
3. 检查云平台安全组配置
4. `tcpdump` 确认数据包是否到达
5. 临时放开规则测试（注意安全）
