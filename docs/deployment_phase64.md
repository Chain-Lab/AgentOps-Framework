# Phase 64 Deployment Guide

## 1. 部署目标

Phase 64 的目标是把 daemon 从"本地可运行"推进到"可生产部署"：

- Docker 容器化部署，非 root 用户运行
- Kubernetes manifests 支持单副本生产部署
- systemd unit 支持裸机/VM 部署
- 健康检查 / 就绪检查 / 优雅停机
- SQLite control plane 持久化
- Bearer token 认证的 control HTTP API

## 2. Docker 本地运行

### 构建镜像

```bash
docker build -t agent-app:0.49.0 .
```

### 运行

```bash
docker run --rm \
  -e AGENT_APP_CONFIG=/app/config/daemon.yaml \
  -e AGENT_APP_CONTROL_TOKEN=dev-token \
  -p 8080:8080 \
  -p 8090:8090 \
  -v $(pwd)/deploy/config/daemon.example.yaml:/app/config/daemon.yaml:ro \
  -v agent-app-data:/data \
  agent-app:0.49.0
```

### 端口

| 端口 | 用途 | Phase |
|------|------|-------|
| 8080 | Health HTTP server (`/health`, `/ready`) | 62 |
| 8090 | Control HTTP server (`/control/commands`, `/approvals`, `/audit/events`) | 63 |

### 卷

| 路径 | 用途 |
|------|------|
| `/app/config` | Daemon YAML 配置（建议只读挂载） |
| `/data` | SQLite control plane DB，可写 |

### 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `AGENT_APP_CONFIG` | 否 | `/app/config/daemon.yaml` | 配置文件路径 |
| `AGENT_APP_CONTROL_TOKEN` | 否 | `""` | Control API Bearer token |
| `AGENT_APP_CONTROL_DB` | 否 | `/data/control_plane.db` | SQLite DB 路径 |

### 健康检查

Docker HEALTHCHECK 调用 `/health` endpoint，使用 Python stdlib `urllib`（无需 curl）：

```bash
docker inspect --format='{{.State.Health.Status}}' agent-app:0.49.0
```

### 本地验证

```bash
docker run --rm \
  -e AGENT_APP_CONFIG=/app/config/daemon.yaml \
  agent-app:0.49.0 --help
```

## 3. Kubernetes 部署步骤

### 前提条件

- Kubernetes 1.24+
- `kubectl` 已配置
- 有创建 Namespace、Secret、PVC 的权限

### 部署步骤

```bash
# 1. 创建 Namespace
kubectl apply -f deploy/kubernetes/namespace.yaml

# 2. 创建 ServiceAccount
kubectl apply -f deploy/kubernetes/serviceaccount.yaml

# 3. 创建 Control Token Secret
kubectl create secret generic agent-app-daemon-secret \
  --from-literal=AGENT_APP_CONTROL_TOKEN=your-secure-token-here \
  -n agent-app

# 4. 创建 PVC
kubectl apply -f deploy/kubernetes/persistent-volume-claim.yaml

# 5. 创建 ConfigMap
kubectl apply -f deploy/kubernetes/configmap.yaml

# 6. 验证配置
kubectl apply -f deploy/kubernetes/job-validate-config.yaml
kubectl wait --for=condition=complete job/agent-app-validate-config \
  -n agent-app --timeout=30s
kubectl logs job/agent-app-validate-config -n agent-app
kubectl delete job agent-app-validate-config -n agent-app

# 7. 部署
kubectl apply -f deploy/kubernetes/deployment.yaml
kubectl apply -f deploy/kubernetes/service.yaml
kubectl apply -f deploy/kubernetes/poddisruptionbudget.yaml
kubectl apply -f deploy/kubernetes/networkpolicy.yaml
```

### 验证部署

```bash
# Pod 状态
kubectl get pods -n agent-app

# 日志
kubectl logs -l app.kubernetes.io/name=agent-app-daemon -n agent-app -f

# 端口转发（本地访问）
kubectl port-forward svc/agent-app-daemon 8080:8080 -n agent-app

# 健康检查
curl http://localhost:8080/health
curl http://localhost:8080/ready

# Control API（需要 token）
curl -H "Authorization: Bearer <token>" http://localhost:8090/control/status
```

