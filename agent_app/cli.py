"""CLI — command-line interface for AgentApp.

Entry point: agentapp

Commands:
    agentapp eval run <suite_file> --config <config_file>
    agentapp trace list --config <config_file>
    agentapp trace show <trace_id> --config <config_file>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys

from agent_app.evals.runner import EvalRunner, load_eval_suite


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="agentapp",
        description="Agent App Framework CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    # eval run
    eval_parser = subparsers.add_parser("eval", help="Eval commands")
    eval_sub = eval_parser.add_subparsers(dest="eval_command")
    run_parser = eval_sub.add_parser("run", help="Run an eval suite")
    run_parser.add_argument("suite", help="Path to eval suite YAML file")
    run_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )

    # trace commands (Phase 12)
    trace_parser = subparsers.add_parser("trace", help="Trace commands")
    trace_sub = trace_parser.add_subparsers(dest="trace_command")

    list_parser = trace_sub.add_parser("list", help="List traces")
    list_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    list_parser.add_argument("--limit", type=int, default=20, help="Max traces to show")
    list_parser.add_argument("--run-id", default=None, help="Filter by run ID")
    list_parser.add_argument("--tenant-id", default=None, help="Filter by tenant ID")
    list_parser.add_argument("--event-type", default=None, help="Filter by event type")
    list_parser.add_argument("--json", action="store_true", help="Output as JSON")

    show_parser = trace_sub.add_parser("show", help="Show trace details")
    show_parser.add_argument("trace_id", help="Trace ID to show")
    show_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    show_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # recovery commands (Phase 16.5)
    recovery_parser = subparsers.add_parser("recovery", help="Recovery commands")
    recovery_sub = recovery_parser.add_subparsers(dest="recovery_command")

    scan_parser = recovery_sub.add_parser("scan", help="Scan for recovery candidates")
    scan_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    scan_parser.add_argument(
        "--limit", type=int, default=100, help="Max candidates to scan"
    )
    scan_parser.add_argument(
        "--workflow", default=None, help="Filter by workflow name"
    )
    scan_parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Dry-run mode: scan but do not recover (default)",
    )
    scan_parser.add_argument(
        "--no-dry-run", action="store_true",
        help="Disable dry-run: allow actual recoveries",
    )
    scan_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    inspect_parser = recovery_sub.add_parser(
        "inspect", help="Inspect a single recovery candidate"
    )
    inspect_parser.add_argument("run_id", help="Run ID to inspect")
    inspect_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    inspect_parser.add_argument("--json", action="store_true", help="Output as JSON")

    recover_parser = recovery_sub.add_parser(
        "recover", help="Manually recover a workflow run"
    )
    recover_parser.add_argument("run_id", help="Run ID to recover")
    recover_parser.add_argument(
        "--workflow", default="", help="Workflow name"
    )
    recover_parser.add_argument(
        "--recovered-by", default="admin-cli", help="Identity of the operator"
    )
    recover_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    recover_parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Dry-run mode: inspect but do not recover (default)",
    )
    recover_parser.add_argument(
        "--no-dry-run", action="store_true",
        help="Disable dry-run: actually recover the run",
    )
    recover_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    # daemon subcommand (Phase 17)
    daemon_parser = recovery_sub.add_parser(
        "daemon", help="Run the automatic recovery daemon"
    )
    daemon_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    daemon_parser.add_argument(
        "--once", action="store_true",
        help="Run a single scan cycle and exit",
    )
    daemon_parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Dry-run mode: log but do not recover (default)",
    )
    daemon_parser.add_argument(
        "--no-dry-run", action="store_true",
        help="Disable dry-run: actually perform recoveries",
    )
    daemon_parser.add_argument(
        "--interval-seconds", type=float, default=30.0,
        help="Seconds between scan cycles (default: 30)",
    )
    daemon_parser.add_argument(
        "--max-recoveries-per-scan", type=int, default=5,
        help="Max recoveries per scan cycle (default: 5)",
    )
    daemon_parser.add_argument(
        "--max-concurrent-recoveries", type=int, default=1,
        help="Max concurrent recoveries (default: 1)",
    )
    daemon_parser.add_argument(
        "--workflow-name", default=None,
        help="Filter by workflow name",
    )
    daemon_parser.add_argument(
        "--tenant-id", default=None,
        help="Filter by tenant ID",
    )
    daemon_parser.add_argument(
        "--json", action="store_true", help="Output tick results as JSON",
    )

    # Phase 18: status subcommand
    status_parser = recovery_sub.add_parser(
        "status", help="Show recovery system status"
    )
    status_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    status_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    # Phase 18: history subcommand
    history_parser = recovery_sub.add_parser(
        "history", help="Show recovery history for a run"
    )
    history_parser.add_argument("run_id", help="Run ID to query")
    history_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    history_parser.add_argument(
        "--limit", type=int, default=50, help="Max events to show (default: 50)"
    )
    history_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    args = parser.parse_args()

    if args.command == "eval" and args.eval_command == "run":
        return asyncio.run(_cmd_eval_run(args.suite, args.config))

    if args.command == "trace" and args.trace_command == "list":
        return asyncio.run(_cmd_trace_list(args))

    if args.command == "trace" and args.trace_command == "show":
        return asyncio.run(_cmd_trace_show(args))

    if args.command == "recovery" and args.recovery_command == "scan":
        return asyncio.run(_cmd_recovery_scan(args))

    if args.command == "recovery" and args.recovery_command == "inspect":
        return asyncio.run(_cmd_recovery_inspect(args))

    if args.command == "recovery" and args.recovery_command == "recover":
        return asyncio.run(_cmd_recovery_recover_admin(args))

    if args.command == "recovery" and args.recovery_command == "daemon":
        return asyncio.run(_cmd_recovery_daemon(args))

    if args.command == "recovery" and args.recovery_command == "status":
        return asyncio.run(_cmd_recovery_status(args))

    if args.command == "recovery" and args.recovery_command == "history":
        return asyncio.run(_cmd_recovery_history(args))

    if args.command == "recovery" and args.recovery_command == "scan":
        return asyncio.run(_cmd_recovery_scan_admin(args))

    if args.command == "recovery" and args.recovery_command == "recover":
        return asyncio.run(_cmd_recovery_recover_admin(args))

    parser.print_help()
    return 0


async def _cmd_eval_run(suite_path: str, config_path: str) -> int:
    """Execute an eval suite and return exit code."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(config_path)
        suite = load_eval_suite(suite_path)
    except Exception as exc:
        print(f"Error loading config or eval suite: {exc}", file=sys.stderr)
        return 1

    runner = EvalRunner(app)
    result = await runner.run_suite(suite)
    result.print_summary()

    return 0 if result.passed else 1


