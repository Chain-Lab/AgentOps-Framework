# Kubernetes Deployment Manifests

This directory contains Kubernetes manifests for deploying the Agent App Delivery Retry Daemon.

## Files

| File | Resource | Purpose |
|------|----------|---------|
| `namespace.yaml` | Namespace | Isolated namespace `agent-app` |
| `serviceaccount.yaml` | ServiceAccount | Non-root pod identity (no K8s API access) |
| `configmap.yaml` | ConfigMap | Daemon YAML config with Phase 61/62/63 fields |
| `secret.yaml` | Secret | Control token placeholder (create real secret separately) |
| `persistent-volume-claim.yaml` | PVC | SQLite control plane DB storage |
| `deployment.yaml` | Deployment | Daemon pod (replicas: 1, non-root, probes, preStop) |
| `service.yaml` | Service | ClusterIP exposing health (8080) and control (8090) |
| `networkpolicy.yaml` | NetworkPolicy | Conservative ingress restriction |
| `poddisruptionbudget.yaml` | PodDisruptionBudget | minAvailable: 0 (single replica, no HA) |
| `job-validate-config.yaml` | Job | Pre-deployment config validation |

## Deployment Steps

```bash
# 1. Create namespace
kubectl apply -f deploy/kubernetes/namespace.yaml

# 2. Create ServiceAccount
kubectl apply -f deploy/kubernetes/serviceaccount.yaml

# 3. Create control token secret (REPLACE with real token!)
kubectl create secret generic agent-app-daemon-secret \
  --from-literal=AGENT_APP_CONTROL_TOKEN=your-token-here \
  -n agent-app

# 4. Create PVC
kubectl apply -f deploy/kubernetes/persistent-volume-claim.yaml

# 5. Create ConfigMap
kubectl apply -f deploy/kubernetes/configmap.yaml

# 6. Validate config before deploying
kubectl apply -f deploy/kubernetes/job-validate-config.yaml
kubectl wait --for=condition=complete job/agent-app-validate-config -n agent-app --timeout=30s
kubectl logs job/agent-app-validate-config -n agent-app
kubectl delete job agent-app-validate-config -n agent-app

# 7. Deploy
kubectl apply -f deploy/kubernetes/deployment.yaml
kubectl apply -f deploy/kubernetes/service.yaml
kubectl apply -f deploy/kubernetes/poddisruptionbudget.yaml
kubectl apply -f deploy/kubernetes/networkpolicy.yaml

# 8. Verify
kubectl get pods -n agent-app
kubectl port-forward svc/agent-app-daemon 8080:8080 -n agent-app
curl http://localhost:8080/health
```

## Important Notes

### Single Replica Only

The default deployment uses `replicas: 1` because:

1. **SQLite is single-writer** — the control plane DB cannot handle concurrent writes from multiple replicas
2. **No distributed command lease** — Phase 64 does not implement cross-instance command assignment

Do NOT increase replicas without also changing the control plane backend (future phase).

### PVC / Storage

- Default PVC requests 1Gi with `ReadWriteOnce` access mode
- Mounted at `/data` — contains `control_plane.db`
- Use a StorageClass with appropriate replication/backup for production

### Control Token

- The `secret.yaml` contains a placeholder token (`dev-token` base64)
- **Always create the secret with a real token before deploying**
- Use `kubectl create secret` or an external secret manager
- Never commit real tokens to version control

### NetworkPolicy

- Health port (8080) is open to same-namespace pods (for K8s probes)
- Control port (8090) is restricted to pods with `app=agent-app-operator` label
- Adjust the NetworkPolicy `from` selectors to match your operator's namespace/labels

### Graceful Shutdown

- `terminationGracePeriodSeconds: 60` gives time for drain
- `preStop` hook sends drain command via control API
- Daemon handles SIGTERM → drain → complete in-flight → exit