## 4. systemd 部署步骤

### 前提条件

- Linux 系统支持 systemd
- Python 3.12+ 已安装
- 项目已安装：`pip install -e ".[dev]"`

### 部署步骤

```bash
# 1. 创建用户
sudo useradd -r -s /sbin/nologin -d /opt/agent-app agent-app

# 2. 创建目录
sudo mkdir -p /opt/agent-app /etc/agent-app /var/lib/agent-app /var/log/agent-app
sudo chown -R agent-app:agent-app /opt/agent-app /var/lib/agent-app /var/log/agent-app

# 3. 复制 unit 文件和配置
sudo cp deploy/systemd/agent-app-daemon.service /etc/systemd/system/
sudo cp deploy/config/daemon.systemd.yaml /etc/agent-app/daemon.yaml
sudo cp deploy/systemd/agent-app-daemon.env /etc/agent-app/agent-app-daemon.env

# 4. 设置环境文件权限（保护 token）
sudo chmod 600 /etc/agent-app/agent-app-daemon.env

# 5. 编辑环境文件，填入真实 token
sudo vi /etc/agent-app/agent-app-daemon.env

# 6. 启用服务
sudo systemctl daemon-reload
sudo systemctl enable --now agent-app-daemon
```

### 验证

```bash
# 服务状态
sudo systemctl status agent-app-daemon

# 日志
sudo journalctl -u agent-app-daemon -f

# 健康检查
curl http://127.0.0.1:8080/health

# Control API
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8090/control/status
```

## 5. 配置说明

### 三层配置结构

| 层级 | 文件 | 适用场景 |
|------|------|---------|
| Example | `deploy/config/daemon.example.yaml` | 文档参考、本地开发 |
| Kubernetes | `deploy/config/daemon.kubernetes.yaml` | K8s 容器部署 |
| systemd | `deploy/config/daemon.systemd.yaml` | 裸机/VM systemd 部署 |

### 关键配置字段

#### Phase 61: 连续循环控制

```yaml
poll_interval_seconds: 1.0      # 轮询间隔（秒）
idle_sleep_seconds: 1.0          # 空闲时睡眠间隔
error_sleep_seconds: 5.0         # 错误后睡眠间隔
max_consecutive_errors: 10       # 最大连续错误数
shutdown_timeout_seconds: 10.0   # 关闭超时
```

#### Phase 62: 优雅停机 / 指标缓冲

```yaml
graceful_shutdown_enabled: true       # 启用优雅停机
drain_timeout_seconds: 30.0           # drain 超时
cancel_inflight_on_timeout: true      # 超时取消进行中的任务
metrics_buffer_enabled: true          # 启用指标缓冲
metrics_buffer_max_size: 1000         # 最大缓冲大小
metrics_flush_interval_seconds: 10.0  # 刷新间隔
flush_metrics_on_stop: true           # 停止时刷新
renew_lock_during_batch: true         # 长 batch 期间续租锁
lock_renewal_failure_policy: standby  # 续租失败策略
```

#### Phase 62: Health HTTP Server

```yaml
health_http_enabled: true           # 启用 health HTTP
health_http_host: 0.0.0.0           # 监听地址（K8s 用 0.0.0.0）
health_http_port: 8080              # 监听端口
ready_requires_leader: false        # ready 是否需要 leader
```

#### Phase 63: 持久化控制平面

```yaml
control_plane_enabled: true                     # 启用控制平面
control_plane_db_path: /data/control_plane.db   # SQLite DB 路径
control_command_poll_interval_seconds: 1.0      # 命令轮询间隔
control_command_max_age_seconds: 86400           # 命令最大存活时间

# Control HTTP Server
control_http_enabled: true                      # 启用 control HTTP
control_http_host: 0.0.0.0                      # 监听地址
control_http_port: 8090                         # 监听端口
control_http_token: null                        # 直接设置 token（不推荐）
control_http_token_env: AGENT_APP_CONTROL_TOKEN # 从环境变量读取 token
```

## 6. Secret 管理

### 控制 Token

**原则：永远不要将真实 token 提交到版本控制。**