async def _cmd_trace_list(args: argparse.Namespace) -> int:
    """List traces from the configured app."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    collector = getattr(app, "trace_collector", None)
    if collector is None:
        print("Trace collector is not configured for this app.", file=sys.stderr)
        print("Add 'observability.tracing.type: memory' to your config.", file=sys.stderr)
        return 1

    trace_ids = await collector.list_traces(
        tenant_id=args.tenant_id,
        run_id=args.run_id,
        limit=args.limit,
    )

    if not trace_ids:
        if args.json:
            print(json.dumps({"traces": [], "total": 0}))
        else:
            print("No traces found.")
        return 0

    # Build trace summaries
    traces: list[dict] = []
    for tid in trace_ids:
        events = await collector.get_events(tid)
        if args.event_type:
            events = [e for e in events if args.event_type in _event_type_str(e)]
        if not events:
            continue
        status = _infer_status(events)
        run_id = str(events[0].run_id or "")[:20]
        last_ts = events[-1].timestamp.isoformat()[:19] if events[-1].timestamp else "?"
        traces.append({
            "trace_id": tid,
            "run_id": run_id,
            "event_count": len(events),
            "status": str(status or "?"),
            "last_event_at": last_ts,
        })

    if args.json:
        print(json.dumps({"traces": traces, "total": len(traces)}))
    else:
        # Print header
        print(f"{'Trace ID':<20} {'Run ID':<20} {'Events':>7} {'Status':<12} {'Last Event'}")
        print("-" * 75)
        for t in traces:
            print(
                f"{t['trace_id']:<20} {t['run_id']:<20} {t['event_count']:>7} "
                f"{t['status']:<12} {t['last_event_at']}"
            )
    return 0


async def _cmd_trace_show(args: argparse.Namespace) -> int:
    """Show details of a specific trace."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    collector = getattr(app, "trace_collector", None)
    if collector is None:
        print("Trace collector is not configured for this app.", file=sys.stderr)
        return 1

    events = await collector.get_events(args.trace_id)
    if not events:
        print(f"Trace '{args.trace_id}' not found.", file=sys.stderr)
        return 1

    if args.json:
        _print_trace_json(events)
        return 0

    # Human-readable output
    run_id = events[0].run_id
    status = _infer_status(events)
    print(f"Trace: {args.trace_id}")
    print(f"Run: {run_id}")
    print(f"Status: {status}")
    print()
    for i, ev in enumerate(events, 1):
        ev_type = _event_type_str(ev)
        tool = getattr(ev, "tool_name", None)
        agent = getattr(ev, "agent_name", None)
        parts = [ev_type]
        if tool:
            parts.append(f"tool={tool}")
        if agent:
            parts.append(f"agent={agent}")
        print(f"{i}. {' '.join(parts)}")

    return 0


