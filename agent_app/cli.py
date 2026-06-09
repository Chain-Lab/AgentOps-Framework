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
        "--workflow", required=True, help="Workflow name"
    )
    recover_parser.add_argument(
        "--recovered-by", required=True, help="Identity of the operator"
    )
    recover_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    recover_parser.add_argument(
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
        return asyncio.run(_cmd_recovery_recover(args))

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