```bash
# 本地 Docker 运行
export AGENT_APP_CONTROL_TOKEN=$(openssl rand -hex 32)
docker run -e AGENT_APP_CONTROL_TOKEN="$AGENT_APP_CONTROL_TOKEN" ...

# Kubernetes
kubectl create secret generic agent-app-daemon-secret \
  --from-literal=AGENT_APP_CONTROL_TOKEN=$(openssl rand -hex 32) \
  -n agent-app

# systemd
sudo vi /etc/agent-app/agent-app-daemon.env
# 设置 AGENT_APP_CONTROL_TOKEN=your-token
sudo chmod 600 /etc/agent-app/agent-app-daemon.env
```

### 生产建议

- 使用外部 Secret Manager（HashiCorp Vault, AWS Secrets Manager, GCP Secret Manager）
- 通过 External Secrets Operator 同步到 K8s Secret
- 定期轮换 token（参考 Phase 58 webhook key rotation）
- 审计 token 访问日志

## 7. PVC / SQLite 限制

### 单副本限制

当前 Phase 64 使用 SQLite 作为 control plane 后端，SQLite 是**单写者数据库**。因此：

- **默认 Deployment replicas = 1**
- PVC 使用 `ReadWriteOnce` 访问模式
- **不要** 在没有更改 backend 的情况下增加副本数

### 升级到多副本（未来）

如需多副本部署，需要：

1. 实现 PostgreSQL/Redis control plane backend（后续 Phase）
2. 或使用 NFS/CSI 的 `ReadWriteMany` 存储（需评估 SQLite 兼容性）
3. 实现分布式 command lease（防止多实例同时处理同一命令）

### PVC 大小

默认 1Gi，根据预期调整：

- 小规模（< 1000 alerts/day）：1Gi 足够
- 大规模：监控 `/data` 使用量，相应调整 `storage` 请求

## 8. Health / Readiness 语义

### Endpoints

| Endpoint | 含义 | K8s Probe |
|----------|------|-----------|
| `GET /health` | 进程存活，依赖可达 | Liveness, Startup |
| `GET /ready` | 可接受流量，非 draining | Readiness |
| `GET /health/live` | 存活（Phase 63 daemon） | - |
| `GET /health/ready` | 就绪（Phase 63 daemon） | - |

### Probe 行为

- **Liveness**: 失败 → Pod 重启
- **Readiness**: 失败 → Pod 从 Service 移除，不接收新流量
- **Startup**: 失败 → Pod 不会被 liveness/readiness 检查，直到启动完成

### Drain 期间 Readiness

当 daemon 收到 drain 命令时：
1. 停止接受新 batch
2. 完成进行中的 batch
3. Readiness probe 应返回 503（不健康）

## 9. Graceful Shutdown 流程

### K8s Pod 终止流程

```
K8s 发送 SIGTERM
    ↓
Pod 进入 Terminating 状态（从 Service 移除）
    ↓
preStop hook 执行：
  - 发送 drain 命令到 control API
  - sleep 5s（给 drain 时间开始）
    ↓
SIGTERM 到达 daemon 进程
    ↓
Daemon 收到 SIGTERM：
  - 设置 graceful_shutdown = True
  - 完成当前 batch
  - flush metrics buffer
  - 关闭 stores
  - 退出
    ↓
如果超时（terminationGracePeriodSeconds）：
  - K8s 发送 SIGKILL
  - 强制终止
```

### systemd Stop 流程

```
systemctl stop agent-app-daemon
    ↓
ExecStop 执行：
  - 发送 drain 命令到 control API
  - || true（不阻塞 systemd stop）
    ↓
SIGTERM 发送到 daemon 进程
    ↓
Daemon 处理 SIGTERM（同上）
    ↓
如果超时（TimeoutStopSec=45）：
  - systemd 发送 SIGKILL
```

## 10. Control Plane 操作示例

### 查看状态

```bash
# CLI
agent-app alerts delivery daemon control status

# HTTP API
curl -H "Authorization: Bearer <token>" http://localhost:8090/control/status
```

### 发送控制命令