def _event_type_str(event: Any) -> str:
    """Extract the string value of an event's event_type field.

    Handles RunEventType enum members (which str() to 'RunEventType.RUN_STARTED')
    as well as plain strings.
    """
    val = getattr(event, "event_type", None)
    if val is None:
        return "?"
    # Enum member: use .value to get the string (e.g. "run.started")
    if hasattr(val, "value"):
        return str(val.value)
    return str(val)


def _print_trace_json(events: list) -> None:
    """Print trace events as JSON."""
    data = []
    for e in events:
        data.append({
            "event_id": getattr(e, "event_id", ""),
            "event_type": _event_type_str(e),
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            "run_id": e.run_id,
            "user_id": e.user_id,
            "tenant_id": e.tenant_id,
            "workflow_name": e.workflow_name,
            "agent_name": e.agent_name,
            "tool_name": e.tool_name,
            "approval_id": e.approval_id,
            "status": e.status,
            "duration_ms": e.duration_ms,
            "error": e.error,
            "data": e.data,
        })
    print(json.dumps(data, indent=2))


def _infer_status(events: list) -> str | None:
    """Infer overall run status from trace events."""
    failed_types = {"run.failed", "workflow.failed", "agent.failed", "tool.failed"}
    for e in events:
        et = _event_type_str(e)
        if et in failed_types:
            return "failed"
    for e in events:
        if _event_type_str(e) == "run.interrupted":
            return "interrupted"
    for e in events:
        if _event_type_str(e) == "run.completed":
            return "completed"
    return None


# -- Recovery commands (Phase 16.5) --


async def _cmd_recovery_scan(args: argparse.Namespace) -> int:
    """Scan for recovery candidates."""
    from agent_app.config.loader import build_app
    from agent_app.runtime.recovery_models import RecoveryScanConfig

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    config = RecoveryScanConfig(limit=args.limit, workflow_name=args.workflow)
    try:
        result = await app.scan_recovery_candidates(config)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        _print_recovery_scan_json(result)
    else:
        _print_recovery_scan_table(result)
    return 0


async def _cmd_recovery_inspect(args: argparse.Namespace) -> int:
    """Inspect a single recovery candidate."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    try:
        candidate = await app.inspect_recovery_candidate(args.run_id)
    except KeyError:
        print(f"Run '{args.run_id}' not found.", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(candidate.model_dump(mode="json"), indent=2, default=str))
    else:
        _print_candidate_detail(candidate)
    return 0


async def _cmd_recovery_recover(args: argparse.Namespace) -> int:
    """Manually recover a workflow run."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    try:
        result = await app.recover_workflow_run(
            workflow=args.workflow,
            run_id=args.run_id,
            recovered_by=args.recovered_by,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(
            {
                "run_id": result.run_id,
                "attempted": result.attempted,
                "recovered": result.recovered,
                "status": result.status,
                "lease_acquired": result.lease_acquired,
                "lease_released": result.lease_released,
                "error": result.error,
            },
            indent=2,
            default=str,
        ))

    if not result.attempted:
        print(f"Recovery not attempted: {result.error}", file=sys.stderr)
        return 1

    if result.recovered:
        print(f"Recovery succeeded for run '{result.run_id}'. Status: {result.status}")
        return 0
    else:
        print(f"Recovery failed for run '{result.run_id}': {result.error}", file=sys.stderr)
        return 1


