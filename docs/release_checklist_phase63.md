# Release Checklist — Phase 63

**Version:** v0.48.0
**Phase:** Persistent Approval / Control Plane
**Date:** 2026-06-30

## Implementation Checklist

- [x] Persistent control plane store (`ControlPlaneStore`): SQLite-backed command state machine (PENDING → ACCEPTED → RUNNING → COMPLETED/FAILED/REJECTED/EXPIRED)
- [x] Persistent approval store (`PersistentApprovalStore`): operator approve/reject/expire lifecycle
- [x] Persistent audit store (`PersistentAuditStore`): append-only events, filterable by event_type/command_id/approval_id
- [x] Control HTTP server (`_ControlHTTPServer`): stdlib-based REST API, Bearer token authentication
- [x] Daemon control polling: background asyncio task polls pending commands, executes, writes audit events
- [x] Daemon control commands: pause, resume, drain, shutdown, flush_metrics, release_lock, health_snapshot
- [x] Health status extensions: control_plane_enabled, control_paused, last_control_command_id, last_control_error, pending_control_commands, pending_approvals
- [x] CLI control commands: `daemon control status`, `daemon control commands list/send/get`
- [x] Config extensions: control_plane_enabled, control_plane_db_path, control_command_poll_interval_seconds, control_http_*
- [x] 79 new Phase 63 unit tests across 5 test files

## Test Coverage

- [x] 79 unit tests across 5 test files (control plane store, approval store, audit store, control server, daemon control commands)

## Acceptance Criteria

- [x] Control commands transition through the full state machine correctly
- [x] Approval requests support operator approve/reject/expire lifecycle
- [x] Audit events are append-only and filterable
- [x] Control HTTP server enforces Bearer token authentication
- [x] Daemon correctly executes all 7 control command types
- [x] Health status reflects control plane and pending-command/approval counts
- [x] CLI control commands round-trip correctly against the HTTP API
- [x] Existing Phase 62 behavior remains backward compatible
