# Phase 64 Release Checklist

## Pre-Release

- [ ] 代码冻结 — 确认无未提交的 Phase 64 代码
- [ ] 基线测试通过 — Phase 60/61/62/63 测试 0 failures
- [ ] Phase 64 专项测试通过 — `test_phase64_*.py` 全部 pass

## Build

- [ ] Docker 镜像构建成功
  ```bash
  docker build -t agent-app:0.49.0 .
  ```
- [ ] 镜像大小检查 — 确认 slim 镜像 < 200MB
  ```bash
  docker images agent-app:0.49.0
  ```
- [ ] 镜像安全扫描（手动检查项）：
  - [ ] 基础镜像 `python:3.12-slim` 无已知 CVE
  - [ ] 无敏感文件进入镜像（检查 `.dockerignore` 覆盖范围）
  - [ ] 非 root 用户运行（`USER agent-app`）
  - [ ] 无 `apt-get install` 残留缓存
  - [ ] HEALTHCHECK 指令存在
  - [ ] 镜像无 tag `latest`

## Tests

- [ ] Phase 64 Docker 测试通过
  ```bash
  pytest tests/unit/test_phase64_docker_artifacts.py -v
  ```
- [ ] Phase 64 部署配置测试通过
  ```bash
  pytest tests/unit/test_phase64_deployment_config.py -v
  ```
- [ ] Phase 64 K8s manifest 测试通过
  ```bash
  pytest tests/unit/test_phase64_kubernetes_manifests.py -v
  ```
- [ ] Phase 64 systemd 测试通过
  ```bash
  pytest tests/unit/test_phase64_systemd_units.py -v
  ```
- [ ] Phase 60/61/62/63 回归测试通过
  ```bash
  pytest tests/unit/test_policy_notification_retry_daemon.py \
         tests/unit/test_phase63_*.py -q
  ```
- [ ] 全量单元测试通过
  ```bash
  pytest tests/unit -q
  ```

## Docker Smoke Test

- [ ] Docker 静态检查通过
  ```bash
  bash scripts/smoke_docker.sh
  ```
- [ ] Docker 镜像可启动
  ```bash
  docker run --rm \
    -e AGENT_APP_CONFIG=/app/config/daemon.example.yaml \
    agent-app:0.49.0 --help
  ```
- [ ] 容器内健康检查通过
  ```bash
  docker run -d --name smoke-test \
    -e AGENT_APP_CONFIG=/app/config/daemon.example.yaml \
    -p 8080:8080 \
    agent-app:0.49.0
  sleep 10 && curl -sf http://localhost:8080/health
  docker rm -f smoke-test
  ```

## Kubernetes Validation

- [ ] 所有 YAML 文件可被 PyYAML 解析
- [ ] `kubectl apply --dry-run=client` 通过（有 kubectl 时）
  ```bash
  for f in deploy/kubernetes/*.yaml; do
    kubectl apply --dry-run=client -f "$f"
  done
  ```
- [ ] validate-config Job 能通过
  ```bash
  kubectl apply -f deploy/kubernetes/job-validate-config.yaml
  kubectl wait --for=condition=complete \
    job/agent-app-validate-config -n agent-app --timeout=30s
  kubectl logs job/agent-app-validate-config -n agent-app
  kubectl delete job agent-app-validate-config -n agent-app
  ```
- [ ] Deployment 默认 replicas = 1
- [ ] Deployment 包含 liveness/readiness/startup probes
- [ ] Deployment 包含 preStop hook
- [ ] Deployment 使用 non-root securityContext
- [ ] PVC 挂载到 `/data`
- [ ] ConfigMap 覆盖 Phase 62/63 关键配置
- [ ] Secret 不含真实 token
- [ ] NetworkPolicy 存在
- [ ] PodDisruptionBudget 存在

## Control Plane Smoke Test

- [ ] Phase 63 控制平面测试通过
  ```bash
  bash scripts/smoke_control_plane.sh
  ```

## Graceful Termination Test

- [ ] Pod 收到 SIGTERM 后执行 drain
  ```bash
  kubectl scale deployment/agent-app-daemon --replicas=0 -n agent-app
  # 检查日志确认 drain 消息
  kubectl logs -l app.kubernetes.io/name=agent-app-daemon -n agent-app \
    --previous | grep -i "drain\|graceful"
  ```
- [ ] terminationGracePeriodSeconds >= drain_timeout_seconds
- [ ] preStop hook 在终止前执行

## Documentation

- [ ] `docs/deployment_phase64.md` 内容完整
- [ ] `deploy/kubernetes/README.md` 内容完整
- [ ] `deploy/systemd/README.md` 内容完整
- [ ] `docker/README.md` 内容完整
- [ ] `CHANGELOG.md` 包含 v0.49.0 条目
- [ ] `README.md` 包含 v0.49.0 roadmap 条目
- [ ] `pyproject.toml` 版本号 0.49.0

## Version

- [ ] 版本号: `0.48.0 → 0.49.0`
- [ ] pyproject.toml 已更新
- [ ] CHANGELOG.md 已更新
- [ ] README.md 已更新

## Rollback

- [ ] 确认回滚到 0.48.0 的步骤清晰
- [ ] 确认 0.48.0 镜像/tag 仍可用
- [ ] 确认 0.48.0 K8s manifests 保留在版本历史中

## Post-Release

- [ ] Git tag 创建：`git tag v0.49.0 && git push --tags`
- [ ] GitHub Release 创建
- [ ] 部署到 staging 环境验证
- [ ] 监控 /health 和 /ready 端点
- [ ] 确认 control plane DB 正常写入