def _print_recovery_scan_table(result: Any) -> None:
    """Print scan results as a table."""
    if not result.candidates:
        print(f"Scanned {result.total_scanned} runs. No recovery candidates found.")
        return

    print(f"Scanned {result.total_scanned} runs. {result.candidate_count} candidates:")
    print()
    header = f"{'Run ID':<20} {'Status':<12} {'Age(s)':>8} {'Lease':<12} {'Recommendation'}"
    print(header)
    print("-" * len(header))
    for c in result.candidates:
        lease_str = "expired" if c.lease_expired else (
            c.lease_owner or "none"
        )
        age = f"{c.age_seconds:.0f}" if c.age_seconds is not None else "?"
        print(
            f"{c.run_id:<20} {c.status:<12} {age:>8} {lease_str:<12} "
            f"{c.recommendation.value}"
        )
    if result.errors:
        print(f"\n{len(result.errors)} non-fatal errors during scan.")


def _print_recovery_scan_json(result: Any) -> None:
    """Print scan results as JSON."""
    data = {
        "scanned_at": result.scanned_at.isoformat(),
        "total_scanned": result.total_scanned,
        "candidate_count": result.candidate_count,
        "candidates": [
            {
                "run_id": c.run_id,
                "status": c.status,
                "age_seconds": c.age_seconds,
                "reasons": [r.value for r in c.reasons],
                "recommendation": c.recommendation.value,
                "lease_present": c.lease_present,
                "lease_owner": c.lease_owner,
                "lease_expired": c.lease_expired,
                "resumable": c.resumable,
            }
            for c in result.candidates
        ],
        "errors": result.errors,
    }
    print(json.dumps(data, indent=2, default=str))


def _print_candidate_detail(candidate: Any) -> None:
    """Print detailed candidate information."""
    print(f"Run ID:       {candidate.run_id}")
    print(f"Workflow:     {candidate.workflow_name or 'unknown'}")
    print(f"Status:       {candidate.status}")
    print(f"Updated At:   {candidate.updated_at.isoformat() if candidate.updated_at else 'unknown'}")
    print(f"Age:          {f'{candidate.age_seconds:.0f}s' if candidate.age_seconds is not None else 'unknown'}")
    print(f"Resumable:    {candidate.resumable}")
    print()
    print(f"Reasons:")
    for r in candidate.reasons:
        print(f"  - {r.value}")
    print()
    print(f"Recommendation: {candidate.recommendation.value}")
    print()
    print(f"Lease present:   {candidate.lease_present}")
    print(f"Lease owner:     {candidate.lease_owner or 'none'}")
    print(f"Lease expired:   {candidate.lease_expired}")
    print(f"Lease expires:   {candidate.lease_expires_at.isoformat() if candidate.lease_expires_at else 'n/a'}")
    if candidate.resume_plan_summary:
        print()
        print("Resume plan:")
        for k, v in candidate.resume_plan_summary.items():
            print(f"  {k}: {v}")
    if candidate.recovery_plan_summary:
        print()
        print("Recovery plan:")
        for k, v in candidate.recovery_plan_summary.items():
            print(f"  {k}: {v}")
    if candidate.error:
        print()
        print(f"Error: {candidate.error}")


# -- Recovery daemon command (Phase 17) --