```bash
# Pause（暂停 batch 处理）
agent-app alerts delivery daemon control commands send \
  --db /data/control_plane.db \
  --command pause \
  --requested-by operator \
  --reason "maintenance window"

# Resume（恢复 batch 处理）
agent-app alerts delivery daemon control commands send \
  --db /data/control_plane.db \
  --command resume \
  --requested-by operator

# Drain（排空队列后停机）
agent-app alerts delivery daemon control commands send \
  --db /data/control_plane.db \
  --command drain \
  --requested-by operator

# Shutdown（立即停机）
agent-app alerts delivery daemon control commands send \
  --db /data/control_plane.db \
  --command shutdown \
  --requested-by operator

# Flush Metrics
agent-app alerts delivery daemon control commands send \
  --db /data/control_plane.db \
  --command flush_metrics \
  --requested-by operator
```

### 查看审计日志

```bash
# CLI
agent-app alerts delivery daemon control commands list

# HTTP API
curl -H "Authorization: Bearer <token>" \
  http://localhost:8090/audit/events?limit=50
```

## 11. 回滚流程

### 回滚到上一个版本

```bash
# 1. 发送 drain 命令
agent-app alerts delivery daemon control commands send \
  --db /data/control_plane.db \
  --command drain \
  --requested-by operator \
  --reason "rollback preparation"

# 2. 等待 drain 完成（检查 /ready 返回 503）
while curl -sf http://localhost:8080/ready >/dev/null; do
    echo "Waiting for drain..."
    sleep 2
done

# 3. 更新镜像
docker tag agent-app:0.49.0 agent-app:0.49.0-old
docker build -t agent-app:0.48.0 .
# 或拉取旧版本
docker pull agent-app:0.48.0

# 4. 重启服务（K8s）
kubectl set image deployment/agent-app-daemon \
  agent-app-daemon=agent-app:0.48.0 -n agent-app
kubectl rollout status deployment/agent-app-daemon -n agent-app

# 5. 验证回滚
curl http://localhost:8080/health
```

### K8s 回滚

```bash
# 查看 rollout 历史
kubectl rollout history deployment/agent-app-daemon -n agent-app

# 回滚到上一版本
kubectl rollout undo deployment/agent-app-daemon -n agent-app

# 回滚到指定版本
kubectl rollout undo deployment/agent-app-daemon \
  --to-revision=2 -n agent-app
```

## 12. 常见故障排查

### Pod 无法启动

```bash
# 检查 Pod 事件
kubectl describe pod -l app.kubernetes.io/name=agent-app-daemon -n agent-app

# 常见原因：
# - ConfigMap config 格式错误 → 检查 job-validate-config 输出
# - PVC 未绑定 → 检查 StorageClass
# - Secret 不存在 → 确认 agent-app-daemon-secret 已创建
# - 镜像拉取失败 → 检查 imagePullPolicy 和 registry
```

### Health Check 失败

```bash
# 检查健康 endpoint
kubectl exec -it <pod> -n agent-app -- curl -v http://127.0.0.1:8080/health

# 常见原因：
# - 健康 HTTP server 未启用（检查 config 中 health_http_enabled）
# - 端口冲突（检查 health_http_port 是否被占用）
# - 依赖服务不可用
```

### Control API 返回 401

```bash
# 检查 token 是否设置
kubectl exec -it <pod> -n agent-app -- env | grep AGENT_APP_CONTROL_TOKEN

# 常见原因：
# - Secret 未挂载 → 检查 env.valueFrom.secretKeyRef
# - Token 不匹配 → 确认发送的 token 与 Secret 一致
# - control_http_enabled 为 false
```

### PVC 存储不足

```bash
# 检查 PVC 使用量
kubectl get pvc -n agent-app

# 扩容 PVC（如果 StorageClass 支持）
kubectl patch pvc agent-app-control-plane-pvc \
  -n agent-app \
  --type='json' \
  -p='[{"op": "replace", "path": "/spec/resources/requests/storage", "value": "5Gi"}]'
```

### 优雅停机超时

```bash
# 如果 Pod 在 drain 期间被 SIGKILL：
# 1. 增加 terminationGracePeriodSeconds
# 2. 确认 drain_timeout_seconds < terminationGracePeriodSeconds
# 3. 检查 daemon 日志是否有 "graceful shutdown" 消息
kubectl logs <pod> -n agent-app | grep -i "drain\|shutdown\|graceful"
```