async def _cmd_recovery_daemon(args: argparse.Namespace) -> int:
    """Run the automatic recovery daemon."""
    from agent_app.config.loader import build_app
    from agent_app.runtime.recovery_models import AutoRecoveryPolicy

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    try:
        daemon = app.create_recovery_daemon()
    except RuntimeError as exc:
        print(f"Error creating recovery daemon: {exc}", file=sys.stderr)
        return 1

    # Override policy from CLI args
    policy = daemon.policy
    if args.no_dry_run:
        policy.dry_run = False
    elif args.dry_run:
        policy.dry_run = True

    if args.interval_seconds != 30.0:
        policy.interval_seconds = args.interval_seconds
    if args.max_recoveries_per_scan != 5:
        policy.max_recoveries_per_scan = args.max_recoveries_per_scan
    if args.max_concurrent_recoveries != 1:
        policy.max_concurrent_recoveries = args.max_concurrent_recoveries
    if args.workflow_name:
        policy.workflow_name = args.workflow_name
    if args.tenant_id:
        policy.tenant_id = args.tenant_id

    daemon.policy = policy

    mode = "DRY-RUN" if policy.dry_run else "LIVE"
    print(f"Recovery daemon starting ({mode} mode)")
    print(f"  Interval: {policy.interval_seconds}s")
    print(f"  Max recoveries per scan: {policy.max_recoveries_per_scan}")
    print(f"  Max concurrent: {policy.max_concurrent_recoveries}")
    if policy.workflow_name:
        print(f"  Workflow filter: {policy.workflow_name}")
    if policy.tenant_id:
        print(f"  Tenant filter: {policy.tenant_id}")
    print()

    if args.once:
        try:
            result = await daemon.run_once()
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        _print_daemon_tick_result(result, args.json)
        return 1 if result.failed_count > 0 else 0

    # run_forever with graceful shutdown
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        print("\nShutting down recovery daemon...")
        stop_event.set()
        daemon.stop()

    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(
            getattr(signal, "SIGINT", None) or signal.SIGTERM,
            _signal_handler,
        )
    except (NotImplementedError, AttributeError):
        # Windows or non-main thread — skip signal handling
        pass

    await daemon.run_forever(stop_event=stop_event)
    return 0


def _print_daemon_tick_result(result: Any, json_output: bool) -> None:
    """Print daemon tick results."""
    if json_output:
        data = {
            "scanned_count": result.scanned_count,
            "selected_count": result.selected_count,
            "recovered_count": result.recovered_count,
            "skipped_count": result.skipped_count,
            "failed_count": result.failed_count,
            "dry_run": result.dry_run,
            "selected_run_ids": result.selected_run_ids,
            "recovered_run_ids": result.recovered_run_ids,
            "skipped": result.skipped,
            "failures": result.failures,
        }
        print(json.dumps(data, indent=2, default=str))
        return

    print(f"Tick result (dry_run={result.dry_run}):")
    print(f"  Scanned:  {result.scanned_count}")
    print(f"  Selected: {result.selected_count}")
    print(f"  Recovered:{result.recovered_count}")
    print(f"  Skipped:  {result.skipped_count}")
    print(f"  Failed:   {result.failed_count}")
    print()

    if result.recovered_run_ids:
        print("Recovered runs:")
        for rid in result.recovered_run_ids:
            print(f"  - {rid}")
    if result.skipped:
        print("Skipped:")
        for s in result.skipped:
            print(f"  - {s['run_id']}: {s['reason']}")
    if result.failures:
        print("Failures:")
        for f in result.failures:
            print(f"  - {f['run_id']}: {f.get('error', f)}")


# -- Phase 18: Observability CLI commands --


async def _cmd_recovery_status(args: argparse.Namespace) -> int:
    """Show recovery system status."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    try:
        status = app.get_recovery_system_status()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "enabled": status.enabled,
            "dry_run": status.dry_run,
            "daemon_configured": status.daemon_configured,
            "scanner_available": status.scanner_available,
            "recovery_service_available": status.recovery_service_available,
            "last_tick_at": status.last_tick_at.isoformat() if status.last_tick_at else None,
            "policy": status.policy.model_dump(mode="json") if status.policy else None,
        }
        print(json.dumps(data, indent=2, default=str))
        return 0

    print("Recovery System Status")
    print("=" * 40)
    print(f"  Enabled:                    {status.enabled}")
    print(f"  Dry-run:                    {status.dry_run}")
    print(f"  Daemon configured:          {status.daemon_configured}")
    print(f"  Scanner available:          {status.scanner_available}")
    print(f"  Recovery service available: {status.recovery_service_available}")
    print(f"  Last tick at:               {status.last_tick_at.isoformat() if status.last_tick_at else 'never'}")
    print()

    if status.policy:
        p = status.policy
        print("Current Policy:")
        print(f"  interval_seconds:             {p.interval_seconds}s")
        print(f"  stale_after_seconds:          {p.stale_after_seconds}s")
        print(f"  max_candidates_per_scan:      {p.max_candidates_per_scan}")
        print(f"  max_recoveries_per_scan:      {p.max_recoveries_per_scan}")
        print(f"  max_concurrent_recoveries:    {p.max_concurrent_recoveries}")
        print(f"  statuses:                     {', '.join(p.statuses)}")
        print(f"  recover_failed:               {p.recover_failed}")
        print(f"  recover_stale_running:        {p.recover_stale_running}")
        print(f"  recover_compensating:         {p.recover_compensating}")
        print(f"  include_completed:            {p.include_completed}")
        if p.workflow_name:
            print(f"  workflow_name:                {p.workflow_name}")
        if p.tenant_id:
            print(f"  tenant_id:                    {p.tenant_id}")

    return 0


async def _cmd_recovery_history(args: argparse.Namespace) -> int:
    """Show recovery history for a run."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    events = await app.get_recovery_history(args.run_id, limit=args.limit)

    if not events:
        if args.json:
            print(json.dumps({"run_id": args.run_id, "events": [], "total": 0}))
        else:
            print(f"No recovery history found for run '{args.run_id}'.")
        return 0

    if args.json:
        data = {
            "run_id": args.run_id,
            "total": len(events),
            "events": [
                {
                    "event_id": e.event_id,
                    "event_type": e.event_type,
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                    "user_id": e.user_id,
                    "tenant_id": e.tenant_id,
                    "data": e.data,
                }
                for e in events
            ],
        }
        print(json.dumps(data, indent=2, default=str))
        return 0

    print(f"Recovery history for run '{args.run_id}' ({len(events)} events):")
    print()
    for i, ev in enumerate(events, 1):
        ts = ev.created_at.isoformat()[:19] if ev.created_at else "?"
        print(f"{i}. [{ts}] {ev.event_type}")
        if ev.data:
            for k, v in ev.data.items():
                print(f"     {k}: {v}")
    return 0


async def _cmd_recovery_scan_admin(args: argparse.Namespace) -> int:
    """Admin scan: run a recovery scan via run_recovery_scan_once."""
    from agent_app.config.loader import build_app
    from agent_app.runtime.recovery_models import RecoveryScanConfig

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    # The scan subcommand now delegates to run_recovery_scan_once (dry-run by default)
    policy = None
    if hasattr(args, "no_dry_run") and args.no_dry_run:
        from agent_app.runtime.recovery_models import AutoRecoveryPolicy
        policy = AutoRecoveryPolicy(dry_run=False)

    try:
        result = await app.run_recovery_scan_once(policy=policy)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        _print_daemon_tick_result(result, json_output=True)
    else:
        _print_daemon_tick_result(result, json_output=False)
        print()
        print("Note: dry-run mode — no actual recovery was attempted.")
        print("Use --no-dry-run to enable live recovery.")
    return 1 if result.failed_count > 0 else 0


async def _cmd_recovery_recover_admin(args: argparse.Namespace) -> int:
    """Admin recover: recover a specific run with dry-run support."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    dry_run = not getattr(args, "no_dry_run", False)
    workflow = getattr(args, "workflow", "")

    try:
        result = await app.recover_run(
            run_id=args.run_id,
            workflow=workflow,
            dry_run=dry_run,
            recovered_by=getattr(args, "recovered_by", "admin-cli"),
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        output = {
            "run_id": result.run_id,
            "attempted": result.attempted,
            "recovered": result.recovered,
            "status": result.status,
            "dry_run": dry_run,
            "error": result.error,
        }
        print(json.dumps(output, indent=2, default=str))

    if not result.attempted:
        if result.error:
            print(f"Recovery not attempted: {result.error}", file=sys.stderr)
        return 1

    if result.recovered:
        print(f"Recovery succeeded for run '{result.run_id}'. Status: {result.status}")
        return 0
    else:
        err = result.error or {}
        print(f"Recovery failed for run '{result.run_id}': {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
