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

    # Phase 24: policy commands
    policy_parser = subparsers.add_parser("policy", help="Policy commands")
    policy_sub = policy_parser.add_subparsers(dest="policy_command")

    validate_parser = policy_sub.add_parser("validate", help="Validate policy config")
    validate_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )

    simulate_parser = policy_sub.add_parser("simulate", help="Simulate policy decision")
    simulate_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    simulate_parser.add_argument("--tool", required=True, help="Tool name to simulate")
    simulate_parser.add_argument("--risk", default="low", help="Risk level (default: low)")
    simulate_parser.add_argument("--workflow-type", default=None, help="Workflow type")
    simulate_parser.add_argument("--agent-name", default=None, help="Agent name")
    simulate_parser.add_argument("--target-agent", default=None, help="Target agent")
    simulate_parser.add_argument("--user-id", default=None, help="User ID")
    simulate_parser.add_argument("--tenant-id", default=None, help="Tenant ID")
    simulate_parser.add_argument("--role", action="append", default=[], help="User role (repeatable)")
    simulate_parser.add_argument("--permission", action="append", default=[], help="Permission (repeatable)")
    simulate_parser.add_argument("--json", action="store_true", help="Output as JSON")

    explain_parser = policy_sub.add_parser("explain", help="Explain policy decision")
    explain_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    explain_parser.add_argument("--tool", required=True, help="Tool name to explain")
    explain_parser.add_argument("--risk", default="low", help="Risk level (default: low)")
    explain_parser.add_argument("--workflow-type", default=None, help="Workflow type")
    explain_parser.add_argument("--agent-name", default=None, help="Agent name")
    explain_parser.add_argument("--target-agent", default=None, help="Target agent")
    explain_parser.add_argument("--user-id", default=None, help="User ID")
    explain_parser.add_argument("--tenant-id", default=None, help="Tenant ID")
    explain_parser.add_argument("--role", action="append", default=[], help="User role (repeatable)")
    explain_parser.add_argument("--permission", action="append", default=[], help="Permission (repeatable)")
    explain_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Phase 25: policy decisions subcommands
    decisions_parser = policy_sub.add_parser("decisions", help="Query policy decisions")
    decisions_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    decisions_parser.add_argument("--run-id", default=None, help="Filter by run ID")
    decisions_parser.add_argument("--tenant-id", default=None, help="Filter by tenant ID")
    decisions_parser.add_argument("--agent-name", default=None, help="Filter by agent name")
    decisions_parser.add_argument("--tool-name", default=None, help="Filter by tool name")
    decisions_parser.add_argument("--rule-name", default=None, help="Filter by rule name")
    decisions_parser.add_argument("--action", default=None, help="Filter by action")
    decisions_parser.add_argument("--limit", type=int, default=20, help="Max results")
    decisions_parser.add_argument("--offset", type=int, default=0, help="Skip results")
    decisions_parser.add_argument("--json", action="store_true", help="Output as JSON")

    report_parser = policy_sub.add_parser("report", help="Policy decision report")
    report_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    report_parser.add_argument("--run-id", default=None, help="Filter by run ID")
    report_parser.add_argument("--tenant-id", default=None, help="Filter by tenant ID")
    report_parser.add_argument("--tool-name", default=None, help="Filter by tool name")
    report_parser.add_argument("--rule-name", default=None, help="Filter by rule name")
    report_parser.add_argument("--action", default=None, help="Filter by action")
    report_parser.add_argument("--limit", type=int, default=1000, help="Max decisions")
    report_parser.add_argument("--json", action="store_true", help="Output as JSON")

    export_parser = policy_sub.add_parser("export", help="Export policy decisions")
    export_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    export_parser.add_argument(
        "--format", choices=["jsonl", "csv"], default="jsonl", help="Export format"
    )
    export_parser.add_argument(
        "--output", required=True, help="Output file path"
    )
    export_parser.add_argument("--run-id", default=None, help="Filter by run ID")
    export_parser.add_argument("--tenant-id", default=None, help="Filter by tenant ID")
    export_parser.add_argument("--limit", type=int, default=10000, help="Max records")

    # Phase 27: policy replay subcommand
    replay_parser = policy_sub.add_parser("replay", help="Replay policy decisions against current policy")
    replay_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    replay_parser.add_argument("--tenant-id", default=None, help="Filter by tenant ID")
    replay_parser.add_argument("--tool-name", default=None, help="Filter by tool name")
    replay_parser.add_argument("--rule-id", default=None, help="Filter by original rule name")
    replay_parser.add_argument("--limit", type=int, default=100, help="Max decisions to replay")
    replay_parser.add_argument("--json", action="store_true", help="Output as JSON")
    replay_parser.add_argument(
        "--background", action="store_true",
        help="Submit as background job instead of running synchronously",
    )
    replay_parser.add_argument(
        "--requested-by", default=None,
        help="Identity of who requested the replay (for background jobs)",
    )
    replay_parser.add_argument(
        "--store", default="memory", choices=["memory", "sqlite"],
        help="Replay result store type (default: memory)",
    )
    replay_parser.add_argument(
        "--db-path", default=None,
        help="SQLite database path (for --store sqlite)",
    )

    # Phase 28: replay run-job subcommand
    run_job_parser = policy_sub.add_parser("run-job", help="Run a queued replay job")
    run_job_parser.add_argument(
        "job_id", help="Job ID to run"
    )
    run_job_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    run_job_parser.add_argument(
        "--store", default="memory", choices=["memory", "sqlite"],
        help="Replay result store type (default: memory)",
    )
    run_job_parser.add_argument(
        "--db-path", default=None,
        help="SQLite database path (for --store sqlite)",
    )
    run_job_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    # Phase 28: replay jobs subcommand
    jobs_parser = policy_sub.add_parser("jobs", help="List replay jobs")
    jobs_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    jobs_parser.add_argument(
        "--store", default="memory", choices=["memory", "sqlite"],
        help="Job store type (default: memory)",
    )
    jobs_parser.add_argument(
        "--db-path", default=None,
        help="SQLite database path (for --store sqlite)",
    )
    jobs_parser.add_argument(
        "--limit", type=int, default=20, help="Max jobs to show (default: 20)"
    )
    jobs_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    # Phase 29: policy bundle subcommands
    bundle_parser = policy_sub.add_parser("bundle", help="Policy bundle commands")
    bundle_sub = bundle_parser.add_subparsers(dest="bundle_command")

    bundle_create_parser = bundle_sub.add_parser("create", help="Create a policy bundle")
    bundle_create_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    bundle_create_parser.add_argument("--name", required=True, help="Bundle name")
    bundle_create_parser.add_argument("--version", required=True, help="Bundle version")
    bundle_create_parser.add_argument(
        "--config-path", required=True, help="Path to policy config file"
    )
    bundle_create_parser.add_argument(
        "--description", default=None, help="Bundle description"
    )
    bundle_create_parser.add_argument(
        "--created-by", default=None, help="Creator identity"
    )
    bundle_create_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    bundle_list_parser = bundle_sub.add_parser("list", help="List policy bundles")
    bundle_list_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    bundle_list_parser.add_argument(
        "--limit", type=int, default=20, help="Max bundles to show"
    )
    bundle_list_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    bundle_active_parser = bundle_sub.add_parser(
        "active", help="Show the active policy bundle"
    )
    bundle_active_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    bundle_active_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    bundle_promote_parser = bundle_sub.add_parser(
        "promote", help="Promote a bundle to ACTIVE"
    )
    bundle_promote_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    bundle_promote_parser.add_argument(
        "--bundle-id", required=True, help="Bundle ID to promote"
    )
    bundle_promote_parser.add_argument(
        "--promoted-by", default=None, help="Promoter identity"
    )
    bundle_promote_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    bundle_rollback_parser = bundle_sub.add_parser(
        "rollback", help="Rollback to a previous bundle"
    )
    bundle_rollback_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    bundle_rollback_parser.add_argument(
        "--bundle-id", required=True, help="Bundle ID to rollback to"
    )
    bundle_rollback_parser.add_argument(
        "--rolled-back-by", default=None, help="Operator identity"
    )
    bundle_rollback_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    # Phase 29: policy gate subcommands
    gate_parser = policy_sub.add_parser("gate", help="Policy gate commands")
    gate_sub = gate_parser.add_subparsers(dest="gate_command")

    gate_run_parser = gate_sub.add_parser(
        "run", help="Run release gate evaluation for a bundle"
    )
    gate_run_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    gate_run_parser.add_argument(
        "--bundle-id", required=True, help="Bundle ID to evaluate"
    )
    gate_run_parser.add_argument(
        "--limit", type=int, default=None, help="Max decisions to replay"
    )
    gate_run_parser.add_argument(
        "--tenant-id", default=None, help="Filter by tenant"
    )
    gate_run_parser.add_argument(
        "--tool-name", default=None, help="Filter by tool name"
    )
    gate_run_parser.add_argument(
        "--rule-id", default=None, help="Filter by original rule"
    )
    gate_run_parser.add_argument(
        "--created-by", default=None, help="Evaluator identity"
    )
    gate_run_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    gate_list_parser = gate_sub.add_parser(
        "list", help="List gate evaluation results"
    )
    gate_list_parser.add_argument(
        "--config", required=True, help="Path to agentapp.yaml config"
    )
    gate_list_parser.add_argument(
        "--bundle-id", default=None, help="Filter by bundle ID"
    )
    gate_list_parser.add_argument(
        "--limit", type=int, default=20, help="Max results to show"
    )
    gate_list_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    # Phase 30: policy promotion subcommands
    promotion_parser = policy_sub.add_parser("promotion", help="Policy promotion commands")
    promo_sub = promotion_parser.add_subparsers(dest="promotion_command")

    promo_request_parser = promo_sub.add_parser("request", help="Request promotion of a policy bundle")
    promo_request_parser.add_argument("--config", required=True)
    promo_request_parser.add_argument("--bundle-id", required=True)
    promo_request_parser.add_argument("--actor-id", required=True)
    promo_request_parser.add_argument("--permissions", action="append", default=[])
    promo_request_parser.add_argument("--reason", default=None)
    promo_request_parser.add_argument("--json", action="store_true")

    promo_list_parser = promo_sub.add_parser("list", help="List promotion requests")
    promo_list_parser.add_argument("--config", required=True)
    promo_list_parser.add_argument("--status", default=None)
    promo_list_parser.add_argument("--limit", type=int, default=20)
    promo_list_parser.add_argument("--json", action="store_true")

    promo_approve_parser = promo_sub.add_parser("approve", help="Approve a promotion request")
    promo_approve_parser.add_argument("--config", required=True)
    promo_approve_parser.add_argument("--promotion-id", required=True)
    promo_approve_parser.add_argument("--actor-id", required=True)
    promo_approve_parser.add_argument("--permissions", action="append", default=[])
    promo_approve_parser.add_argument("--reason", default=None)
    promo_approve_parser.add_argument("--json", action="store_true")

    promo_reject_parser = promo_sub.add_parser("reject", help="Reject a promotion request")
    promo_reject_parser.add_argument("--config", required=True)
    promo_reject_parser.add_argument("--promotion-id", required=True)
    promo_reject_parser.add_argument("--actor-id", required=True)
    promo_reject_parser.add_argument("--permissions", action="append", default=[])
    promo_reject_parser.add_argument("--reason", default=None)
    promo_reject_parser.add_argument("--json", action="store_true")

    promo_execute_parser = promo_sub.add_parser("execute", help="Execute an approved promotion")
    promo_execute_parser.add_argument("--config", required=True)
    promo_execute_parser.add_argument("--promotion-id", required=True)
    promo_execute_parser.add_argument("--actor-id", required=True)
    promo_execute_parser.add_argument("--permissions", action="append", default=[])
    promo_execute_parser.add_argument("--bypass-gate", action="store_true")
    promo_execute_parser.add_argument("--bypass-reason", default=None)
    promo_execute_parser.add_argument("--environment", default="prod", help="Target environment (Phase 31)")
    promo_execute_parser.add_argument("--reason", default=None, help="Activation reason (Phase 31)")
    promo_execute_parser.add_argument("--json", action="store_true")

    # Phase 42: policy promotion gate subcommands
    promo_gate_parser = promo_sub.add_parser("gate", help="Promotion gate lifecycle commands")
    promo_gate_sub = promo_gate_parser.add_subparsers(dest="promotion_gate_command")

    gate_require_parser = promo_gate_sub.add_parser("require", help="Create a gate requirement for a promotion")
    gate_require_parser.add_argument("--config", required=True)
    gate_require_parser.add_argument("--promotion-id", required=True)
    gate_require_parser.add_argument("--max-age-seconds", type=int, default=None)
    gate_require_parser.add_argument("--actor-id", default=None)
    gate_require_parser.add_argument("--permissions", action="append", default=[])

    gate_run_parser = promo_gate_sub.add_parser("run", help="Run simulation + gate and attach to promotion")
    gate_run_parser.add_argument("--config", required=True)
    gate_run_parser.add_argument("--promotion-id", required=True)
    gate_run_parser.add_argument("--rules-file", required=True, help="YAML file with candidate rules")
    gate_run_parser.add_argument("--gate-rules-file", default=None, help="YAML file with gate rules")
    gate_run_parser.add_argument("--actor-id", default=None)
    gate_run_parser.add_argument("--permissions", action="append", default=[])
    gate_run_parser.add_argument("--since", default=None, help="ISO 8601 datetime for audit window start")
    gate_run_parser.add_argument("--until", default=None, help="ISO 8601 datetime for audit window end")
    gate_run_parser.add_argument("--limit", type=int, default=None, help="Max audit cases to replay")

    gate_attach_parser = promo_gate_sub.add_parser("attach", help="Attach an existing gate result to a promotion")
    gate_attach_parser.add_argument("--config", required=True)
    gate_attach_parser.add_argument("--promotion-id", required=True)
    gate_attach_parser.add_argument("--gate-result-id", required=True)
    gate_attach_parser.add_argument("--simulation-id", default=None)
    gate_attach_parser.add_argument("--actor-id", default=None)
    gate_attach_parser.add_argument("--permissions", action="append", default=[])

    gate_status_parser = promo_gate_sub.add_parser("status", help="Show gate requirement status for a promotion")
    gate_status_parser.add_argument("--config", required=True)
    gate_status_parser.add_argument("--promotion-id", required=True)
    gate_status_parser.add_argument("--json", action="store_true")

    # Phase 31: policy activation subcommands
    activation_parser = policy_sub.add_parser("activation", help="Policy activation commands")
    activation_sub = activation_parser.add_subparsers(dest="activation_command")

    activation_list_parser = activation_sub.add_parser("list", help="List policy activations")
    activation_list_parser.add_argument("--config", required=True)
    activation_list_parser.add_argument("--environment", default=None)
    activation_list_parser.add_argument("--json", action="store_true")

    activation_active_parser = activation_sub.add_parser("active", help="Show active policy bundle for an environment")
    activation_active_parser.add_argument("--config", required=True)
    activation_active_parser.add_argument("--environment", default="prod")
    activation_active_parser.add_argument("--json", action="store_true")

    # Phase 32: activation rollback subcommand
    activation_rollback_parser = activation_sub.add_parser("rollback", help="Roll back an environment to a previous activation")
    activation_rollback_parser.add_argument("--config", required=True)
    activation_rollback_parser.add_argument("--environment", dest="env_name", required=True, help="Environment to roll back")
    activation_rollback_parser.add_argument("--actor-id", required=True, help="Who is rolling back")
    activation_rollback_parser.add_argument("--reason", default=None, help="Rollback reason")
    activation_rollback_parser.add_argument("--target-activation-id", default=None, help="Specific activation to roll back to")
    activation_rollback_parser.add_argument("--permissions", action="append", default=[])
    activation_rollback_parser.add_argument("--json", action="store_true")

    # Phase 32: policy environment subcommands
    environment_parser = policy_sub.add_parser("environment", help="Policy environment management (Phase 32)")
    environment_sub = environment_parser.add_subparsers(dest="environment_command")

    env_list_parser = environment_sub.add_parser("list", help="List policy environment states")
    env_list_parser.add_argument("--config", required=True, help="Config file path")
    env_list_parser.add_argument("--permissions", action="append", default=[])
    env_list_parser.add_argument("--json", action="store_true")

    env_disable_parser = environment_sub.add_parser("disable", help="Disable a policy environment")
    env_disable_parser.add_argument("--config", required=True)
    env_disable_parser.add_argument("--environment", dest="env_name", required=True, help="Environment to disable")
    env_disable_parser.add_argument("--actor-id", required=True, help="Who is disabling")
    env_disable_parser.add_argument("--reason", required=True, help="Reason for disabling (required)")
    env_disable_parser.add_argument("--permissions", action="append", default=[])
    env_disable_parser.add_argument("--json", action="store_true")

    env_enable_parser = environment_sub.add_parser("enable", help="Re-enable a disabled policy environment")
    env_enable_parser.add_argument("--config", required=True)
    env_enable_parser.add_argument("--environment", dest="env_name", required=True, help="Environment to enable")
    env_enable_parser.add_argument("--actor-id", required=True, help="Who is enabling")
    env_enable_parser.add_argument("--reason", default=None, help="Reason for enabling")
    env_enable_parser.add_argument("--permissions", action="append", default=[])
    env_enable_parser.add_argument("--json", action="store_true")

    # Phase 33: policy ring subcommands
    ring_parser = policy_sub.add_parser("ring", help="Release ring management (Phase 33)")
    ring_sub = ring_parser.add_subparsers(dest="ring_command")

    ring_list_parser = ring_sub.add_parser("list", help="List release rings for an environment")
    ring_list_parser.add_argument("--config", required=True, help="Config file path")
    ring_list_parser.add_argument("--environment", required=True, help="Environment name")
    ring_list_parser.add_argument("--permissions", action="append", default=[])
    ring_list_parser.add_argument("--json", action="store_true")

    ring_create_parser = ring_sub.add_parser("create", help="Create a release ring")
    ring_create_parser.add_argument("--config", required=True, help="Config file path")
    ring_create_parser.add_argument("--environment", required=True, help="Environment name")
    ring_create_parser.add_argument("--name", required=True, help="Ring name")
    ring_create_parser.add_argument("--actor-id", required=True, help="Who is creating")
    ring_create_parser.add_argument("--description", default=None, help="Ring description")
    ring_create_parser.add_argument("--is-default", action="store_true", default=False, help="Set as default ring")
    ring_create_parser.add_argument("--permissions", action="append", default=[])
    ring_create_parser.add_argument("--json", action="store_true")

    ring_assign_parser = ring_sub.add_parser("assign", help="Assign an activation to a ring")
    ring_assign_parser.add_argument("--config", required=True, help="Config file path")
    ring_assign_parser.add_argument("--environment", required=True, help="Environment name")
    ring_assign_parser.add_argument("--ring", dest="ring_name", required=True, help="Ring name")
    ring_assign_parser.add_argument("--activation-id", required=True, help="Activation ID to assign")
    ring_assign_parser.add_argument("--actor-id", required=True, help="Who is assigning")
    ring_assign_parser.add_argument("--reason", default=None, help="Reason for assignment")
    ring_assign_parser.add_argument("--permissions", action="append", default=[])
    ring_assign_parser.add_argument("--json", action="store_true")

    ring_promote_parser = ring_sub.add_parser("promote", help="Promote canary ring activation to stable ring")
    ring_promote_parser.add_argument("--config", required=True, help="Config file path")
    ring_promote_parser.add_argument("--environment", required=True, help="Environment name")
    ring_promote_parser.add_argument("--from-ring", dest="from_ring", required=True, help="Source canary ring name")
    ring_promote_parser.add_argument("--to-ring", dest="to_ring", required=True, help="Target stable ring name")
    ring_promote_parser.add_argument("--actor-id", required=True, help="Who is promoting")
    ring_promote_parser.add_argument("--reason", default=None, help="Reason for promotion")
    ring_promote_parser.add_argument("--permissions", action="append", default=[])
    ring_promote_parser.add_argument("--json", action="store_true")

    ring_disable_parser = ring_sub.add_parser("disable", help="Disable a release ring")
    ring_disable_parser.add_argument("--config", required=True, help="Config file path")
    ring_disable_parser.add_argument("--environment", required=True, help="Environment name")
    ring_disable_parser.add_argument("--ring", dest="ring_name", required=True, help="Ring name")
    ring_disable_parser.add_argument("--actor-id", required=True, help="Who is disabling")
    ring_disable_parser.add_argument("--reason", default=None, help="Reason for disabling")
    ring_disable_parser.add_argument("--permissions", action="append", default=[])
    ring_disable_parser.add_argument("--json", action="store_true")

    ring_enable_parser = ring_sub.add_parser("enable", help="Enable a disabled release ring")
    ring_enable_parser.add_argument("--config", required=True, help="Config file path")
    ring_enable_parser.add_argument("--environment", required=True, help="Environment name")
    ring_enable_parser.add_argument("--ring", dest="ring_name", required=True, help="Ring name")
    ring_enable_parser.add_argument("--actor-id", required=True, help="Who is enabling")
    ring_enable_parser.add_argument("--permissions", action="append", default=[])
    ring_enable_parser.add_argument("--json", action="store_true")

    # Phase 33: policy canary subcommands
    canary_parser = policy_sub.add_parser("canary", help="Canary evaluation (Phase 33)")
    canary_sub = canary_parser.add_subparsers(dest="canary_command")

    canary_eval_parser = canary_sub.add_parser("eval", help="Run canary evaluation against an activation")
    canary_eval_parser.add_argument("--config", required=True, help="Config file path")
    canary_eval_parser.add_argument("--environment", required=True, help="Environment name")
    canary_eval_parser.add_argument("--ring", dest="ring_name", required=True, help="Ring name")
    canary_eval_parser.add_argument("--activation-id", required=True, help="Activation ID to evaluate")
    canary_eval_parser.add_argument("--suite", dest="suite_path", required=True, help="Path to eval suite YAML")
    canary_eval_parser.add_argument("--json", action="store_true")

    # Phase 34: policy reload subcommands
    reload_parser = policy_sub.add_parser("reload", help="Policy reload management (Phase 34)")
    reload_sub = reload_parser.add_subparsers(dest="reload_command")

    reload_request_parser = reload_sub.add_parser("request", help="Request a policy reload")
    reload_request_parser.add_argument("--config", required=True, help="Config file path")
    reload_request_parser.add_argument("--environment", default=None, help="Environment to reload")
    reload_request_parser.add_argument("--ring", dest="ring_name", default=None, help="Ring to reload")
    reload_request_parser.add_argument("--actor-id", required=True, help="Who is requesting the reload")
    reload_request_parser.add_argument("--reason", default=None, help="Reason for the reload")
    reload_request_parser.add_argument("--json", action="store_true")

    reload_status_parser = reload_sub.add_parser("status", help="Show policy cache status")
    reload_status_parser.add_argument("--config", required=True, help="Config file path")
    reload_status_parser.add_argument("--json", action="store_true")

    # Phase 34: policy events subcommands
    events_parser = policy_sub.add_parser("events", help="Policy change events (Phase 34)")
    events_sub = events_parser.add_subparsers(dest="events_command")

    events_list_parser = events_sub.add_parser("list", help="List policy change events")
    events_list_parser.add_argument("--config", required=True, help="Config file path")
    events_list_parser.add_argument("--environment", default=None, help="Filter by environment")
    events_list_parser.add_argument("--ring", dest="ring_name", default=None, help="Filter by ring")
    events_list_parser.add_argument("--limit", type=int, default=20, help="Max events to show")
    events_list_parser.add_argument("--json", action="store_true")

    # Phase 34: policy routing subcommands
    routing_parser = policy_sub.add_parser("routing", help="Policy routing simulation (Phase 34)")
    routing_sub = routing_parser.add_subparsers(dest="routing_command")

    routing_simulate_parser = routing_sub.add_parser("simulate", help="Simulate policy routing")
    routing_simulate_parser.add_argument("--config", required=True, help="Config file path")
    routing_simulate_parser.add_argument("--environment", required=True, help="Target environment")
    routing_simulate_parser.add_argument("--actor-id", required=True, help="Actor ID for routing context")
    routing_simulate_parser.add_argument("--tenant-id", default="default", help="Tenant ID for routing context")
    routing_simulate_parser.add_argument("--permissions", action="append", default=[])
    routing_simulate_parser.add_argument("--json", action="store_true")

    # Phase 35: rollout commands
    rollout_parser = policy_sub.add_parser("rollout", help="Rollout plan management (Phase 35)")
    rollout_sub = rollout_parser.add_subparsers(dest="rollout_command")

    rollout_create = rollout_sub.add_parser("create", help="Create a rollout plan")
    rollout_create.add_argument("--config", required=True, help="Config file path")
    rollout_create.add_argument("--name", required=True, help="Rollout plan name")
    rollout_create.add_argument("--bundle-id", required=True, help="Bundle ID to roll out")
    rollout_create.add_argument("--steps-file", required=True, help="YAML file with rollout steps")
    rollout_create.add_argument("--actor-id", required=True, help="Actor ID")
    rollout_create.add_argument("--permissions", action="append", default=[], help="Permissions (repeatable)")
    rollout_create.add_argument("--reason", default=None, help="Reason for creating rollout")

    rollout_list = rollout_sub.add_parser("list", help="List rollout plans")
    rollout_list.add_argument("--config", required=True, help="Config file path")
    rollout_list.add_argument("--status", default=None, help="Filter by status")
    rollout_list.add_argument("--bundle-id", default=None, help="Filter by bundle ID")
    rollout_list.add_argument("--json", action="store_true", help="Output as JSON")

    rollout_show = rollout_sub.add_parser("show", help="Show rollout plan details")
    rollout_show.add_argument("--config", required=True, help="Config file path")
    rollout_show.add_argument("--rollout-id", required=True, help="Rollout plan ID")
    rollout_show.add_argument("--json", action="store_true", help="Output as JSON")

    rollout_start = rollout_sub.add_parser("start", help="Start a rollout plan")
    rollout_start.add_argument("--config", required=True, help="Config file path")
    rollout_start.add_argument("--rollout-id", required=True, help="Rollout plan ID")
    rollout_start.add_argument("--actor-id", required=True, help="Actor ID")
    rollout_start.add_argument("--permissions", action="append", default=[], help="Permissions (repeatable)")

    rollout_next = rollout_sub.add_parser("run-next", help="Run next step in rollout plan")
    rollout_next.add_argument("--config", required=True, help="Config file path")
    rollout_next.add_argument("--rollout-id", required=True, help="Rollout plan ID")
    rollout_next.add_argument("--actor-id", required=True, help="Actor ID")
    rollout_next.add_argument("--permissions", action="append", default=[], help="Permissions (repeatable)")

    rollout_all = rollout_sub.add_parser("run-all", help="Run all available steps in rollout plan")
    rollout_all.add_argument("--config", required=True, help="Config file path")
    rollout_all.add_argument("--rollout-id", required=True, help="Rollout plan ID")
    rollout_all.add_argument("--actor-id", required=True, help="Actor ID")
    rollout_all.add_argument("--permissions", action="append", default=[], help="Permissions (repeatable)")

    rollout_cancel = rollout_sub.add_parser("cancel", help="Cancel a rollout plan")
    rollout_cancel.add_argument("--config", required=True, help="Config file path")
    rollout_cancel.add_argument("--rollout-id", required=True, help="Rollout plan ID")
    rollout_cancel.add_argument("--actor-id", required=True, help="Actor ID")
    rollout_cancel.add_argument("--permissions", action="append", default=[], help="Permissions (repeatable)")
    rollout_cancel.add_argument("--reason", default=None, help="Reason for cancellation")

    # Phase 36: rollout approval commands
    rollout_approval = rollout_sub.add_parser("approval", help="Rollout step approval management (Phase 36)")
    rollout_approval_sub = rollout_approval.add_subparsers(dest="approval_command")

    approval_list = rollout_approval_sub.add_parser("list", help="List rollout step approvals")
    approval_list.add_argument("--config", required=True, help="Config file path")
    approval_list.add_argument("--rollout-id", default=None, help="Filter by rollout ID")
    approval_list.add_argument("--status", default=None, help="Filter by approval status")
    approval_list.add_argument("--json", action="store_true", help="Output as JSON")

    approval_request = rollout_approval_sub.add_parser("request", help="Request approval for a rollout step")
    approval_request.add_argument("--config", required=True, help="Config file path")
    approval_request.add_argument("--rollout-id", required=True, help="Rollout plan ID")
    approval_request.add_argument("--step-id", required=True, help="Step ID to request approval for")
    approval_request.add_argument("--actor-id", required=True, help="Actor ID")
    approval_request.add_argument("--permissions", action="append", default=[], help="Permissions (repeatable)")
    approval_request.add_argument("--reason", default=None, help="Reason for the approval request")

    approval_approve = rollout_approval_sub.add_parser("approve", help="Approve a rollout step approval")
    approval_approve.add_argument("--config", required=True, help="Config file path")
    approval_approve.add_argument("--approval-id", required=True, help="Approval ID to approve")
    approval_approve.add_argument("--actor-id", required=True, help="Actor ID")
    approval_approve.add_argument("--permissions", action="append", default=[], help="Permissions (repeatable)")
    approval_approve.add_argument("--reason", default=None, help="Reason for approval")
    approval_approve.add_argument("--roles", action="append", default=[], help="Approver roles (Phase 37)")

    approval_reject = rollout_approval_sub.add_parser("reject", help="Reject a rollout step approval")
    approval_reject.add_argument("--config", required=True, help="Config file path")
    approval_reject.add_argument("--approval-id", required=True, help="Approval ID to reject")
    approval_reject.add_argument("--actor-id", required=True, help="Actor ID")
    approval_reject.add_argument("--permissions", action="append", default=[], help="Permissions (repeatable)")
    approval_reject.add_argument("--reason", default=None, help="Reason for rejection")
    approval_reject.add_argument("--roles", action="append", default=[], help="Approver roles (Phase 37)")

    # Phase 37: expire subcommand
    approval_expire = rollout_approval_sub.add_parser("expire", help="Expire pending approvals past their expiration time")
    approval_expire.add_argument("--config", required=True, help="Config file path")
    approval_expire.add_argument("--actor-id", required=True, help="Who is expiring approvals")
    approval_expire.add_argument("--permissions", action="append", default=[], help="Permissions (repeatable)")

    # Phase 43: rollout gate subcommands
    rollout_gate_parser = rollout_sub.add_parser("gate", help="Rollout step gate lifecycle commands (Phase 43)")
    rollout_gate_sub = rollout_gate_parser.add_subparsers(dest="rollout_gate_command")

    rollout_gate_run_parser = rollout_gate_sub.add_parser("run", help="Run simulation gate for a rollout step")
    rollout_gate_run_parser.add_argument("--config", required=True, help="Config file path")
    rollout_gate_run_parser.add_argument("--rollout-id", required=True, help="Rollout plan ID")
    rollout_gate_run_parser.add_argument("--step-id", required=True, help="Step ID to run gate for")
    rollout_gate_run_parser.add_argument("--actor-id", required=True, help="Actor ID")
    rollout_gate_run_parser.add_argument("--permissions", action="append", default=[], help="Permissions (repeatable)")

    rollout_gate_status_parser = rollout_gate_sub.add_parser("status", help="Check gate status for a rollout step")
    rollout_gate_status_parser.add_argument("--config", required=True, help="Config file path")
    rollout_gate_status_parser.add_argument("--rollout-id", required=True, help="Rollout plan ID")
    rollout_gate_status_parser.add_argument("--step-id", required=True, help="Step ID to check")
    rollout_gate_status_parser.add_argument("--json", action="store_true", help="Output as JSON")

    rollout_gate_attach_parser = rollout_gate_sub.add_parser("attach", help="Attach an existing gate result to a rollout step")
    rollout_gate_attach_parser.add_argument("--config", required=True, help="Config file path")
    rollout_gate_attach_parser.add_argument("--rollout-id", required=True, help="Rollout plan ID")
    rollout_gate_attach_parser.add_argument("--step-id", required=True, help="Step ID to attach gate to")
    rollout_gate_attach_parser.add_argument("--gate-result-id", required=True, help="Gate result ID to attach")
    rollout_gate_attach_parser.add_argument("--simulation-id", default=None, help="Simulation ID")
    rollout_gate_attach_parser.add_argument("--actor-id", required=True, help="Actor ID")
    rollout_gate_attach_parser.add_argument("--permissions", action="append", default=[], help="Permissions (repeatable)")

    # Phase 45: rollout history/timeline/analytics subcommands
    rollout_history_parser = rollout_sub.add_parser("history", help="Show rollout history events (Phase 45)")
    rollout_history_parser.add_argument("--config", required=True, help="Config file path")
    rollout_history_parser.add_argument("--rollout-id", required=True, help="Rollout plan ID")
    rollout_history_parser.add_argument("--limit", type=int, default=50, help="Max events to show")
    rollout_history_parser.add_argument("--event-type", default=None, help="Filter by event type")

    rollout_timeline_parser = rollout_sub.add_parser("timeline", help="Show rollout timeline (Phase 45)")
    rollout_timeline_parser.add_argument("--config", required=True, help="Config file path")
    rollout_timeline_parser.add_argument("--rollout-id", required=True, help="Rollout plan ID")
    rollout_timeline_parser.add_argument("--json", action="store_true", help="Output as JSON")

    rollout_analytics_parser = rollout_sub.add_parser("analytics", help="Show rollout analytics report (Phase 45)")
    rollout_analytics_sub = rollout_analytics_parser.add_subparsers(dest="rollout_analytics_command")

    rollout_analytics_show_parser = rollout_analytics_sub.add_parser("show", help="Show rollout analytics report")
    rollout_analytics_show_parser.add_argument("--config", required=True, help="Config file path")
    rollout_analytics_show_parser.add_argument("--since", default=None, help="Window start (ISO datetime)")
    rollout_analytics_show_parser.add_argument("--until", default=None, help="Window end (ISO datetime)")

    rollout_analytics_export_parser = rollout_analytics_sub.add_parser("export", help="Export rollout analytics report")
    rollout_analytics_export_parser.add_argument("--config", required=True, help="Config file path")
    rollout_analytics_export_parser.add_argument("--format", required=True, choices=["json", "csv"], help="Export format")
    rollout_analytics_export_parser.add_argument("--output", required=True, help="Output file path")
    rollout_analytics_export_parser.add_argument("--since", default=None, help="Window start (ISO datetime)")
    rollout_analytics_export_parser.add_argument("--until", default=None, help="Window end (ISO datetime)")

    # Phase 46: policy federation subcommands
    federation_parser = policy_sub.add_parser("federation", help="Policy rollout federation commands")
    federation_sub = federation_parser.add_subparsers(dest="federation_command")

    target_parser = federation_sub.add_parser("target", help="Federation target commands")
    target_sub = target_parser.add_subparsers(dest="target_command")

    # target create
    p = target_sub.add_parser("create", help="Create a federation target")
    p.add_argument("--config", default="agentapp.yaml")
    p.add_argument("--name", required=True)
    p.add_argument("--environment", required=True)
    p.add_argument("--ring", default=None)
    p.add_argument("--region", default=None)
    p.add_argument("--tenant-id", default=None)
    p.add_argument("--label", action="append", default=None)
    p.add_argument("--actor-id", default=None)
    p.add_argument("--permissions", default=None)
    p.set_defaults(func=_cmd_policy_federation_target_create)

    # target list
    p = target_sub.add_parser("list", help="List federation targets")
    p.add_argument("--config", default="agentapp.yaml")
    p.add_argument("--tenant-id", default=None)
    p.add_argument("--environment", default=None)
    p.add_argument("--ring", default=None)
    p.add_argument("--status", default=None)
    p.set_defaults(func=_cmd_policy_federation_target_list)

    # target enable
    p = target_sub.add_parser("enable", help="Enable a federation target")
    p.add_argument("--config", default="agentapp.yaml")
    p.add_argument("--target-id", required=True)
    p.add_argument("--actor-id", default=None)
    p.add_argument("--permissions", default=None)
    p.set_defaults(func=_cmd_policy_federation_target_enable)

    # target disable
    p = target_sub.add_parser("disable", help="Disable a federation target")
    p.add_argument("--config", default="agentapp.yaml")
    p.add_argument("--target-id", required=True)
    p.add_argument("--actor-id", default=None)
    p.add_argument("--permissions", default=None)
    p.set_defaults(func=_cmd_policy_federation_target_disable)

    plan_parser = federation_sub.add_parser("plan", help="Federated rollout plan commands")
    plan_sub = plan_parser.add_subparsers(dest="plan_command")

    # plan create
    p = plan_sub.add_parser("create", help="Create a federated plan")
    p.add_argument("--config", default="agentapp.yaml")
    p.add_argument("--name", required=True)
    p.add_argument("--bundle-id", required=True)
    p.add_argument("--targets-file", required=True)
    p.add_argument("--steps-file", required=True)
    p.add_argument("--strategy", default="sequential", choices=["sequential", "parallel", "wave"])
    p.add_argument("--actor-id", required=True)
    p.add_argument("--permissions", default=None)
    p.add_argument("--reason", default=None)
    p.set_defaults(func=_cmd_policy_federation_plan_create)

    # plan list
    p = plan_sub.add_parser("list", help="List federated plans")
    p.add_argument("--config", default="agentapp.yaml")
    p.set_defaults(func=_cmd_policy_federation_plan_list)

    # plan show
    p = plan_sub.add_parser("show", help="Show a federated plan")
    p.add_argument("--config", default="agentapp.yaml")
    p.add_argument("--federation-id", required=True)
    p.set_defaults(func=_cmd_policy_federation_plan_show)

    # plan start
    p = plan_sub.add_parser("start", help="Start a federated plan")
    p.add_argument("--config", default="agentapp.yaml")
    p.add_argument("--federation-id", required=True)
    p.add_argument("--actor-id", required=True)
    p.add_argument("--permissions", default=None)
    p.set_defaults(func=_cmd_policy_federation_plan_start)

    # plan run-next
    p = plan_sub.add_parser("run-next", help="Run next target in a federated plan")
    p.add_argument("--config", default="agentapp.yaml")
    p.add_argument("--federation-id", required=True)
    p.add_argument("--actor-id", required=True)
    p.add_argument("--permissions", default=None)
    p.set_defaults(func=_cmd_policy_federation_plan_run_next)

    # plan run-all
    p = plan_sub.add_parser("run-all", help="Run all available targets in a federated plan")
    p.add_argument("--config", default="agentapp.yaml")
    p.add_argument("--federation-id", required=True)
    p.add_argument("--actor-id", required=True)
    p.add_argument("--permissions", default=None)
    p.set_defaults(func=_cmd_policy_federation_plan_run_all)

    # plan cancel
    p = plan_sub.add_parser("cancel", help="Cancel a federated plan")
    p.add_argument("--config", default="agentapp.yaml")
    p.add_argument("--federation-id", required=True)
    p.add_argument("--actor-id", required=True)
    p.add_argument("--permissions", default=None)
    p.add_argument("--reason", default=None)
    p.set_defaults(func=_cmd_policy_federation_plan_cancel)

    # plan conflicts
    p = plan_sub.add_parser("conflicts", help="Show conflicts for a federated plan")
    p.add_argument("--config", default="agentapp.yaml")
    p.add_argument("--federation-id", required=True)
    p.set_defaults(func=_cmd_policy_federation_plan_conflicts)

    # Phase 44: policy notification subcommands
    notification_parser = policy_sub.add_parser("notification", help="Policy notification commands (Phase 44)")
    notification_sub = notification_parser.add_subparsers(dest="notification_command")

    notification_list_parser = notification_sub.add_parser("list", help="List policy notifications")
    notification_list_parser.add_argument("--config", required=True, help="Config file path")
    notification_list_parser.add_argument("--status", default=None, help="Filter by status")
    notification_list_parser.add_argument("--event-type", default=None, help="Filter by event type")
    notification_list_parser.add_argument("--limit", type=int, default=20, help="Max results")
    notification_list_parser.add_argument("--json", action="store_true", help="Output as JSON")

    notification_send_pending_parser = notification_sub.add_parser("send-pending", help="Send pending notifications")
    notification_send_pending_parser.add_argument("--config", required=True, help="Config file path")
    notification_send_pending_parser.add_argument("--limit", type=int, default=None, help="Max notifications to send")
    notification_send_pending_parser.add_argument("--json", action="store_true", help="Output as JSON")

    notification_rule_parser = notification_sub.add_parser("rule", help="Notification rule commands")
    notification_rule_sub = notification_rule_parser.add_subparsers(dest="notification_rule_command")

    notification_rule_list_parser = notification_rule_sub.add_parser("list", help="List notification rules")
    notification_rule_list_parser.add_argument("--config", required=True, help="Config file path")
    notification_rule_list_parser.add_argument("--json", action="store_true", help="Output as JSON")

    notification_rule_enable_parser = notification_rule_sub.add_parser("enable", help="Enable a notification rule")
    notification_rule_enable_parser.add_argument("--config", required=True, help="Config file path")
    notification_rule_enable_parser.add_argument("--rule-id", required=True, help="Rule ID to enable")

    notification_rule_disable_parser = notification_rule_sub.add_parser("disable", help="Disable a notification rule")
    notification_rule_disable_parser.add_argument("--config", required=True, help="Config file path")
    notification_rule_disable_parser.add_argument("--rule-id", required=True, help="Rule ID to disable")

    # Phase 44: policy expiration subcommands
    expiration_parser = policy_sub.add_parser("expiration", help="Policy expiration commands (Phase 44)")
    expiration_sub = expiration_parser.add_subparsers(dest="expiration_command")

    expiration_sweep_parser = expiration_sub.add_parser("sweep", help="Run expiration sweep")
    expiration_sweep_parser.add_argument("--config", required=True, help="Config file path")
    expiration_sweep_parser.add_argument("--json", action="store_true", help="Output as JSON")

    expiration_run_once_parser = expiration_sub.add_parser("run-once", help="Run expiration sweep once")
    expiration_run_once_parser.add_argument("--config", required=True, help="Config file path")
    expiration_run_once_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Phase 38: runtime policy subcommands
    runtime_parser = policy_sub.add_parser("runtime", help="Runtime policy management (Phase 38)")
    runtime_sub = runtime_parser.add_subparsers(dest="runtime_command")

    runtime_list_parser = runtime_sub.add_parser("list", help="List runtime policy rules")
    runtime_list_parser.add_argument("--config", default="agentapp.yaml")
    runtime_list_parser.add_argument("--action-type", default=None, help="Filter by action type")
    runtime_list_parser.add_argument("--json", action="store_true")

    runtime_create_parser = runtime_sub.add_parser("create", help="Create a runtime policy rule")
    runtime_create_parser.add_argument("--config", default="agentapp.yaml")
    runtime_create_parser.add_argument("--name", required=True, help="Rule name")
    runtime_create_parser.add_argument("--action-type", required=True, help="Action type (e.g. tool.execute)")
    runtime_create_parser.add_argument("--effect", required=True, help="Effect (allow, deny, require_approval)")
    runtime_create_parser.add_argument("--tool-name", default=None, help="Tool name to match")
    runtime_create_parser.add_argument("--risk-level", default=None, help="Risk level to match")
    runtime_create_parser.add_argument("--required-permissions", action="append", default=[])
    runtime_create_parser.add_argument("--required-roles", action="append", default=[])
    runtime_create_parser.add_argument("--approval-policy-type", default=None, help="Approval policy type (single, quorum)")
    runtime_create_parser.add_argument("--required-approvals", type=int, default=None, help="Required approvals count")
    runtime_create_parser.add_argument("--reason", default=None, help="Reason for the rule")
    runtime_create_parser.add_argument("--actor-id", required=True, help="Who is creating")
    runtime_create_parser.add_argument("--permissions", action="append", default=[])
    runtime_create_parser.add_argument("--roles", action="append", default=[])

    runtime_enable_parser = runtime_sub.add_parser("enable", help="Enable a runtime policy rule")
    runtime_enable_parser.add_argument("--config", default="agentapp.yaml")
    runtime_enable_parser.add_argument("--rule-id", required=True, help="Rule ID to enable")

    runtime_disable_parser = runtime_sub.add_parser("disable", help="Disable a runtime policy rule")
    runtime_disable_parser.add_argument("--config", default="agentapp.yaml")
    runtime_disable_parser.add_argument("--rule-id", required=True, help="Rule ID to disable")

    runtime_eval_parser = runtime_sub.add_parser("evaluate", help="Evaluate a runtime policy decision")
    runtime_eval_parser.add_argument("--config", default="agentapp.yaml")
    runtime_eval_parser.add_argument("--action-type", required=True, help="Action type")
    runtime_eval_parser.add_argument("--tool-name", default=None, help="Tool name")
    runtime_eval_parser.add_argument("--risk-level", default=None, help="Risk level")
    runtime_eval_parser.add_argument("--actor-id", required=True, help="Actor ID")
    runtime_eval_parser.add_argument("--permissions", action="append", default=[])
    runtime_eval_parser.add_argument("--roles", action="append", default=[])

    # Phase 39: observability subcommands
    observability_parser = policy_sub.add_parser("observability", help="Policy observability and analytics")
    obs_subparsers = observability_parser.add_subparsers(dest="observability_command")

    obs_report_parser = obs_subparsers.add_parser("report", help="Generate observability report")
    obs_report_parser.add_argument("--config", default="agentapp.yaml")
    obs_report_parser.add_argument("--since", default=None, help="Window start (ISO 8601)")
    obs_report_parser.add_argument("--until", default=None, help="Window end (ISO 8601)")
    obs_report_parser.add_argument("--json", action="store_true", help="JSON output")

    obs_export_parser = obs_subparsers.add_parser("export", help="Export observability report")
    obs_export_parser.add_argument("--config", default="agentapp.yaml")
    obs_export_parser.add_argument("--format", required=True, choices=["json", "csv"], help="Export format")
    obs_export_parser.add_argument("--output", required=True, help="Output file path")
    obs_export_parser.add_argument("--since", default=None, help="Window start (ISO 8601)")
    obs_export_parser.add_argument("--until", default=None, help="Window end (ISO 8601)")

    # Phase 40: simulation subcommands
    simulation_parser = policy_sub.add_parser("simulation", help="Policy simulation commands (Phase 40)")
    sim_subparsers = simulation_parser.add_subparsers(dest="simulation_command")

    sim_validate_parser = sim_subparsers.add_parser("validate", help="Validate candidate rules from a YAML file")
    sim_validate_parser.add_argument("--config", default="agentapp.yaml", help="Config file path")
    sim_validate_parser.add_argument("--rules-file", required=True, help="YAML file with candidate rules")

    sim_replay_parser = sim_subparsers.add_parser("replay", help="Replay candidate rules against audit history")
    sim_replay_parser.add_argument("--config", default="agentapp.yaml", help="Config file path")
    sim_replay_parser.add_argument("--rules-file", required=True, help="YAML file with candidate rules")
    sim_replay_parser.add_argument("--since", default=None, help="Window start (ISO 8601)")
    sim_replay_parser.add_argument("--until", default=None, help="Window end (ISO 8601)")
    sim_replay_parser.add_argument("--limit", type=int, default=None, help="Max audit cases to replay")
    sim_replay_parser.add_argument("--json", action="store_true", help="JSON output")

    sim_export_parser = sim_subparsers.add_parser("export", help="Export simulation report to file")
    sim_export_parser.add_argument("--config", default="agentapp.yaml", help="Config file path")
    sim_export_parser.add_argument("--rules-file", required=True, help="YAML file with candidate rules")
    sim_export_parser.add_argument("--format", required=True, choices=["json", "csv"], help="Export format")
    sim_export_parser.add_argument("--output", required=True, help="Output file path")
    sim_export_parser.add_argument("--since", default=None, help="Window start (ISO 8601)")
    sim_export_parser.add_argument("--until", default=None, help="Window end (ISO 8601)")
    sim_export_parser.add_argument("--limit", type=int, default=None, help="Max audit cases to replay")

    # Phase 41: simulation gate subcommand
    sim_gate_parser = sim_subparsers.add_parser("gate", help="Run simulation gate (validate + replay + gate)")
    sim_gate_parser.add_argument("--config", default="agentapp.yaml", help="Config file path")
    sim_gate_parser.add_argument("--rules-file", required=True, help="YAML file with candidate runtime policy rules")
    sim_gate_parser.add_argument("--gate-rules-file", required=False, help="YAML file with simulation gate rules")
    sim_gate_parser.add_argument("--since", help="ISO 8601 datetime for audit window start")
    sim_gate_parser.add_argument("--until", help="ISO 8601 datetime for audit window end")
    sim_gate_parser.add_argument("--limit", type=int, default=None, help="Max audit cases to replay")
    sim_gate_parser.add_argument("--json", action="store_true", help="Output as JSON")
    sim_gate_parser.add_argument("--output", help="Write output to file")

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

    if args.command == "policy" and args.policy_command == "validate":
        return asyncio.run(_cmd_policy_validate(args))

    if args.command == "policy" and args.policy_command == "simulate":
        return asyncio.run(_cmd_policy_simulate(args))

    if args.command == "policy" and args.policy_command == "explain":
        return asyncio.run(_cmd_policy_explain(args))

    if args.command == "policy" and args.policy_command == "decisions":
        return asyncio.run(_cmd_policy_decisions(args))

    if args.command == "policy" and args.policy_command == "report":
        return asyncio.run(_cmd_policy_report(args))

    if args.command == "policy" and args.policy_command == "export":
        return asyncio.run(_cmd_policy_export(args))

    if args.command == "policy" and args.policy_command == "replay":
        return asyncio.run(_cmd_policy_replay(args))

    if args.command == "policy" and args.policy_command == "run-job":
        return asyncio.run(_cmd_policy_replay_run_job(args))

    if args.command == "policy" and args.policy_command == "jobs":
        return asyncio.run(_cmd_policy_replay_jobs(args))

    # Phase 29: policy bundle subcommands
    if args.command == "policy" and args.policy_command == "bundle":
        if args.bundle_command == "create":
            return asyncio.run(_cmd_policy_bundle_create(args))
        if args.bundle_command == "list":
            return asyncio.run(_cmd_policy_bundle_list(args))
        if args.bundle_command == "active":
            return asyncio.run(_cmd_policy_bundle_active(args))
        if args.bundle_command == "promote":
            return asyncio.run(_cmd_policy_bundle_promote(args))
        if args.bundle_command == "rollback":
            return asyncio.run(_cmd_policy_bundle_rollback(args))

    # Phase 29: policy gate subcommands
    if args.command == "policy" and args.policy_command == "gate":
        if args.gate_command == "run":
            return asyncio.run(_cmd_policy_gate_run(args))
        if args.gate_command == "list":
            return asyncio.run(_cmd_policy_gate_list(args))

    # Phase 30: policy promotion subcommands
    if args.command == "policy" and args.policy_command == "promotion":
        if args.promotion_command == "request":
            return asyncio.run(_cmd_policy_promotion_request(args))
        if args.promotion_command == "list":
            return asyncio.run(_cmd_policy_promotion_list(args))
        if args.promotion_command == "approve":
            return asyncio.run(_cmd_policy_promotion_approve(args))
        if args.promotion_command == "reject":
            return asyncio.run(_cmd_policy_promotion_reject(args))
        if args.promotion_command == "execute":
            return asyncio.run(_cmd_policy_promotion_execute(args))
        # Phase 42: policy promotion gate subcommands
        if args.promotion_command == "gate":
            if args.promotion_gate_command == "require":
                return asyncio.run(_cmd_policy_promotion_gate_require(args))
            if args.promotion_gate_command == "run":
                return asyncio.run(_cmd_policy_promotion_gate_run(args))
            if args.promotion_gate_command == "attach":
                return asyncio.run(_cmd_policy_promotion_gate_attach(args))
            if args.promotion_gate_command == "status":
                return asyncio.run(_cmd_policy_promotion_gate_status(args))

    # Phase 31: policy activation subcommands
    if args.command == "policy" and args.policy_command == "activation":
        if args.activation_command == "list":
            return asyncio.run(_cmd_policy_activation_list(args))
        if args.activation_command == "active":
            return asyncio.run(_cmd_policy_activation_active(args))
        if args.activation_command == "rollback":
            return asyncio.run(_cmd_policy_activation_rollback(args))

    # Phase 32: policy environment subcommands
    if args.command == "policy" and args.policy_command == "environment":
        if args.environment_command == "list":
            return asyncio.run(_cmd_policy_environment_list(args))
        if args.environment_command == "disable":
            return asyncio.run(_cmd_policy_environment_disable(args))
        if args.environment_command == "enable":
            return asyncio.run(_cmd_policy_environment_enable(args))

    # Phase 33: policy ring subcommands
    if args.command == "policy" and args.policy_command == "ring":
        if args.ring_command == "list":
            return asyncio.run(_cmd_policy_ring_list(args))
        if args.ring_command == "create":
            return asyncio.run(_cmd_policy_ring_create(args))
        if args.ring_command == "assign":
            return asyncio.run(_cmd_policy_ring_assign(args))
        if args.ring_command == "promote":
            return asyncio.run(_cmd_policy_ring_promote(args))
        if args.ring_command == "disable":
            return asyncio.run(_cmd_policy_ring_disable(args))
        if args.ring_command == "enable":
            return asyncio.run(_cmd_policy_ring_enable(args))

    # Phase 33: policy canary subcommands
    if args.command == "policy" and args.policy_command == "canary":
        if args.canary_command == "eval":
            return asyncio.run(_cmd_policy_canary_eval(args))

    # Phase 34: policy reload subcommands
    if args.command == "policy" and args.policy_command == "reload":
        if args.reload_command == "request":
            return asyncio.run(_cmd_policy_reload_request(args))
        if args.reload_command == "status":
            return asyncio.run(_cmd_policy_reload_status(args))

    # Phase 34: policy events subcommands
    if args.command == "policy" and args.policy_command == "events":
        if args.events_command == "list":
            return asyncio.run(_cmd_policy_events_list(args))

    # Phase 34: policy routing subcommands
    if args.command == "policy" and args.policy_command == "routing":
        if args.routing_command == "simulate":
            return asyncio.run(_cmd_policy_routing_simulate(args))

    # Phase 35: policy rollout subcommands
    if args.command == "policy" and args.policy_command == "rollout":
        if args.rollout_command == "create":
            return asyncio.run(_cmd_policy_rollout_create(args))
        if args.rollout_command == "list":
            return asyncio.run(_cmd_policy_rollout_list(args))
        if args.rollout_command == "show":
            return asyncio.run(_cmd_policy_rollout_show(args))
        if args.rollout_command == "start":
            return asyncio.run(_cmd_policy_rollout_start(args))
        if args.rollout_command == "run-next":
            return asyncio.run(_cmd_policy_rollout_run_next(args))
        if args.rollout_command == "run-all":
            return asyncio.run(_cmd_policy_rollout_run_all(args))
        if args.rollout_command == "cancel":
            return asyncio.run(_cmd_policy_rollout_cancel(args))
        # Phase 36: rollout approval subcommands
        if args.rollout_command == "approval":
            if args.approval_command == "list":
                return asyncio.run(_cmd_policy_rollout_approval_list(args))
            if args.approval_command == "request":
                return asyncio.run(_cmd_policy_rollout_approval_request(args))
            if args.approval_command == "approve":
                return asyncio.run(_cmd_policy_rollout_approval_approve(args))
            if args.approval_command == "reject":
                return asyncio.run(_cmd_policy_rollout_approval_reject(args))
            if args.approval_command == "expire":
                return asyncio.run(_cmd_policy_rollout_approval_expire(args))
        # Phase 43: rollout gate subcommands
        if args.rollout_command == "gate":
            if args.rollout_gate_command == "run":
                return asyncio.run(_cmd_policy_rollout_gate_run(args))
            if args.rollout_gate_command == "status":
                return asyncio.run(_cmd_policy_rollout_gate_status(args))
            if args.rollout_gate_command == "attach":
                return asyncio.run(_cmd_policy_rollout_gate_attach(args))
        # Phase 45: rollout history/timeline/analytics subcommands
        if args.rollout_command == "history":
            return asyncio.run(_cmd_policy_rollout_history(args))
        if args.rollout_command == "timeline":
            return asyncio.run(_cmd_policy_rollout_timeline(args))
        if args.rollout_command == "analytics":
            if args.rollout_analytics_command == "export":
                return asyncio.run(_cmd_policy_rollout_analytics_export(args))
            return asyncio.run(_cmd_policy_rollout_analytics(args))

    # Phase 46: policy federation subcommands
    if args.command == "policy" and args.policy_command == "federation":
        if args.federation_command == "target":
            if args.target_command == "create":
                return asyncio.run(_cmd_policy_federation_target_create(args))
            if args.target_command == "list":
                return asyncio.run(_cmd_policy_federation_target_list(args))
            if args.target_command == "enable":
                return asyncio.run(_cmd_policy_federation_target_enable(args))
            if args.target_command == "disable":
                return asyncio.run(_cmd_policy_federation_target_disable(args))
            target_parser.print_help()
            return 1
        if args.federation_command == "plan":
            if args.plan_command == "create":
                return asyncio.run(_cmd_policy_federation_plan_create(args))
            if args.plan_command == "list":
                return asyncio.run(_cmd_policy_federation_plan_list(args))
            if args.plan_command == "show":
                return asyncio.run(_cmd_policy_federation_plan_show(args))
            if args.plan_command == "start":
                return asyncio.run(_cmd_policy_federation_plan_start(args))
            if args.plan_command == "run-next":
                return asyncio.run(_cmd_policy_federation_plan_run_next(args))
            if args.plan_command == "run-all":
                return asyncio.run(_cmd_policy_federation_plan_run_all(args))
            if args.plan_command == "cancel":
                return asyncio.run(_cmd_policy_federation_plan_cancel(args))
            if args.plan_command == "conflicts":
                return asyncio.run(_cmd_policy_federation_plan_conflicts(args))
            plan_parser.print_help()
            return 1
        federation_parser.print_help()
        return 1

    # Phase 44: policy notification subcommands
    if args.command == "policy" and args.policy_command == "notification":
        if args.notification_command == "list":
            return asyncio.run(_cmd_policy_notification_list(args))
        if args.notification_command == "send-pending":
            return asyncio.run(_cmd_policy_notification_send_pending(args))
        if args.notification_command == "rule":
            if args.notification_rule_command == "list":
                return asyncio.run(_cmd_policy_notification_rule_list(args))
            if args.notification_rule_command == "enable":
                return asyncio.run(_cmd_policy_notification_rule_enable(args))
            if args.notification_rule_command == "disable":
                return asyncio.run(_cmd_policy_notification_rule_disable(args))
        notification_parser.print_help()
        return 1

    # Phase 44: policy expiration subcommands
    if args.command == "policy" and args.policy_command == "expiration":
        if args.expiration_command == "sweep":
            return asyncio.run(_cmd_policy_expiration_sweep(args))
        if args.expiration_command == "run-once":
            return asyncio.run(_cmd_policy_expiration_run_once(args))
        expiration_parser.print_help()
        return 1

    # Phase 38: runtime policy subcommands
    if args.command == "policy" and args.policy_command == "runtime":
        if args.runtime_command == "list":
            return asyncio.run(_cmd_policy_runtime_list(args))
        if args.runtime_command == "create":
            return asyncio.run(_cmd_policy_runtime_create(args))
        if args.runtime_command == "enable":
            return asyncio.run(_cmd_policy_runtime_enable(args))
        if args.runtime_command == "disable":
            return asyncio.run(_cmd_policy_runtime_disable(args))
        if args.runtime_command == "evaluate":
            return asyncio.run(_cmd_policy_runtime_evaluate(args))
        runtime_parser.print_help()
        return 1

    # Phase 39: observability subcommands
    if args.command == "policy" and args.policy_command == "observability":
        if args.observability_command == "report":
            return asyncio.run(_cmd_policy_observability_report(args))
        elif args.observability_command == "export":
            return asyncio.run(_cmd_policy_observability_export(args))
        else:
            observability_parser.print_help()
            return 1

    # Phase 40: simulation subcommands
    if args.command == "policy" and args.policy_command == "simulation":
        if args.simulation_command == "validate":
            return asyncio.run(_cmd_policy_simulation_validate(args))
        elif args.simulation_command == "replay":
            return asyncio.run(_cmd_policy_simulation_replay(args))
        elif args.simulation_command == "export":
            return asyncio.run(_cmd_policy_simulation_export(args))
        elif args.simulation_command == "gate":
            return asyncio.run(_cmd_policy_simulation_gate(args))
        else:
            simulation_parser.print_help()
            return 1

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


# -- Phase 24: Policy commands --


async def _cmd_policy_validate(args: argparse.Namespace) -> int:
    """Validate policy config and report issues."""
    from agent_app.config.loader import load_config
    from agent_app.governance.policy_validation import validate_policy_config

    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    gov = getattr(config, "governance", None)
    policy_cfg = getattr(gov, "policies", None) if gov else None

    if policy_cfg is None or not getattr(policy_cfg, "enabled", False):
        print("Policy engine is not enabled in this config.")
        return 0

    result = validate_policy_config(policy_cfg)

    if not result.issues:
        print("Policy config is valid. No issues found.")
        return 0

    for issue in result.issues:
        level_tag = "ERROR" if issue.level == "error" else "WARNING"
        location = f" ({issue.path})" if issue.path else ""
        rule = f" in rule '{issue.rule_name}'" if issue.rule_name else ""
        print(f"  [{level_tag}]{location}{rule}: {issue.message}")

    error_count = sum(1 for i in result.issues if i.level == "error")
    warning_count = sum(1 for i in result.issues if i.level == "warning")

    print()
    print(f"  {error_count} error(s), {warning_count} warning(s)")

    return 1 if error_count > 0 else 0


async def _cmd_policy_simulate(args: argparse.Namespace) -> int:
    """Simulate a policy decision."""
    from agent_app.config.loader import load_config
    from agent_app.governance.policy import ConfigurablePolicyEngine, DefaultPolicyEngine
    from agent_app.governance.policy_simulator import PolicySimulationInput, PolicySimulator

    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    gov = getattr(config, "governance", None)
    policy_cfg = getattr(gov, "policies", None) if gov else None

    if policy_cfg is None or not getattr(policy_cfg, "enabled", False):
        print("Policy engine is not enabled. Using default policy.", file=sys.stderr)
        engine: Any = DefaultPolicyEngine()
    else:
        rules = [r.model_dump() if hasattr(r, "model_dump") else r for r in policy_cfg.rules]
        engine = ConfigurablePolicyEngine(
            rules=rules,
            default_action=getattr(policy_cfg, "default_action", "allow"),
        )

    sim = PolicySimulator(policy_engine=engine)
    inp = PolicySimulationInput(
        tool_name=args.tool,
        risk_level=args.risk,
        workflow_type=args.workflow_type,
        agent_name=args.agent_name,
        target_agent=args.target_agent,
        user_id=args.user_id,
        tenant_id=args.tenant_id,
        roles=list(args.role),
        permissions=list(args.permission),
    )
    result = await sim.simulate(inp)

    if args.json:
        data = {
            "tool": args.tool,
            "action": result.decision.action.value,
            "allowed": result.decision.allowed,
            "requires_approval": result.decision.requires_approval,
            "reason": result.decision.reason,
            "rule_name": result.decision.metadata.get("rule_name"),
            "ttl_seconds": result.decision.ttl_seconds,
        }
        print(json.dumps(data, indent=2))
    else:
        print(f"Policy simulation for tool '{args.tool}':")
        print(f"  Action:     {result.decision.action.value}")
        print(f"  Allowed:    {result.decision.allowed}")
        print(f"  Rule:       {result.decision.metadata.get('rule_name', 'default')}")
        if result.decision.reason:
            print(f"  Reason:     {result.decision.reason}")
        if result.decision.ttl_seconds:
            print(f"  TTL:        {result.decision.ttl_seconds}s")
        if result.decision.requires_approval:
            print("  → Requires human approval")

    return 0


async def _cmd_policy_explain(args: argparse.Namespace) -> int:
    """Explain a policy decision with matched rule and conditions."""
    from agent_app.config.loader import load_config
    from agent_app.governance.policy import ConfigurablePolicyEngine, DefaultPolicyEngine
    from agent_app.governance.policy_simulator import PolicySimulationInput, PolicySimulator

    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    gov = getattr(config, "governance", None)
    policy_cfg = getattr(gov, "policies", None) if gov else None

    if policy_cfg is None or not getattr(policy_cfg, "enabled", False):
        print("Policy engine is not enabled. Using default policy.", file=sys.stderr)
        engine: Any = DefaultPolicyEngine()
    else:
        rules = [r.model_dump() if hasattr(r, "model_dump") else r for r in policy_cfg.rules]
        engine = ConfigurablePolicyEngine(
            rules=rules,
            default_action=getattr(policy_cfg, "default_action", "allow"),
        )

    sim = PolicySimulator(policy_engine=engine)
    inp = PolicySimulationInput(
        tool_name=args.tool,
        risk_level=args.risk,
        workflow_type=args.workflow_type,
        agent_name=args.agent_name,
        target_agent=args.target_agent,
        user_id=args.user_id,
        tenant_id=args.tenant_id,
        roles=list(args.role),
        permissions=list(args.permission),
    )
    result = await sim.explain(inp)
    trace = result.trace

    if trace is None:
        print("No trace available.")
        return 1

    if args.json:
        data = {
            "decision_id": trace.decision_id,
            "action": trace.action.value,
            "rule_name": trace.rule_name,
            "reason": trace.reason,
            "matched_conditions": trace.matched_conditions,
            "context_summary": trace.context_summary,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"Policy explain for tool '{args.tool}':")
        print(f"  Decision ID:  {trace.decision_id}")
        print(f"  Action:       {trace.action.value}")
        print(f"  Rule:         {trace.rule_name or '(default)'}")
        if trace.reason:
            print(f"  Reason:       {trace.reason}")
        if trace.matched_conditions:
            print(f"  Matched:      {trace.matched_conditions}")
        if trace.context_summary:
            print(f"  Context:      {trace.context_summary}")

    return 0


async def _cmd_policy_decisions(args: argparse.Namespace) -> int:
    """Query policy decisions from the store."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    store = getattr(app, "policy_decision_store", None)
    if store is None:
        print("Policy decision store not configured.", file=sys.stderr)
        return 1

    traces = await store.query(
        run_id=args.run_id,
        tenant_id=args.tenant_id,
        agent_name=args.agent_name,
        tool_name=args.tool_name,
        rule_name=args.rule_name,
        action=args.action,
        limit=args.limit,
        offset=args.offset,
    )

    if not traces:
        print("No policy decisions found.")
        return 0

    if args.json:
        data = []
        for t in traces:
            data.append({
                "decision_id": t.decision_id,
                "run_id": t.run_id,
                "rule_name": t.rule_name,
                "action": t.action.value,
                "reason": t.reason,
                "tool_name": t.tool_name,
                "created_at": t.created_at.isoformat(),
            })
        print(json.dumps(data, indent=2, default=str))
    else:
        for t in traces:
            print(f"[{t.action.value}] {t.decision_id}")
            print(f"  Tool:     {t.tool_name or '(unknown)'}")
            print(f"  Rule:     {t.rule_name or '(default)'}")
            print(f"  Run:      {t.run_id or '(none)'}")
            if t.reason:
                print(f"  Reason:   {t.reason}")
            print(f"  Created:  {t.created_at.isoformat()}")
            print()

    return 0


async def _cmd_policy_report(args: argparse.Namespace) -> int:
    """Generate a policy decision report."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_decision_store import PolicyReportingService

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    store = getattr(app, "policy_decision_store", None)
    if store is None:
        print("Policy decision store not configured.", file=sys.stderr)
        return 1

    service = PolicyReportingService(store)
    report = await service.generate_report(
        run_id=args.run_id,
        tenant_id=args.tenant_id,
        tool_name=args.tool_name,
        rule_name=args.rule_name,
        action=args.action,
        limit=args.limit,
    )

    if args.json:
        print(json.dumps(report.model_dump(mode="json"), indent=2, default=str))
    else:
        print("Policy Decision Report")
        print("=" * 40)
        print(f"Total decisions: {report.total_decisions}")
        print()
        print("By action:")
        for action, count in sorted(report.action_breakdown.items()):
            print(f"  {action:20s} {count}")
        print()
        print("By rule:")
        for rule, count in sorted(report.rule_breakdown.items()):
            print(f"  {rule:30s} {count}")
        print()
        print("By tool:")
        for tool, count in sorted(report.tool_breakdown.items()):
            print(f"  {tool:30s} {count}")
        tr = report.time_range
        if tr.get("start") and tr.get("end"):
            print()
            print(f"Time range: {tr['start'].isoformat()} - {tr['end'].isoformat()}")

    return 0


async def _cmd_policy_export(args: argparse.Namespace) -> int:
    """Export policy decisions to a file."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_decision_store import PolicyReportingService

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    store = getattr(app, "policy_decision_store", None)
    if store is None:
        print("Policy decision store not configured.", file=sys.stderr)
        return 1

    service = PolicyReportingService(store)
    if args.format == "jsonl":
        count = await service.export_jsonl(
            file_path=args.output,
            run_id=args.run_id,
            tenant_id=args.tenant_id,
            limit=args.limit,
        )
    else:
        count = await service.export_csv(
            file_path=args.output,
            run_id=args.run_id,
            tenant_id=args.tenant_id,
            limit=args.limit,
        )

    print(f"Exported {count} policy decisions to {args.output}")
    return 0


async def _cmd_policy_replay(args: argparse.Namespace) -> int:
    """Replay policy decisions against the current policy engine.

    Runs synchronously by default, or submits as background job with --background.
    """
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_replay import PolicyReplayRunner
    from agent_app.runtime.policy_replay_store import create_replay_store
    from agent_app.runtime.policy_replay_jobs import (
        InMemoryPolicyReplayJobStore,
        create_replay_job_store,
    )
    from agent_app.runtime.policy_replay_background import PolicyReplayBackgroundRunner
    from agent_app.governance.policy_replay_context import PolicyReplayContextBuilder

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    store = getattr(app, "policy_decision_store", None)
    if store is None:
        print("Policy decision store not configured.", file=sys.stderr)
        return 1

    engine = getattr(app, "policy_engine", None)
    if engine is None:
        print("Policy engine is not configured. Using default allow policy.", file=sys.stderr)
        from agent_app.governance.policy import DefaultPolicyEngine
        engine = DefaultPolicyEngine()

    # Set up replay store
    replay_store = create_replay_store(
        store_type=args.store,
        db_path=args.db_path,
    )

    # Set up context builder
    context_builder = PolicyReplayContextBuilder()

    runner = PolicyReplayRunner(
        decision_store=store,
        policy_engine=engine,
        replay_store=replay_store,
        context_builder=context_builder,
    )

    # Background mode: submit job and exit
    if args.background:
        job_store = create_replay_job_store(
            store_type=args.store,
            db_path=args.db_path,
        )
        bg_runner = PolicyReplayBackgroundRunner(
            replay_runner=runner,
            job_store=job_store,
            replay_store=replay_store,
        )
        job = await bg_runner.submit(
            limit=args.limit,
            tenant_id=args.tenant_id,
            tool_name=args.tool_name,
            rule_id=args.rule_id,
            requested_by=args.requested_by,
        )
        if args.json:
            data = {
                "job_id": job.job_id,
                "status": job.status,
                "limit": job.limit,
                "tenant_id": job.tenant_id,
                "tool_name": job.tool_name,
                "rule_id": job.rule_id,
                "requested_by": job.requested_by,
                "created_at": job.created_at.isoformat(),
            }
            print(json.dumps(data, indent=2, default=str))
        else:
            print("Policy replay job queued")
            print()
            print(f"Job ID:       {job.job_id}")
            print(f"Status:       {job.status}")
            print(f"Requested by: {job.requested_by or 'anonymous'}")
            print()
            print(f"Run with: agentapp policy run-job {job.job_id} --config {args.config}")
        return 0

    # Synchronous mode (default)
    try:
        result = await runner.run_replay(
            limit=args.limit,
            tenant_id=args.tenant_id,
            tool_name=args.tool_name,
            rule_id=args.rule_id,
        )
    except Exception as exc:
        print(f"Error during replay: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "replay_id": result.replay.replay_id,
            "status": result.replay.status,
            "source_decision_count": result.replay.source_decision_count,
            "changed_count": result.replay.changed_count,
            "unchanged_count": result.replay.unchanged_count,
            "failed_count": result.replay.failed_count,
            "created_at": result.replay.created_at.isoformat(),
            "changes": [
                {
                    "decision_id": c.decision_id,
                    "original_action": c.original_action,
                    "replayed_action": c.replayed_action,
                    "changed": c.changed,
                    "original_rule_id": c.original_rule_id,
                    "replayed_rule_id": c.replayed_rule_id,
                    "reason": c.reason,
                    "context_metadata": c.context_metadata,
                }
                for c in result.changes
            ],
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print("Policy replay completed")
        print()
        print(f"Replay ID:     {result.replay.replay_id}")
        print(f"Source decisions: {result.replay.source_decision_count}")
        print(f"Changed:       {result.replay.changed_count}")
        print(f"Unchanged:     {result.replay.unchanged_count}")
        print(f"Failed:        {result.replay.failed_count}")
        print()
        if result.changes:
            print("Changes:")
            for c in result.changes:
                if c.changed:
                    print(
                        f"  {c.decision_id}: "
                        f"{c.original_action} -> {c.replayed_action} "
                        f"(rule: {c.replayed_rule_id or 'default'})"
                    )
            if result.replay.failed_count > 0:
                print()
                print("Failures:")
                for c in result.changes:
                    if c.replayed_action == "error":
                        print(f"  {c.decision_id}: {c.reason}")
            if result.replay.failed_count == 0 and result.replay.changed_count == 0:
                print("  All decisions produced the same action. No regressions detected.")

    return 0


async def _cmd_policy_replay_run_job(args: argparse.Namespace) -> int:
    """Run a queued replay job."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_replay import PolicyReplayRunner
    from agent_app.runtime.policy_replay_store import create_replay_store
    from agent_app.runtime.policy_replay_jobs import create_replay_job_store
    from agent_app.runtime.policy_replay_background import PolicyReplayBackgroundRunner
    from agent_app.governance.policy_replay_context import PolicyReplayContextBuilder

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    store = getattr(app, "policy_decision_store", None)
    if store is None:
        print("Policy decision store not configured.", file=sys.stderr)
        return 1

    engine = getattr(app, "policy_engine", None)
    if engine is None:
        from agent_app.governance.policy import DefaultPolicyEngine
        engine = DefaultPolicyEngine()

    replay_store = create_replay_store(
        store_type=args.store,
        db_path=args.db_path,
    )
    job_store = create_replay_job_store(
        store_type=args.store,
        db_path=args.db_path,
    )
    context_builder = PolicyReplayContextBuilder()

    runner = PolicyReplayRunner(
        decision_store=store,
        policy_engine=engine,
        replay_store=replay_store,
        context_builder=context_builder,
    )
    bg_runner = PolicyReplayBackgroundRunner(
        replay_runner=runner,
        job_store=job_store,
        replay_store=replay_store,
    )

    try:
        job = await bg_runner.run_job(args.job_id)
    except KeyError:
        print(f"Job '{args.job_id}' not found.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error running job: {exc}", file=sys.stderr)
        return 1

    if args.json:
        output = {
            "job_id": job.job_id,
            "status": job.status,
            "replay_id": job.replay_id,
            "requested_by": job.requested_by,
            "error": job.error,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }
        print(json.dumps(output, indent=2, default=str))
    else:
        if job.status == "completed":
            print("Policy replay job completed")
            print()
            print(f"Job ID:       {job.job_id}")
            print(f"Replay ID:    {job.replay_id}")
            print(f"Status:       {job.status}")
        elif job.status == "failed":
            print("Policy replay job failed")
            print()
            print(f"Job ID:  {job.job_id}")
            print(f"Error:   {job.error}")
        else:
            print(f"Job ID: {job.job_id}")
            print(f"Status: {job.status}")

    return 0 if job.status == "completed" else 1


async def _cmd_policy_replay_jobs(args: argparse.Namespace) -> int:
    """List replay jobs."""
    from agent_app.runtime.policy_replay_jobs import create_replay_job_store

    job_store = create_replay_job_store(
        store_type=args.store,
        db_path=args.db_path,
    )

    jobs = await job_store.list(limit=args.limit)

    if not jobs:
        print("No replay jobs found.")
        return 0

    if args.json:
        data = [
            {
                "job_id": j.job_id,
                "status": j.status,
                "replay_id": j.replay_id,
                "limit": j.limit,
                "tenant_id": j.tenant_id,
                "tool_name": j.tool_name,
                "rule_id": j.rule_id,
                "requested_by": j.requested_by,
                "error": j.error,
                "created_at": j.created_at.isoformat(),
                "started_at": j.started_at.isoformat() if j.started_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            }
            for j in jobs
        ]
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"{'Job ID':<20} {'Status':<12} {'Replay ID':<20} {'Tenant':<15} {'Created'}")
        print("-" * 85)
        for j in jobs:
            print(
                f"{j.job_id:<20} {j.status:<12} "
                f"{(j.replay_id or '—'):<20} "
                f"{(j.tenant_id or '—'):<15} "
                f"{j.created_at.isoformat()[:19]}"
            )

    return 0


# -- Phase 29: Policy Release CLI commands --


async def _cmd_policy_bundle_create(args: argparse.Namespace) -> int:
    """Create a policy bundle."""
    from agent_app.config.loader import build_app
    from agent_app.runtime.policy_release import PolicyReleaseService

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    try:
        bundle = await service.create_bundle(
            name=args.name,
            version=args.version,
            config_path=args.config_path,
            description=args.description,
            created_by=args.created_by,
        )
    except Exception as exc:
        print(f"Error creating bundle: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "bundle_id": bundle.bundle_id,
            "name": bundle.name,
            "version": bundle.version,
            "status": bundle.status,
            "config_hash": bundle.config_hash,
            "description": bundle.description,
            "created_by": bundle.created_by,
            "created_at": bundle.created_at.isoformat(),
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print("Policy bundle created")
        print()
        print(f"Bundle ID:    {bundle.bundle_id}")
        print(f"Name:         {bundle.name}")
        print(f"Version:      {bundle.version}")
        print(f"Status:       {bundle.status}")
        print(f"Config Hash:  {bundle.config_hash[:16]}...")
        if bundle.description:
            print(f"Description:  {bundle.description}")
        print(f"Created By:   {bundle.created_by or 'anonymous'}")
    return 0


async def _cmd_policy_bundle_list(args: argparse.Namespace) -> int:
    """List policy bundles."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    store = _get_bundle_store(app)
    if store is None:
        print("Policy bundle store not configured.", file=sys.stderr)
        return 1

    bundles = await store.list(limit=args.limit)

    if not bundles:
        print("No policy bundles found.")
        return 0

    if args.json:
        data = []
        for b in bundles:
            data.append({
                "bundle_id": b.bundle_id,
                "name": b.name,
                "version": b.version,
                "status": b.status,
                "config_hash": b.config_hash[:16] + "...",
                "description": b.description,
                "created_by": b.created_by,
                "created_at": b.created_at.isoformat(),
                "activated_at": b.activated_at.isoformat() if b.activated_at else None,
                "archived_at": b.archived_at.isoformat() if b.archived_at else None,
            })
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"{'Bundle ID':<20} {'Name':<20} {'Version':<10} {'Status':<12} {'Created'}")
        print("-" * 80)
        for b in bundles:
            print(
                f"{b.bundle_id:<20} {b.name:<20} {b.version:<10} "
                f"{b.status:<12} {b.created_at.isoformat()[:19]}"
            )
    return 0


async def _cmd_policy_bundle_active(args: argparse.Namespace) -> int:
    """Show the active policy bundle."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    store = _get_bundle_store(app)
    if store is None:
        print("Policy bundle store not configured.", file=sys.stderr)
        return 1

    bundle = await store.get_active()
    if bundle is None:
        print("No active policy bundle found.")
        return 0

    if args.json:
        data = {
            "bundle_id": bundle.bundle_id,
            "name": bundle.name,
            "version": bundle.version,
            "status": bundle.status,
            "config_hash": bundle.config_hash[:16] + "...",
            "activated_at": bundle.activated_at.isoformat() if bundle.activated_at else None,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print("Active Policy Bundle")
        print("=" * 40)
        print(f"Bundle ID:    {bundle.bundle_id}")
        print(f"Name:         {bundle.name}")
        print(f"Version:      {bundle.version}")
        print(f"Status:       {bundle.status}")
        print(f"Config Hash:  {bundle.config_hash[:16]}...")
        if bundle.activated_at:
            print(f"Activated:    {bundle.activated_at.isoformat()}")
    return 0


async def _cmd_policy_bundle_promote(args: argparse.Namespace) -> int:
    """Promote a bundle to ACTIVE."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    try:
        bundle = await service.promote(
            bundle_id=args.bundle_id,
            promoted_by=args.promoted_by,
        )
    except KeyError:
        print(f"Bundle '{args.bundle_id}' not found.", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "bundle_id": bundle.bundle_id,
            "name": bundle.name,
            "version": bundle.version,
            "status": bundle.status,
            "activated_at": bundle.activated_at.isoformat() if bundle.activated_at else None,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print("Bundle promoted")
        print()
        print(f"Bundle ID:    {bundle.bundle_id}")
        print(f"Name:         {bundle.name}")
        print(f"Version:      {bundle.version}")
        print(f"Status:       {bundle.status}")
        print(f"Activated:    {bundle.activated_at.isoformat() if bundle.activated_at else 'N/A'}")
    return 0


async def _cmd_policy_bundle_rollback(args: argparse.Namespace) -> int:
    """Rollback to a previous bundle."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    try:
        bundle = await service.rollback(
            target_bundle_id=args.bundle_id,
            rolled_back_by=args.rolled_back_by,
        )
    except KeyError:
        print(f"Bundle '{args.bundle_id}' not found.", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "bundle_id": bundle.bundle_id,
            "name": bundle.name,
            "version": bundle.version,
            "status": bundle.status,
            "activated_at": bundle.activated_at.isoformat() if bundle.activated_at else None,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print("Bundle rollback complete")
        print()
        print(f"Bundle ID:    {bundle.bundle_id}")
        print(f"Name:         {bundle.name}")
        print(f"Version:      {bundle.version}")
        print(f"Status:       {bundle.status}")
        print(f"Activated:    {bundle.activated_at.isoformat() if bundle.activated_at else 'N/A'}")
    return 0


async def _cmd_policy_gate_run(args: argparse.Namespace) -> int:
    """Run release gate evaluation for a bundle."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    try:
        result = await service.run_gate(
            bundle_id=args.bundle_id,
            limit=args.limit,
            tenant_id=args.tenant_id,
            tool_name=args.tool_name,
            rule_id=args.rule_id,
            created_by=args.created_by,
        )
    except KeyError:
        print(f"Bundle '{args.bundle_id}' not found.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error running gate: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "gate_result_id": result.gate_result_id,
            "bundle_id": result.bundle_id,
            "replay_id": result.replay_id,
            "status": result.status,
            "passed": result.passed,
            "total_decisions": result.total_decisions,
            "changed_decisions": result.changed_decisions,
            "failed_replays": result.failed_replays,
            "changed_ratio": result.changed_ratio,
            "new_denies": result.new_denies,
            "new_approvals": result.new_approvals,
            "missing_context_count": result.missing_context_count,
            "rule_results": result.rule_results,
            "summary": result.summary,
            "created_by": result.created_by,
            "created_at": result.created_at.isoformat(),
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        status_icon = "PASSED" if result.passed else "FAILED"
        print(f"Gate evaluation: {status_icon}")
        print()
        print(f"Gate Result ID: {result.gate_result_id}")
        print(f"Bundle ID:      {result.bundle_id}")
        print(f"Replay ID:      {result.replay_id}")
        print(f"Status:         {result.status}")
        print()
        print("Metrics:")
        print(f"  Total decisions:    {result.total_decisions}")
        print(f"  Changed decisions:  {result.changed_decisions}")
        print(f"  Changed ratio:      {result.changed_ratio:.2%}")
        print(f"  Failed replays:     {result.failed_replays}")
        print(f"  New denies:         {result.new_denies}")
        print(f"  Missing context:    {result.missing_context_count}")
        if result.rule_results:
            print()
            print("Rule Results:")
            for rr in result.rule_results:
                status_mark = "PASS" if rr["status"] == "passed" else "FAIL"
                print(f"  [{status_mark}] {rr['rule_name']}")
                for failure in rr.get("failures", []):
                    print(f"         - {failure}")
    return 0 if result.passed else 1


async def _cmd_policy_gate_list(args: argparse.Namespace) -> int:
    """List gate evaluation results."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    store = _get_gate_store(app)
    if store is None:
        print("Policy gate store not configured.", file=sys.stderr)
        return 1

    results = await store.list(bundle_id=args.bundle_id, limit=args.limit)

    if not results:
        print("No gate results found.")
        return 0

    if args.json:
        data = []
        for r in results:
            data.append({
                "gate_result_id": r.gate_result_id,
                "bundle_id": r.bundle_id,
                "replay_id": r.replay_id,
                "status": r.status,
                "passed": r.passed,
                "total_decisions": r.total_decisions,
                "changed_decisions": r.changed_decisions,
                "changed_ratio": r.changed_ratio,
                "failed_replays": r.failed_replays,
                "created_at": r.created_at.isoformat(),
                "created_by": r.created_by,
            })
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"{'Gate ID':<20} {'Bundle ID':<20} {'Status':<10} {'Changed':<10} {'Created'}")
        print("-" * 80)
        for r in results:
            print(
                f"{r.gate_result_id:<20} {r.bundle_id:<20} "
                f"{r.status:<10} {r.changed_decisions:<10} "
                f"{r.created_at.isoformat()[:19]}"
            )
    return 0


def _get_bundle_store(app: Any) -> Any:
    """Get the policy bundle store from the app."""
    service = _get_release_service(app)
    if service is not None:
        return service._bundle_store
    return None


def _get_gate_store(app: Any) -> Any:
    """Get the policy gate store from the app."""
    service = _get_release_service(app)
    if service is not None:
        return service._gate_store
    return None


def _get_promotion_store(app: Any) -> Any:
    """Get the promotion request store from the app."""
    service = _get_release_service(app)
    if service is not None:
        return getattr(service, "promotion_store", None)
    return None


def _get_release_service(app: Any) -> Any:
    """Get or create the policy release service from the app."""
    release_config = getattr(app, "_release_config", None)
    if release_config is None:
        return None

    # Check if already created
    existing = getattr(app, "_release_service", None)
    if existing is not None:
        return existing

    # Create stores from config
    bundle_store_type = getattr(release_config.bundles, "type", "memory")
    bundle_db_path = getattr(release_config.bundles, "path", None)
    gate_store_type = getattr(release_config.gates, "type", "memory")
    gate_db_path = getattr(release_config.gates, "path", None)

    from agent_app.governance.policy_bundle import create_bundle_store
    from agent_app.runtime.policy_gate_store import create_gate_store
    from agent_app.governance.policy_gate import PolicyGateEvaluator, PolicyGateRule

    bundle_store = create_bundle_store(
        store_type=bundle_store_type,
        db_path=bundle_db_path,
    )
    gate_store = create_gate_store(
        store_type=gate_store_type,
        db_path=gate_db_path,
    )

    # -- Phase 30: Promotion store --
    promotion_store = None
    if getattr(release_config, "promotions", None):
        promo_type = getattr(release_config.promotions, "type", "memory")
        promo_path = getattr(release_config.promotions, "path", None)
        from agent_app.runtime.promotion_store import create_promotion_store
        promotion_store = create_promotion_store(
            store_type=promo_type,
            db_path=promo_path,
        )

    # -- Phase 31: Activation store --
    activation_store = None
    if getattr(release_config, "activations", None):
        act_type = getattr(release_config.activations, "type", "memory")
        act_path = getattr(release_config.activations, "path", None)
        from agent_app.runtime.policy_activation_store import create_policy_activation_store
        activation_store = create_policy_activation_store(
            store_type=act_type,
            db_path=act_path,
        )

    # -- Phase 31: Policy resolver --
    policy_resolver = None
    if activation_store is not None:
        from agent_app.runtime.policy_resolver import ActivePolicyResolver
        policy_resolver = ActivePolicyResolver(
            activation_store=activation_store,
            bundle_store=None,  # will be set after bundle_store creation
        )

    # -- Phase 32: Environment store --
    environment_store = None
    if getattr(release_config, "environments", None):
        env_type = getattr(release_config.environments, "type", "memory")
        env_path = getattr(release_config.environments, "path", None)
        from agent_app.runtime.policy_environment_store import create_policy_environment_store
        environment_store = create_policy_environment_store(
            store_type=env_type,
            db_path=env_path,
        )

    # -- Phase 33: Ring store --
    ring_store = None
    if getattr(release_config, "rings", None):
        ring_type = getattr(release_config.rings, "type", "memory")
        ring_path = getattr(release_config.rings, "path", None)
        from agent_app.runtime.policy_ring_store import create_release_ring_store
        ring_store = create_release_ring_store(
            store_type=ring_type,
            db_path=ring_path,
        )

    # -- Phase 33: Ring assignment store --
    ring_assignment_store = None
    if getattr(release_config, "ring_assignments", None):
        ra_type = getattr(release_config.ring_assignments, "type", "memory")
        ra_path = getattr(release_config.ring_assignments, "path", None)
        from agent_app.runtime.policy_ring_assignment_store import create_ring_assignment_store
        ring_assignment_store = create_ring_assignment_store(
            store_type=ra_type,
            db_path=ra_path,
        )

    # Build rules from config
    rules = []
    for rule_cfg in getattr(release_config, "rules", []):
        rules.append(PolicyGateRule(
            name=rule_cfg.name,
            description=getattr(rule_cfg, "description", None),
            max_changed_decisions=getattr(rule_cfg, "max_changed_decisions", None),
            max_changed_ratio=getattr(rule_cfg, "max_changed_ratio", None),
            max_failed_replays=getattr(rule_cfg, "max_failed_replays", None),
            max_new_denies=getattr(rule_cfg, "max_new_denies", None),
            max_new_approvals=getattr(rule_cfg, "max_new_approvals", None),
            fail_on_missing_required_context=getattr(rule_cfg, "fail_on_missing_required_context", False),
        ))
    evaluator = PolicyGateEvaluator(rules=rules)

    # Get replay runner components from app
    decision_store = getattr(app, "policy_decision_store", None)
    policy_engine = getattr(app, "policy_engine", None)

    from agent_app.governance.policy_replay import PolicyReplayRunner
    from agent_app.governance.policy_replay_context import PolicyReplayContextBuilder
    from agent_app.runtime.policy_replay_store import create_replay_store
    from agent_app.runtime.policy_release import PolicyReleaseService

    replay_runner = PolicyReplayRunner(
        decision_store=decision_store,
        policy_engine=policy_engine,
        replay_store=None,
        context_builder=PolicyReplayContextBuilder(),
    )

    service = PolicyReleaseService(
        bundle_store=bundle_store,
        replay_runner=replay_runner,
        replay_store=None,
        gate_evaluator=evaluator,
        gate_store=gate_store,
        promotion_store=promotion_store,
        allow_gate_bypass=getattr(release_config, "allow_gate_bypass", False),
        activation_store=activation_store,
        policy_resolver=policy_resolver,
        environment_store=environment_store,
        ring_store=ring_store,
        ring_assignment_store=ring_assignment_store,
    )
    # Wire bundle_store into resolver now that it exists
    if policy_resolver is not None:
        policy_resolver._bundle_store = bundle_store
    app._release_service = service
    return service


def _build_context(actor_id: str, permissions: list[str], roles: list[str] | None = None, tenant_id: str = "default") -> RunContext:
    """Build a RunContext for CLI invocations."""
    from agent_app.core.context import RunContext
    return RunContext(
        run_id=f"cli_{actor_id}",
        user_id=actor_id,
        tenant_id=tenant_id,
        permissions=permissions,
        roles=roles or [],
    )


async def _cmd_policy_promotion_request(args: argparse.Namespace) -> int:
    """Request promotion of a policy bundle."""
    from agent_app.config.loader import build_app
    from agent_app.core.context import RunContext
    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1
    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1
    context = _build_context(args.actor_id, args.permissions)
    try:
        req = await service.request_promotion(
            bundle_id=args.bundle_id, requested_by=args.actor_id,
            context=context, reason=args.reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error requesting promotion: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({
            "promotion_id": req.promotion_id, "bundle_id": req.bundle_id,
            "status": req.status.value if hasattr(req.status, "value") else req.status,
            "requested_by": req.requested_by,
            "reason": req.reason, "created_at": req.created_at.isoformat(),
        }, indent=2, default=str))
    else:
        status_str = req.status.value if hasattr(req.status, "value") else req.status
        print("Promotion request created")
        print()
        print(f"Promotion ID:  {req.promotion_id}")
        print(f"Bundle ID:     {req.bundle_id}")
        print(f"Status:        {status_str}")
        print(f"Requested By:  {req.requested_by}")
        if req.reason:
            print(f"Reason:        {req.reason}")
    return 0


async def _cmd_policy_promotion_list(args: argparse.Namespace) -> int:
    """List promotion requests."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_promotion import PromotionRequestStatus
    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1
    store = _get_promotion_store(app)
    if store is None:
        print("Promotion store not configured.", file=sys.stderr)
        return 1
    status = None
    if args.status:
        try:
            status = PromotionRequestStatus(args.status)
        except ValueError:
            print(f"Invalid status: '{args.status}'. Valid: pending, approved, rejected, executed, cancelled", file=sys.stderr)
            return 1
    requests = await store.list(status=status)
    if not requests:
        print("No promotion requests.")
        return 0
    if args.json:
        print(json.dumps([{
            "promotion_id": r.promotion_id, "bundle_id": r.bundle_id,
            "status": r.status.value if hasattr(r.status, "value") else r.status,
            "requested_by": r.requested_by,
            "resolved_by": r.resolved_by, "executed_by": r.executed_by,
            "reason": r.reason, "created_at": r.created_at.isoformat(),
        } for r in requests], indent=2, default=str))
    else:
        print(f"{'Promotion ID':<20} {'Bundle ID':<20} {'Status':<12} {'Requested By':<15} {'Created'}")
        print("-" * 85)
        for r in requests:
            status_str = r.status.value if hasattr(r.status, "value") else r.status
            print(f"{r.promotion_id:<20} {r.bundle_id:<20} {status_str:<12} {r.requested_by:<15} {r.created_at.isoformat()[:19]}")
    return 0


async def _cmd_policy_promotion_approve(args: argparse.Namespace) -> int:
    """Approve a promotion request."""
    from agent_app.config.loader import build_app
    from agent_app.core.context import RunContext
    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1
    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1
    context = _build_context(args.actor_id, args.permissions)
    try:
        req = await service.approve_promotion(
            promotion_id=args.promotion_id,
            approved_by=args.actor_id,
            context=context,
            reason=args.reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error approving promotion: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({
            "promotion_id": req.promotion_id, "bundle_id": req.bundle_id,
            "status": req.status.value if hasattr(req.status, "value") else req.status,
            "resolved_by": req.resolved_by,
            "approval_reason": req.approval_reason,
            "resolved_at": req.resolved_at.isoformat() if req.resolved_at else None,
        }, indent=2, default=str))
    else:
        status_str = req.status.value if hasattr(req.status, "value") else req.status
        print("Promotion approved")
        print()
        print(f"Promotion ID:  {req.promotion_id}")
        print(f"Bundle ID:     {req.bundle_id}")
        print(f"Status:        {status_str}")
        print(f"Resolved By:   {req.resolved_by}")
        if req.approval_reason:
            print(f"Reason:        {req.approval_reason}")
    return 0


async def _cmd_policy_promotion_reject(args: argparse.Namespace) -> int:
    """Reject a promotion request."""
    from agent_app.config.loader import build_app
    from agent_app.core.context import RunContext
    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1
    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1
    context = _build_context(args.actor_id, args.permissions)
    try:
        req = await service.reject_promotion(
            promotion_id=args.promotion_id,
            rejected_by=args.actor_id,
            context=context,
            reason=args.reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error rejecting promotion: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({
            "promotion_id": req.promotion_id, "bundle_id": req.bundle_id,
            "status": req.status.value if hasattr(req.status, "value") else req.status,
            "resolved_by": req.resolved_by,
            "rejection_reason": req.rejection_reason,
            "resolved_at": req.resolved_at.isoformat() if req.resolved_at else None,
        }, indent=2, default=str))
    else:
        status_str = req.status.value if hasattr(req.status, "value") else req.status
        print("Promotion rejected")
        print()
        print(f"Promotion ID:  {req.promotion_id}")
        print(f"Bundle ID:     {req.bundle_id}")
        print(f"Status:        {status_str}")
        print(f"Resolved By:   {req.resolved_by}")
        if req.rejection_reason:
            print(f"Reason:        {req.rejection_reason}")
    return 0


async def _cmd_policy_promotion_execute(args: argparse.Namespace) -> int:
    """Execute an approved promotion."""
    from agent_app.config.loader import build_app
    from agent_app.core.context import RunContext
    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1
    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1
    context = _build_context(args.actor_id, args.permissions)
    try:
        result = await service.execute_promotion(
            promotion_id=args.promotion_id,
            executed_by=args.actor_id,
            context=context,
            bypass_gate=args.bypass_gate,
            bypass_reason=args.bypass_reason,
            environment=args.environment,
            reason=args.reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error executing promotion: {exc}", file=sys.stderr)
        return 1

    # Handle both PolicyActivation (Phase 31) and PolicyBundle return types
    activation_id = getattr(result, "activation_id", None)
    environment = getattr(result, "environment", "prod")
    activated_by = getattr(result, "activated_by", args.actor_id)

    if args.json:
        data = {
            "bundle_id": result.bundle_id,
            "status": getattr(result, "status", "ACTIVE"),
        }
        if activation_id:
            data["activation_id"] = activation_id
            data["environment"] = environment
            data["activated_by"] = activated_by
            data["reason"] = getattr(result, "reason", None)
        print(json.dumps(data, indent=2, default=str))
    else:
        if activation_id:
            print("Promotion executed — bundle activated")
            print()
            print(f"Activation ID: {activation_id}")
            print(f"Environment:   {environment}")
            print(f"Bundle ID:     {result.bundle_id}")
            print(f"By:            {activated_by}")
            reason = getattr(result, "reason", None)
            if reason:
                print(f"Reason:        {reason}")
        else:
            print("Promotion executed — bundle activated")
            print()
            print(f"Bundle ID:    {result.bundle_id}")
            print(f"Name:         {getattr(result, 'name', 'N/A')}")
            print(f"Version:      {getattr(result, 'version', 'N/A')}")
            print(f"Status:       {result.status}")
            print(f"Activated:    {result.activated_at.isoformat() if getattr(result, 'activated_at', None) else 'N/A'}")
    return 0


async def _cmd_policy_activation_list(args: argparse.Namespace) -> int:
    """List policy activations, optionally filtered by environment."""
    from agent_app.config.loader import build_app
    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1
    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1
    activations = await service.list_activations(environment=args.environment)
    if not activations:
        print("No activations found.")
        return 0
    if args.json:
        data = []
        for act in activations:
            data.append({
                "activation_id": act.activation_id,
                "environment": act.environment,
                "bundle_id": act.bundle_id,
                "status": act.status.value if hasattr(act.status, "value") else act.status,
                "activated_by": act.activated_by,
                "activated_at": act.activated_at.isoformat() if act.activated_at else None,
                "reason": act.reason,
            })
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"{'Activation ID':<20} {'Env':<10} {'Bundle ID':<20} {'Status':<12} {'By':<15}")
        print("-" * 80)
        for act in activations:
            status_str = act.status.value if hasattr(act.status, "value") else act.status
            print(
                f"{act.activation_id:<20} {act.environment:<10} "
                f"{act.bundle_id:<20} {status_str:<12} "
                f"{act.activated_by:<15}"
            )
    return 0


async def _cmd_policy_activation_active(args: argparse.Namespace) -> int:
    """Show the active policy bundle for an environment."""
    from agent_app.config.loader import build_app
    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1
    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1
    try:
        bundle = await service.require_active_policy(args.environment)
    except (KeyError, RuntimeError) as exc:
        print(f"No active policy: {exc}")
        return 0
    if args.json:
        data = {
            "bundle_id": bundle.bundle_id,
            "name": bundle.name,
            "version": bundle.version,
            "status": bundle.status,
            "config_hash": bundle.config_hash[:16] + "...",
        }
        if getattr(bundle, "activated_at", None):
            data["activated_at"] = bundle.activated_at.isoformat()
        print(json.dumps(data, indent=2, default=str))
    else:
        print(
            f"Active bundle for '{args.environment}': {bundle.bundle_id} "
            f"(v{bundle.version}, hash={bundle.config_hash[:12]}...)"
        )
    return 0


# -- Phase 32: Policy environment CLI commands --


async def _cmd_policy_environment_list(args: argparse.Namespace) -> int:
    """List policy environment states."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_rbac import PolicyReleasePermission

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    context = _build_context("cli_viewer", args.permissions)
    try:
        await service._check_permission(PolicyReleasePermission.ENVIRONMENT_VIEW, context)
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1

    env_store = service.environment_store
    if env_store is None:
        # No store configured; show default state for known environments
        if args.json:
            print(json.dumps([]))
        else:
            print("No environment states configured.")
        return 0

    states = await env_store.list()

    if not states:
        if args.json:
            print(json.dumps([]))
        else:
            print("No environment states found. All environments default to enabled.")
        return 0

    if args.json:
        data = []
        for s in states:
            entry = {
                "environment": s.environment,
                "status": s.status.value if hasattr(s.status, "value") else s.status,
                "disabled_reason": s.disabled_reason,
                "disabled_by": s.disabled_by,
                "disabled_at": s.disabled_at.isoformat() if s.disabled_at else None,
                "enabled_by": s.enabled_by,
                "enabled_at": s.enabled_at.isoformat() if s.enabled_at else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            data.append(entry)
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"{'Environment':<15} {'Status':<12} {'Disabled Reason':<25} {'Disabled By':<15}")
        print("-" * 70)
        for s in states:
            status_str = s.status.value if hasattr(s.status, "value") else s.status
            reason = s.disabled_reason or ""
            disabled_by = s.disabled_by or ""
            print(f"{s.environment:<15} {status_str:<12} {reason:<25} {disabled_by:<15}")
    return 0


async def _cmd_policy_environment_disable(args: argparse.Namespace) -> int:
    """Disable a policy environment."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_rbac import PolicyReleasePermission

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)
    try:
        await service._check_permission(PolicyReleasePermission.ENVIRONMENT_DISABLE, context)
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1

    env_store = service.environment_store
    if env_store is None:
        print("Environment store not configured.", file=sys.stderr)
        return 1

    try:
        state = await env_store.disable(
            environment=args.env_name,
            disabled_by=args.actor_id,
            reason=args.reason,
        )
    except Exception as exc:
        print(f"Error disabling environment: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "environment": state.environment,
            "status": state.status.value if hasattr(state.status, "value") else state.status,
            "disabled_reason": state.disabled_reason,
            "disabled_by": state.disabled_by,
            "disabled_at": state.disabled_at.isoformat() if state.disabled_at else None,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"Environment '{state.environment}' disabled")
        print(f"  Reason:    {state.disabled_reason}")
        print(f"  Disabled by: {state.disabled_by}")
        if state.disabled_at:
            print(f"  At:        {state.disabled_at.isoformat()}")
    return 0


async def _cmd_policy_environment_enable(args: argparse.Namespace) -> int:
    """Re-enable a disabled policy environment."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_rbac import PolicyReleasePermission

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)
    try:
        await service._check_permission(PolicyReleasePermission.ENVIRONMENT_ENABLE, context)
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1

    env_store = service.environment_store
    if env_store is None:
        print("Environment store not configured.", file=sys.stderr)
        return 1

    try:
        state = await env_store.enable(
            environment=args.env_name,
            enabled_by=args.actor_id,
            reason=args.reason,
        )
    except Exception as exc:
        print(f"Error enabling environment: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "environment": state.environment,
            "status": state.status.value if hasattr(state.status, "value") else state.status,
            "enabled_by": state.enabled_by,
            "enabled_at": state.enabled_at.isoformat() if state.enabled_at else None,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"Environment '{state.environment}' enabled")
        print(f"  Enabled by: {state.enabled_by}")
        if state.enabled_at:
            print(f"  At:         {state.enabled_at.isoformat()}")
        if args.reason:
            print(f"  Reason:     {args.reason}")
    return 0


# -- Phase 32: Activation rollback CLI command --


async def _cmd_policy_activation_rollback(args: argparse.Namespace) -> int:
    """Roll back an environment to a previous activation."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)
    try:
        result = await service.rollback_environment(
            environment=args.env_name,
            rolled_back_by=args.actor_id,
            context=context,
            target_activation_id=args.target_activation_id,
            reason=args.reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error rolling back activation: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "activation_id": result.activation_id,
            "environment": result.environment,
            "bundle_id": result.bundle_id,
            "status": result.status.value if hasattr(result.status, "value") else result.status,
            "rolled_back_by": args.actor_id,
            "reason": args.reason,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print("Activation rollback complete")
        print()
        print(f"Activation ID:  {result.activation_id}")
        print(f"Environment:    {result.environment}")
        print(f"Bundle ID:      {result.bundle_id}")
        status_str = result.status.value if hasattr(result.status, "value") else result.status
        print(f"Status:         {status_str}")
        print(f"Rolled back by: {args.actor_id}")
        if args.reason:
            print(f"Reason:         {args.reason}")
    return 0


# -- Phase 33: Ring management CLI commands --


async def _cmd_policy_ring_list(args: argparse.Namespace) -> int:
    """List release rings for an environment."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_rbac import PolicyReleasePermission

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    context = _build_context("cli_viewer", args.permissions)
    try:
        await service._check_permission(PolicyReleasePermission.RING_VIEW, context)
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1

    ring_store = service.ring_store
    if ring_store is None:
        print("Ring store not configured.", file=sys.stderr)
        return 1

    try:
        rings = await ring_store.list(environment=args.environment)
    except Exception as exc:
        print(f"Error listing rings: {exc}", file=sys.stderr)
        return 1

    if not rings:
        if args.json:
            print(json.dumps([]))
        else:
            print("No release rings found.")
        return 0

    if args.json:
        data = []
        for r in rings:
            entry = {
                "ring_id": r.ring_id,
                "name": r.name,
                "environment": r.environment,
                "status": r.status.value if hasattr(r.status, "value") else r.status,
                "is_default": r.is_default,
                "description": r.description,
            }
            data.append(entry)
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"{'Ring ID':<20} {'Name':<15} {'Status':<12} {'Default':<10}")
        print("-" * 60)
        for r in rings:
            status_str = r.status.value if hasattr(r.status, "value") else r.status
            default_str = "yes" if r.is_default else "no"
            print(f"{r.ring_id:<20} {r.name:<15} {status_str:<12} {default_str:<10}")
    return 0


async def _cmd_policy_ring_create(args: argparse.Namespace) -> int:
    """Create a release ring."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)
    try:
        ring = await service.create_ring(
            environment=args.environment,
            name=args.name,
            created_by=args.actor_id,
            context=context,
            description=args.description,
            is_default=args.is_default,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error creating ring: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "ring_id": ring.ring_id,
            "name": ring.name,
            "environment": ring.environment,
            "status": ring.status.value if hasattr(ring.status, "value") else ring.status,
            "is_default": ring.is_default,
            "description": ring.description,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"Ring '{ring.name}' created")
        print()
        print(f"Ring ID:     {ring.ring_id}")
        print(f"Name:        {ring.name}")
        print(f"Environment: {ring.environment}")
        status_str = ring.status.value if hasattr(ring.status, "value") else ring.status
        print(f"Status:      {status_str}")
        if ring.is_default:
            print("Default:     yes")
        if ring.description:
            print(f"Description: {ring.description}")
    return 0


async def _cmd_policy_ring_assign(args: argparse.Namespace) -> int:
    """Assign an activation to a ring."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)
    try:
        assignment = await service.assign_activation_to_ring(
            environment=args.environment,
            ring_name=args.ring_name,
            activation_id=args.activation_id,
            assigned_by=args.actor_id,
            context=context,
            reason=args.reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error assigning activation to ring: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "assignment_id": assignment.assignment_id,
            "environment": assignment.environment,
            "ring_name": assignment.ring_name,
            "activation_id": assignment.activation_id,
            "status": assignment.status.value if hasattr(assignment.status, "value") else assignment.status,
            "assigned_by": assignment.assigned_by,
            "reason": assignment.reason,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"Activation assigned to ring '{assignment.ring_name}'")
        print()
        print(f"Assignment ID: {assignment.assignment_id}")
        print(f"Ring:          {assignment.ring_name}")
        print(f"Activation ID: {assignment.activation_id}")
        status_str = assignment.status.value if hasattr(assignment.status, "value") else assignment.status
        print(f"Status:        {status_str}")
        print(f"Assigned by:   {assignment.assigned_by}")
        if assignment.reason:
            print(f"Reason:        {assignment.reason}")
    return 0


async def _cmd_policy_ring_promote(args: argparse.Namespace) -> int:
    """Promote canary ring's activation to stable ring."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)
    try:
        assignment = await service.promote_canary_to_stable(
            environment=args.environment,
            canary_ring=args.from_ring,
            stable_ring=args.to_ring,
            promoted_by=args.actor_id,
            context=context,
            reason=args.reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error promoting canary to stable: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "assignment_id": assignment.assignment_id,
            "environment": assignment.environment,
            "ring_name": assignment.ring_name,
            "activation_id": assignment.activation_id,
            "status": assignment.status.value if hasattr(assignment.status, "value") else assignment.status,
            "assigned_by": assignment.assigned_by,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"Promoted activation from '{args.from_ring}' to '{args.to_ring}'")
        print()
        print(f"Assignment ID: {assignment.assignment_id}")
        print(f"Target Ring:   {assignment.ring_name}")
        print(f"Activation ID: {assignment.activation_id}")
        status_str = assignment.status.value if hasattr(assignment.status, "value") else assignment.status
        print(f"Status:        {status_str}")
        print(f"Promoted by:   {assignment.assigned_by}")
    return 0


async def _cmd_policy_ring_disable(args: argparse.Namespace) -> int:
    """Disable a release ring."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)
    try:
        ring = await service.disable_ring(
            environment=args.environment,
            ring_name=args.ring_name,
            disabled_by=args.actor_id,
            context=context,
            reason=args.reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error disabling ring: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "ring_id": ring.ring_id,
            "name": ring.name,
            "environment": ring.environment,
            "status": ring.status.value if hasattr(ring.status, "value") else ring.status,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"Ring '{ring.name}' disabled")
        print(f"  Environment: {ring.environment}")
        status_str = ring.status.value if hasattr(ring.status, "value") else ring.status
        print(f"  Status:      {status_str}")
        if args.reason:
            print(f"  Reason:      {args.reason}")
    return 0


async def _cmd_policy_ring_enable(args: argparse.Namespace) -> int:
    """Enable a disabled release ring."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)
    try:
        ring = await service.enable_ring(
            environment=args.environment,
            ring_name=args.ring_name,
            enabled_by=args.actor_id,
            context=context,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error enabling ring: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "ring_id": ring.ring_id,
            "name": ring.name,
            "environment": ring.environment,
            "status": ring.status.value if hasattr(ring.status, "value") else ring.status,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"Ring '{ring.name}' enabled")
        print(f"  Environment: {ring.environment}")
        status_str = ring.status.value if hasattr(ring.status, "value") else ring.status
        print(f"  Status:      {status_str}")
    return 0


# -- Phase 33: Canary eval CLI command --


async def _cmd_policy_canary_eval(args: argparse.Namespace) -> int:
    """Run canary evaluation against an activation."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    ring_store = service.ring_store
    if ring_store is None:
        print("Ring store not configured.", file=sys.stderr)
        return 1

    # Verify the ring exists
    try:
        ring = await ring_store.get_by_name(environment=args.environment, name=args.ring_name)
    except Exception as exc:
        print(f"Error looking up ring: {exc}", file=sys.stderr)
        return 1

    if ring is None:
        print(f"Ring '{args.ring_name}' not found in environment '{args.environment}'.", file=sys.stderr)
        return 1

    # Try to load and run the canary eval runner
    try:
        from agent_app.evals.canary import CanaryEvalRunner, CanaryEvalResult
    except ImportError:
        print("CanaryEvalRunner not available (agent_app.evals.canary not implemented).", file=sys.stderr)
        return 1

    try:
        runner = CanaryEvalRunner(
            ring_store=ring_store,
            ring_assignment_store=service.ring_assignment_store,
        )
        result = await runner.run_eval(
            environment=args.environment,
            ring_name=args.ring_name,
            activation_id=args.activation_id,
            suite_path=args.suite_path,
        )
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error running canary eval: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "ring": args.ring_name,
            "environment": args.environment,
            "activation_id": args.activation_id,
            "passed": result.passed if hasattr(result, "passed") else False,
            "total": result.total if hasattr(result, "total") else 0,
            "passed_count": result.passed_count if hasattr(result, "passed_count") else 0,
            "failed_count": result.failed_count if hasattr(result, "failed_count") else 0,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        passed = result.passed if hasattr(result, "passed") else False
        status = "PASSED" if passed else "FAILED"
        print(f"Canary eval {status}")
        print()
        print(f"Ring:         {args.ring_name}")
        print(f"Environment:  {args.environment}")
        print(f"Activation:   {args.activation_id}")
        if hasattr(result, "total"):
            print(f"Total:        {result.total}")
        if hasattr(result, "passed_count"):
            print(f"Passed:       {result.passed_count}")
        if hasattr(result, "failed_count"):
            print(f"Failed:       {result.failed_count}")

    passed = result.passed if hasattr(result, "passed") else False
    return 0 if passed else 1


# -- Phase 34: Reload, events, and routing CLI commands --


async def _cmd_policy_reload_request(args: argparse.Namespace) -> int:
    """Request a policy reload for an environment/ring."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    reload_manager = getattr(app, "_reload_manager", None)
    if reload_manager is None:
        print("Policy reload manager not configured. Enable change_events in policy_release config.", file=sys.stderr)
        return 1

    try:
        results = await reload_manager.request_reload(
            environment=args.environment,
            ring_name=args.ring_name,
            requested_by=args.actor_id,
            reason=args.reason,
        )
    except Exception as exc:
        print(f"Error requesting reload: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = []
        for r in results:
            entry = {
                "environment": r.target.environment if r.target else None,
                "ring_name": r.target.ring_name if r.target else None,
                "refreshed": r.refreshed,
                "error": r.error,
                "refreshed_at": r.refreshed_at.isoformat() if r.refreshed_at else None,
                "event_id": getattr(r, "event_id", None),
            }
            data.append(entry)
        print(json.dumps(data, indent=2, default=str))
    else:
        print("Policy reload requested")
        print()
        for i, r in enumerate(results):
            env = r.target.environment if r.target else "(all)"
            ring = r.target.ring_name if r.target else "(all)"
            status = "OK" if r.refreshed else "FAILED"
            print(f"  [{status}] environment={env} ring={ring}")
            if r.error:
                print(f"         error: {r.error}")
            if getattr(r, "event_id", None):
                print(f"         event_id: {r.event_id}")
    return 0


async def _cmd_policy_reload_status(args: argparse.Namespace) -> int:
    """Show policy cache status."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_release_service(app)
    if service is None:
        print("Policy release not configured.", file=sys.stderr)
        return 1

    resolver = getattr(service, "policy_resolver", None)
    if resolver is None:
        print("Policy resolver not configured.", file=sys.stderr)
        return 1

    try:
        status = resolver.cache_status()
    except Exception as exc:
        print(f"Error getting cache status: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(status, indent=2, default=str))
    else:
        print("Policy Cache Status")
        print("=" * 40)
        print(f"  Cached entries: {status.get('entries', 0)}")
        print(f"  TTL:            {status.get('ttl', 0)}s")
        keys = status.get("keys", [])
        if keys:
            print(f"  Keys:           {', '.join(keys)}")
        else:
            print("  Keys:           (none)")
    return 0


async def _cmd_policy_events_list(args: argparse.Namespace) -> int:
    """List policy change events."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    event_store = getattr(app, "_event_store", None)
    if event_store is None:
        print("Policy event store not configured. Enable change_events in policy_release config.", file=sys.stderr)
        return 1

    try:
        events = await event_store.list(
            environment=args.environment,
            ring_name=args.ring_name,
            limit=args.limit,
        )
    except Exception as exc:
        print(f"Error listing events: {exc}", file=sys.stderr)
        return 1

    if not events:
        if args.json:
            print(json.dumps([]))
        else:
            print("No policy change events found.")
        return 0

    if args.json:
        data = []
        for e in events:
            entry = {
                "event_id": e.event_id,
                "event_type": e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type),
                "environment": e.environment,
                "ring_name": e.ring_name,
                "actor_id": e.actor_id,
                "reason": e.reason,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            data.append(entry)
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"{'Event ID':<20} {'Type':<25} {'Env':<10} {'Ring':<12} {'Actor':<15} {'Created'}")
        print("-" * 100)
        for e in events:
            etype = e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type)
            ts = e.created_at.isoformat()[:19] if e.created_at else "?"
            print(
                f"{e.event_id:<20} {etype:<25} "
                f"{(e.environment or '—'):<10} {(e.ring_name or '—'):<12} "
                f"{(e.actor_id or '—'):<15} {ts}"
            )
    return 0


async def _cmd_policy_routing_simulate(args: argparse.Namespace) -> int:
    """Simulate policy routing for a given context."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    ring_router = getattr(app, "_ring_router", None)
    if ring_router is None:
        print("Policy ring router not configured. Enable rings and routing in policy_release config.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions, tenant_id=args.tenant_id)

    try:
        result = await ring_router.simulate_routing(args.environment, context)
    except Exception as exc:
        print(f"Error simulating routing: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print("Routing Simulation Result")
        print("=" * 40)
        print(f"  Environment:      {result.get('environment', '?')}")
        print(f"  Selected Ring:    {result.get('selected_ring', '?')}")
        print(f"  Routing Mode:     {result.get('routing_mode', '?')}")
        if result.get("hash_key"):
            print(f"  Hash Key:         {result['hash_key']}")
        if result.get("bucket") is not None:
            print(f"  Bucket:           {result['bucket']}")
        if result.get("canary_percentage") is not None:
            print(f"  Canary %:         {result['canary_percentage']}")
        if result.get("reason"):
            print(f"  Reason:           {result['reason']}")
    return 0


# ---------------------------------------------------------------------------
# Phase 35: Rollout helpers
# ---------------------------------------------------------------------------

def _parse_steps_file(steps_file: str) -> list[dict]:
    """Parse a YAML steps file and return the raw step dicts."""
    import yaml

    with open(steps_file) as f:
        data = yaml.safe_load(f)
    return data.get("steps", [])


def _get_rollout_service(app: Any) -> Any:
    """Get the rollout service from the app, or None if not configured.

    If the service was created with a DefaultPermissionChecker (which has
    a mismatched ``check`` signature for PolicyReleasePermission), replace
    it with a PolicyReleasePermissionChecker so that the service's
    ``_check_permission`` calls work correctly.
    """
    service = getattr(app, "rollout_service", None) or getattr(app, "_rollout_service", None)
    if service is None:
        return None
    # Ensure the permission_checker is compatible with PolicyReleasePermission
    from agent_app.governance.permission import DefaultPermissionChecker
    if isinstance(service._permission_checker, DefaultPermissionChecker):
        from agent_app.governance.policy_rbac import PolicyReleasePermissionChecker
        service._permission_checker = PolicyReleasePermissionChecker()
    return service


async def _cmd_policy_rollout_create(args: argparse.Namespace) -> int:
    """Create a rollout plan from a steps file."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_rollout_service(app)
    if service is None:
        print("Rollout service not configured. Enable rollouts in policy_release config.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)

    # Parse steps file
    try:
        raw_steps = _parse_steps_file(args.steps_file)
    except Exception as exc:
        print(f"Error parsing steps file: {exc}", file=sys.stderr)
        return 1

    if not raw_steps:
        print("Steps file must contain at least one step.", file=sys.stderr)
        return 1

    # Convert raw dicts to RolloutStep objects
    steps: list[RolloutStep] = []
    for raw in raw_steps:
        step_type_str = raw.get("step_type", "activate")
        try:
            step_type = RolloutStepType(step_type_str)
        except ValueError:
            print(f"Invalid step_type '{step_type_str}' in step '{raw.get('step_id', '?')}'", file=sys.stderr)
            return 1
        steps.append(RolloutStep(
            step_id=raw.get("step_id", f"s{len(steps)+1}"),
            step_type=step_type,
            environment=raw.get("environment", "default"),
            ring_name=raw.get("ring_name"),
            from_ring=raw.get("from_ring"),
            to_ring=raw.get("to_ring"),
            required_gate_status=raw.get("required_gate_status"),
            eval_suite=raw.get("eval_suite"),
            requires_approval=raw.get("requires_approval", False),
            require_previous_step=raw.get("require_previous_step"),
        ))

    try:
        plan = await service.create_plan(
            name=args.name,
            bundle_id=args.bundle_id,
            steps=steps,
            created_by=args.actor_id,
            context=context,
            reason=args.reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error creating rollout plan: {exc}", file=sys.stderr)
        return 1

    data = {
        "rollout_id": plan.rollout_id,
        "name": plan.name,
        "bundle_id": plan.bundle_id,
        "status": plan.status.value if hasattr(plan.status, "value") else plan.status,
        "step_count": len(plan.steps),
        "created_by": plan.created_by,
        "created_at": str(plan.created_at),
    }
    print(json.dumps(data, indent=2, default=str))
    return 0


async def _cmd_policy_rollout_list(args: argparse.Namespace) -> int:
    """List rollout plans."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_rollout import RolloutPlanStatus

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_rollout_service(app)
    if service is None:
        print("Rollout service not configured. Enable rollouts in policy_release config.", file=sys.stderr)
        return 1

    rollout_store = getattr(app, "rollout_store", None) or getattr(app, "_rollout_store", None)
    if rollout_store is None:
        print("Rollout store not configured.", file=sys.stderr)
        return 1

    status_filter = None
    if args.status:
        try:
            status_filter = RolloutPlanStatus(args.status)
        except ValueError:
            print(f"Invalid status '{args.status}'. Valid values: {[s.value for s in RolloutPlanStatus]}", file=sys.stderr)
            return 1

    try:
        plans = await rollout_store.list(status=status_filter, bundle_id=args.bundle_id)
    except Exception as exc:
        print(f"Error listing rollout plans: {exc}", file=sys.stderr)
        return 1

    if not plans:
        if args.json:
            print(json.dumps([]))
        else:
            print("No rollout plans found.")
        return 0

    if args.json:
        data = []
        for p in plans:
            data.append({
                "rollout_id": p.rollout_id,
                "name": p.name,
                "bundle_id": p.bundle_id,
                "status": p.status.value if hasattr(p.status, "value") else p.status,
                "step_count": len(p.steps),
                "created_by": p.created_by,
                "created_at": str(p.created_at),
            })
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"{'Rollout ID':<20} {'Name':<20} {'Status':<12} {'Steps':<8} {'Created By':<15}")
        print("-" * 80)
        for p in plans:
            status_str = p.status.value if hasattr(p.status, "value") else p.status
            print(f"{p.rollout_id:<20} {p.name:<20} {status_str:<12} {len(p.steps):<8} {p.created_by:<15}")
    return 0


async def _cmd_policy_rollout_show(args: argparse.Namespace) -> int:
    """Show a specific rollout plan."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    rollout_store = getattr(app, "rollout_store", None) or getattr(app, "_rollout_store", None)
    if rollout_store is None:
        print("Rollout store not configured.", file=sys.stderr)
        return 1

    try:
        plan = await rollout_store.get(args.rollout_id)
    except Exception as exc:
        print(f"Error fetching rollout plan: {exc}", file=sys.stderr)
        return 1

    if plan is None:
        print(f"Rollout plan '{args.rollout_id}' not found.", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "rollout_id": plan.rollout_id,
            "name": plan.name,
            "bundle_id": plan.bundle_id,
            "status": plan.status.value if hasattr(plan.status, "value") else plan.status,
            "steps": [
                {
                    "step_id": s.step_id,
                    "step_type": s.step_type.value if hasattr(s.step_type, "value") else s.step_type,
                    "environment": s.environment,
                    "ring_name": s.ring_name,
                    "status": s.status.value if hasattr(s.status, "value") else s.status,
                    "activation_id": s.activation_id,
                    "assignment_id": s.assignment_id,
                    "error": s.error,
                }
                for s in plan.steps
            ],
            "created_by": plan.created_by,
            "reason": plan.reason,
            "created_at": str(plan.created_at),
            "updated_at": str(plan.updated_at),
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        status_str = plan.status.value if hasattr(plan.status, "value") else plan.status
        print(f"Rollout Plan: {plan.name}")
        print(f"  ID:         {plan.rollout_id}")
        print(f"  Bundle:     {plan.bundle_id}")
        print(f"  Status:     {status_str}")
        print(f"  Created By: {plan.created_by}")
        if plan.reason:
            print(f"  Reason:     {plan.reason}")
        print(f"  Steps ({len(plan.steps)}):")
        for s in plan.steps:
            s_status = s.status.value if hasattr(s.status, "value") else s.status
            s_type = s.step_type.value if hasattr(s.step_type, "value") else s.step_type
            print(f"    {s.step_id}: [{s_type}] env={s.environment} ring={s.ring_name or '-'} status={s_status}")
            if s.error:
                print(f"      Error: {s.error}")
    return 0


async def _cmd_policy_rollout_start(args: argparse.Namespace) -> int:
    """Start a rollout plan (transition from DRAFT to ACTIVE)."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_rollout_service(app)
    if service is None:
        print("Rollout service not configured. Enable rollouts in policy_release config.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)

    try:
        plan = await service.start_plan(
            rollout_id=args.rollout_id,
            started_by=args.actor_id,
            context=context,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Not found: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error starting rollout plan: {exc}", file=sys.stderr)
        return 1

    status_str = plan.status.value if hasattr(plan.status, "value") else plan.status
    data = {
        "rollout_id": plan.rollout_id,
        "name": plan.name,
        "status": status_str,
        "updated_at": str(plan.updated_at),
    }
    print(json.dumps(data, indent=2, default=str))
    return 0


async def _cmd_policy_rollout_run_next(args: argparse.Namespace) -> int:
    """Run the next step in a rollout plan."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_rollout_service(app)
    if service is None:
        print("Rollout service not configured. Enable rollouts in policy_release config.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)

    try:
        plan = await service.run_next_step(
            rollout_id=args.rollout_id,
            actor_id=args.actor_id,
            context=context,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Not found: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error running next step: {exc}", file=sys.stderr)
        return 1

    status_str = plan.status.value if hasattr(plan.status, "value") else plan.status
    data = {
        "rollout_id": plan.rollout_id,
        "name": plan.name,
        "status": status_str,
        "steps": [
            {
                "step_id": s.step_id,
                "step_type": s.step_type.value if hasattr(s.step_type, "value") else s.step_type,
                "status": s.status.value if hasattr(s.status, "value") else s.status,
                "error": s.error,
            }
            for s in plan.steps
        ],
        "updated_at": str(plan.updated_at),
    }
    print(json.dumps(data, indent=2, default=str))
    return 0


async def _cmd_policy_rollout_run_all(args: argparse.Namespace) -> int:
    """Run all available steps in a rollout plan."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_rollout_service(app)
    if service is None:
        print("Rollout service not configured. Enable rollouts in policy_release config.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)

    try:
        plan = await service.run_all_available(
            rollout_id=args.rollout_id,
            actor_id=args.actor_id,
            context=context,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Not found: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error running all available steps: {exc}", file=sys.stderr)
        return 1

    status_str = plan.status.value if hasattr(plan.status, "value") else plan.status
    data = {
        "rollout_id": plan.rollout_id,
        "name": plan.name,
        "status": status_str,
        "steps": [
            {
                "step_id": s.step_id,
                "step_type": s.step_type.value if hasattr(s.step_type, "value") else s.step_type,
                "status": s.status.value if hasattr(s.status, "value") else s.status,
                "error": s.error,
            }
            for s in plan.steps
        ],
        "updated_at": str(plan.updated_at),
    }
    print(json.dumps(data, indent=2, default=str))
    return 0


async def _cmd_policy_rollout_cancel(args: argparse.Namespace) -> int:
    """Cancel a rollout plan."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_rollout_service(app)
    if service is None:
        print("Rollout service not configured. Enable rollouts in policy_release config.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)

    try:
        plan = await service.cancel_plan(
            rollout_id=args.rollout_id,
            cancelled_by=args.actor_id,
            context=context,
            reason=args.reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Not found: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error cancelling rollout plan: {exc}", file=sys.stderr)
        return 1

    status_str = plan.status.value if hasattr(plan.status, "value") else plan.status
    data = {
        "rollout_id": plan.rollout_id,
        "name": plan.name,
        "status": status_str,
        "updated_at": str(plan.updated_at),
    }
    print(json.dumps(data, indent=2, default=str))
    return 0


# --- Phase 36: Rollout approval CLI commands ---


def _approval_to_dict(approval) -> dict:
    """Convert a RolloutStepApproval to a JSON-serializable dict."""
    return {
        "approval_id": approval.approval_id,
        "rollout_id": approval.rollout_id,
        "step_id": approval.step_id,
        "bundle_id": approval.bundle_id,
        "environment": approval.environment,
        "ring_name": approval.ring_name,
        "requested_by": approval.requested_by,
        "requested_reason": approval.requested_reason,
        "status": approval.status.value if hasattr(approval.status, "value") else approval.status,
        "resolved_by": approval.resolved_by,
        "resolved_reason": approval.resolved_reason,
        "created_at": str(approval.created_at),
        "resolved_at": str(approval.resolved_at) if approval.resolved_at else None,
        "policy": {
            "policy_type": approval.policy.policy_type.value,
            "required_approvals": approval.policy.required_approvals,
            "allowed_approver_roles": approval.policy.allowed_approver_roles,
            "allowed_approver_permissions": approval.policy.allowed_approver_permissions,
            "prohibit_requester_approval": approval.policy.prohibit_requester_approval,
            "prohibit_creator_approval": approval.policy.prohibit_creator_approval,
            "expires_after_seconds": approval.policy.expires_after_seconds,
            "require_reason": approval.policy.require_reason,
        } if hasattr(approval, 'policy') and approval.policy else None,
        "decisions": [
            {
                "decision_id": d.decision_id,
                "decision_type": d.decision_type.value,
                "decided_by": d.decided_by,
                "reason": d.reason,
                "roles": d.roles,
                "permissions": d.permissions,
                "created_at": str(d.created_at),
            }
            for d in (approval.decisions if hasattr(approval, 'decisions') else [])
        ],
        "expires_at": str(approval.expires_at) if hasattr(approval, 'expires_at') and approval.expires_at else None,
        "required_approvals": approval.policy.required_approvals if hasattr(approval, 'policy') and approval.policy else 1,
        "current_approvals": sum(1 for d in (approval.decisions if hasattr(approval, 'decisions') else []) if d.decision_type.value == "approve"),
    }


async def _cmd_policy_rollout_approval_list(args: argparse.Namespace) -> int:
    """List rollout step approvals."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_rollout_approval import RolloutStepApprovalStatus

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_rollout_service(app)
    if service is None:
        print("Rollout service not configured. Enable rollouts in policy_release config.", file=sys.stderr)
        return 1

    status_filter = None
    if args.status:
        try:
            status_filter = RolloutStepApprovalStatus(args.status)
        except ValueError:
            print(
                f"Invalid status '{args.status}'. Valid values: {[s.value for s in RolloutStepApprovalStatus]}",
                file=sys.stderr,
            )
            return 1

    context = _build_context("cli_viewer", getattr(args, "permissions", []))

    try:
        approvals = await service.list_step_approvals(
            status=status_filter,
            rollout_id=args.rollout_id,
            context=context,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error listing approvals: {exc}", file=sys.stderr)
        return 1

    if not approvals:
        if args.json:
            print(json.dumps([]))
        else:
            print("No rollout step approvals found.")
        return 0

    if args.json:
        data = [_approval_to_dict(a) for a in approvals]
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"{'Approval ID':<20} {'Rollout ID':<20} {'Step ID':<10} {'Status':<10} {'Approvals':<12} {'Requested By':<15}")
        print("-" * 92)
        for a in approvals:
            status_str = a.status.value if hasattr(a.status, "value") else a.status
            current = sum(1 for d in (a.decisions if hasattr(a, 'decisions') else []) if d.decision_type.value == "approve")
            required = a.policy.required_approvals if hasattr(a, 'policy') and a.policy else 1
            print(f"{a.approval_id:<20} {a.rollout_id:<20} {a.step_id:<10} {status_str:<10} {current}/{required:<11} {a.requested_by:<15}")
    return 0


async def _cmd_policy_rollout_approval_request(args: argparse.Namespace) -> int:
    """Request approval for a rollout step."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_rollout_service(app)
    if service is None:
        print("Rollout service not configured. Enable rollouts in policy_release config.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)

    try:
        approval = await service.request_step_approval(
            rollout_id=args.rollout_id,
            step_id=args.step_id,
            requested_by=args.actor_id,
            context=context,
            reason=args.reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Not found: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error requesting approval: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(_approval_to_dict(approval), indent=2, default=str))
    return 0


async def _cmd_policy_rollout_approval_approve(args: argparse.Namespace) -> int:
    """Approve a rollout step approval."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_rollout_service(app)
    if service is None:
        print("Rollout service not configured. Enable rollouts in policy_release config.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions, roles=args.roles)

    try:
        approval = await service.approve_step(
            approval_id=args.approval_id,
            approved_by=args.actor_id,
            context=context,
            reason=args.reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Not found: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error approving step: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(_approval_to_dict(approval), indent=2, default=str))
    return 0


async def _cmd_policy_rollout_approval_reject(args: argparse.Namespace) -> int:
    """Reject a rollout step approval."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_rollout_service(app)
    if service is None:
        print("Rollout service not configured. Enable rollouts in policy_release config.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions, roles=args.roles)

    try:
        approval = await service.reject_step(
            approval_id=args.approval_id,
            rejected_by=args.actor_id,
            context=context,
            reason=args.reason,
        )
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(f"Not found: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error rejecting step: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(_approval_to_dict(approval), indent=2, default=str))
    return 0


async def _cmd_policy_rollout_approval_expire(args: argparse.Namespace) -> int:
    """Expire pending approvals past their expiration time."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_rollout_service(app)
    if service is None:
        print("Rollout service not configured. Enable rollouts in policy_release config.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)

    try:
        expired = await service.expire_approvals(context=context)
    except Exception as exc:
        print(f"Error expiring approvals: {exc}", file=sys.stderr)
        return 1

    if not expired:
        print("No approvals expired.")
        return 0

    print(json.dumps([_approval_to_dict(a) for a in expired], indent=2, default=str))
    return 0


# --- Phase 38: Runtime policy CLI commands ---


def _get_runtime_policy_store(app):
    """Get runtime policy store from app."""
    return getattr(app, '_runtime_policy_store', None)


def _get_policy_enforcement_service(app):
    """Get policy enforcement service from app."""
    return getattr(app, '_policy_enforcement_service', None)


def _rule_to_dict(rule) -> dict:
    """Convert a RuntimePolicyRule to a JSON-serializable dict."""
    result = {
        "rule_id": rule.rule_id,
        "name": rule.name,
        "action_type": rule.action_type.value,
        "effect": rule.effect.value,
        "status": rule.status.value,
        "tool_name": rule.tool_name,
        "risk_level": rule.risk_level,
        "required_permissions": rule.required_permissions,
        "required_roles": rule.required_roles,
        "reason": rule.reason,
    }
    if rule.approval_policy:
        result["approval_policy"] = {
            "policy_type": rule.approval_policy.policy_type.value,
            "required_approvals": rule.approval_policy.required_approvals,
            "allowed_approver_roles": rule.approval_policy.allowed_approver_roles,
            "allowed_approver_permissions": rule.approval_policy.allowed_approver_permissions,
            "prohibit_requester_approval": rule.approval_policy.prohibit_requester_approval,
            "expires_after_seconds": rule.approval_policy.expires_after_seconds,
        }
    return result


def _decision_to_dict(decision) -> dict:
    """Convert a PolicyEnforcementDecision to a JSON-serializable dict."""
    return {
        "decision_id": decision.decision_id,
        "status": decision.status.value,
        "action_type": decision.action_type.value,
        "subject": decision.subject,
        "reason": decision.reason,
        "required_permissions": decision.required_permissions,
        "required_roles": decision.required_roles,
        "approval_policy": {
            "policy_type": decision.approval_policy.policy_type.value,
            "required_approvals": decision.approval_policy.required_approvals,
        } if decision.approval_policy else None,
    }


def _get_observability_service(app):
    """Get policy observability service from app."""
    return getattr(app, 'policy_observability_service', None)


async def _cmd_policy_observability_report(args: argparse.Namespace) -> int:
    """Generate a policy observability report."""
    from agent_app.config.loader import build_app
    from datetime import datetime

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_observability_service(app)
    if service is None:
        print("Policy observability not configured.", file=sys.stderr)
        return 1

    # Parse window
    window_start = None
    window_end = None
    if args.since:
        try:
            window_start = datetime.fromisoformat(args.since.replace('Z', '+00:00'))
        except ValueError:
            print(f"Invalid datetime format for --since: {args.since}", file=sys.stderr)
            return 1
    if args.until:
        try:
            window_end = datetime.fromisoformat(args.until.replace('Z', '+00:00'))
        except ValueError:
            print(f"Invalid datetime format for --until: {args.until}", file=sys.stderr)
            return 1

    report = await service.generate_report(window_start=window_start, window_end=window_end)

    if args.json:
        from agent_app.runtime.policy_compliance_export import report_to_json
        print(report_to_json(report))
    else:
        # Human-readable output
        print(f"Policy Observability Report: {report.report_id}")
        print(f"Generated: {report.generated_at}")
        if report.window_start:
            print(f"Window: {report.window_start} to {report.window_end or 'now'}")
        print(f"\nTotal Decisions: {report.total_decisions}")
        print("\nBy Status:")
        for dc in report.decisions_by_status:
            print(f"  {dc.status}: {dc.count}")
        print("\nBy Action:")
        for a in report.actions:
            print(f"  {a.action_type}: allowed={a.allowed} denied={a.denied} approval_required={a.approval_required} total={a.total}")
        print("\nTop Actors:")
        for a in report.actors[:5]:
            print(f"  {a.actor_id}: allowed={a.allowed} denied={a.denied} total={a.total}")
        print("\nTop Tools:")
        for t in report.tools[:5]:
            print(f"  {t.tool_name}: allowed={t.allowed} denied={t.denied} total={t.total}")
        if report.approval_latency:
            al = report.approval_latency
            print(f"\nApproval Latency: count={al.count} avg={al.average_seconds}s min={al.min_seconds}s max={al.max_seconds}s")
        if report.top_denials:
            print("\nTop Denials:")
            for d in report.top_denials[:5]:
                print(f"  {d['reason']}: {d['count']}")
    return 0


async def _cmd_policy_observability_export(args: argparse.Namespace) -> int:
    """Export a policy observability report to a file."""
    from agent_app.config.loader import build_app
    from datetime import datetime

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_observability_service(app)
    if service is None:
        print("Policy observability not configured.", file=sys.stderr)
        return 1

    # Parse window
    window_start = None
    window_end = None
    if args.since:
        try:
            window_start = datetime.fromisoformat(args.since.replace('Z', '+00:00'))
        except ValueError:
            print(f"Invalid datetime format for --since: {args.since}", file=sys.stderr)
            return 1
    if args.until:
        try:
            window_end = datetime.fromisoformat(args.until.replace('Z', '+00:00'))
        except ValueError:
            print(f"Invalid datetime format for --until: {args.until}", file=sys.stderr)
            return 1

    report = await service.generate_report(window_start=window_start, window_end=window_end)

    if args.format == "json":
        from agent_app.runtime.policy_compliance_export import report_to_json
        content = report_to_json(report)
    elif args.format == "csv":
        import csv
        import io
        from agent_app.runtime.policy_compliance_export import report_to_csv_rows
        rows = report_to_csv_rows(report)
        if not rows:
            content = "section,key,allowed,denied,approval_required,total\n"
        else:
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
            content = output.getvalue()
    else:
        print(f"Unsupported format '{args.format}'. Supported: json, csv", file=sys.stderr)
        return 1

    try:
        with open(args.output, 'w') as f:
            f.write(content)
        print(f"Report exported to {args.output}")
    except Exception as exc:
        print(f"Error writing file: {exc}", file=sys.stderr)
        return 1

    return 0


# --- Phase 40: Policy simulation CLI commands ---


def _parse_candidate_rules(rules_file: str) -> list:
    """Parse candidate rules from a YAML file and return RuntimePolicyRule objects."""
    import uuid
    import yaml
    from agent_app.governance.policy_enforcement import PolicyActionType
    from agent_app.governance.runtime_policy import RuntimePolicyEffect, RuntimePolicyRule

    with open(rules_file) as f:
        data = yaml.safe_load(f)

    if not data or "rules" not in data:
        raise ValueError("YAML file must contain a top-level 'rules' key")

    rules: list[RuntimePolicyRule] = []
    for raw in data["rules"]:
        try:
            action_type = PolicyActionType(raw["action_type"])
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"Invalid or missing action_type in rule '{raw.get('name', '?')}': {exc}"
            ) from exc

        try:
            effect = RuntimePolicyEffect(raw["effect"])
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"Invalid or missing effect in rule '{raw.get('name', '?')}': {exc}"
            ) from exc

        rules.append(RuntimePolicyRule(
            rule_id=f"rpr_{uuid.uuid4().hex[:12]}",
            name=raw["name"],
            action_type=action_type,
            effect=effect,
            tool_name=raw.get("tool_name"),
            risk_level=raw.get("risk_level"),
            required_permissions=raw.get("required_permissions", []),
            required_roles=raw.get("required_roles", []),
            reason=raw.get("reason"),
        ))

    return rules


async def _cmd_policy_simulation_validate(args: argparse.Namespace) -> int:
    """Validate candidate rules from a YAML file."""
    from agent_app.config.loader import build_app
    from agent_app.runtime.policy_validation import RuntimePolicyValidator

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    # Parse candidate rules
    try:
        rules = _parse_candidate_rules(args.rules_file)
    except Exception as exc:
        print(f"Error parsing rules file: {exc}", file=sys.stderr)
        return 1

    if not rules:
        print("No rules found in the rules file.", file=sys.stderr)
        return 1

    validator = RuntimePolicyValidator()
    report = validator.validate_rules(rules)

    if report.valid and not report.issues:
        print(f"Validation passed. {len(rules)} rule(s) are valid.")
        return 0

    for issue in report.issues:
        level_tag = issue.severity.value.upper()
        rule_info = f" (rule: {issue.rule_id})" if issue.rule_id else ""
        print(f"  [{level_tag}]{rule_info} {issue.code}: {issue.message}")

    error_count = sum(1 for i in report.issues if i.severity.value == "error")
    warning_count = sum(1 for i in report.issues if i.severity.value == "warning")

    print()
    print(f"  {error_count} error(s), {warning_count} warning(s)")

    if not report.valid:
        print()
        print("Validation failed.")
        return 1

    print()
    print(f"Validation passed with warnings. {len(rules)} rule(s) validated.")
    return 0


def _parse_window(args: argparse.Namespace) -> tuple:
    """Parse --since and --until from args, returning (window_start, window_end)."""
    from datetime import datetime

    window_start = None
    window_end = None
    if args.since:
        try:
            window_start = datetime.fromisoformat(args.since.replace('Z', '+00:00'))
        except ValueError:
            print(f"Invalid datetime format for --since: {args.since}", file=sys.stderr)
            return None, None, True
    if args.until:
        try:
            window_end = datetime.fromisoformat(args.until.replace('Z', '+00:00'))
        except ValueError:
            print(f"Invalid datetime format for --until: {args.until}", file=sys.stderr)
            return None, None, True
    return window_start, window_end, False


async def _cmd_policy_simulation_replay(args: argparse.Namespace) -> int:
    """Replay candidate rules against audit history."""
    from agent_app.config.loader import build_app
    from agent_app.runtime.policy_simulation_service import PolicySimulationService

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    # Parse candidate rules
    try:
        rules = _parse_candidate_rules(args.rules_file)
    except Exception as exc:
        print(f"Error parsing rules file: {exc}", file=sys.stderr)
        return 1

    if not rules:
        print("No rules found in the rules file.", file=sys.stderr)
        return 1

    # Parse time window
    window_start, window_end, parse_error = _parse_window(args)
    if parse_error:
        return 1

    # Build simulation service from app components
    audit_logger = getattr(app, "_audit_logger", None)
    runtime_policy_store = getattr(app, "_runtime_policy_store", None)

    service = PolicySimulationService(
        audit_logger=audit_logger,
        runtime_policy_store=runtime_policy_store,
    )

    try:
        report = await service.simulate_from_audit(
            candidate_rules=rules,
            window_start=window_start,
            window_end=window_end,
            limit=args.limit,
        )
    except Exception as exc:
        print(f"Error running simulation: {exc}", file=sys.stderr)
        return 1

    if args.json:
        from agent_app.runtime.policy_compliance_export import simulation_report_to_json
        print(simulation_report_to_json(report))
    else:
        print("Policy Simulation Report")
        print("=" * 40)
        print(f"Simulation ID: {report.simulation_id}")
        print(f"Generated At:  {report.generated_at.isoformat()}")
        print(f"Candidate Rules: {len(report.candidate_rule_ids)}")
        print()
        print("Summary:")
        print(f"  Total:                {report.summary.total}")
        print(f"  Unchanged:            {report.summary.unchanged}")
        print(f"  Would Allow:          {report.summary.would_allow}")
        print(f"  Would Deny:           {report.summary.would_deny}")
        print(f"  Would Require Approval: {report.summary.would_require_approval}")
        print(f"  Would Change:         {report.summary.would_change}")
        print(f"  Errors:               {report.summary.errors}")

        if report.results:
            # Show changed/error results
            changes = [r for r in report.results if r.outcome.value != "unchanged"]
            if changes:
                print()
                print("Changes:")
                for r in changes:
                    print(
                        f"  {r.case_id}: {r.baseline_status} -> "
                        f"{r.candidate_status} ({r.outcome.value})"
                    )
                    if r.reason:
                        print(f"    Reason: {r.reason}")
            else:
                print()
                print("  All decisions unchanged. No regressions detected.")

    return 0


async def _cmd_policy_simulation_export(args: argparse.Namespace) -> int:
    """Export simulation report to a file."""
    from agent_app.config.loader import build_app
    from agent_app.runtime.policy_simulation_service import PolicySimulationService

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    # Parse candidate rules
    try:
        rules = _parse_candidate_rules(args.rules_file)
    except Exception as exc:
        print(f"Error parsing rules file: {exc}", file=sys.stderr)
        return 1

    if not rules:
        print("No rules found in the rules file.", file=sys.stderr)
        return 1

    # Parse time window
    window_start, window_end, parse_error = _parse_window(args)
    if parse_error:
        return 1

    # Build simulation service from app components
    audit_logger = getattr(app, "_audit_logger", None)
    runtime_policy_store = getattr(app, "_runtime_policy_store", None)

    service = PolicySimulationService(
        audit_logger=audit_logger,
        runtime_policy_store=runtime_policy_store,
    )

    try:
        report = await service.simulate_from_audit(
            candidate_rules=rules,
            window_start=window_start,
            window_end=window_end,
            limit=args.limit,
        )
    except Exception as exc:
        print(f"Error running simulation: {exc}", file=sys.stderr)
        return 1

    # Export
    if args.format == "json":
        from agent_app.runtime.policy_compliance_export import simulation_report_to_json
        content = simulation_report_to_json(report)
    elif args.format == "csv":
        import csv
        import io
        from agent_app.runtime.policy_compliance_export import simulation_report_to_csv_rows
        rows = simulation_report_to_csv_rows(report)
        if not rows:
            content = "case_id,baseline_status,candidate_status,outcome,reason,decision_id,errors\n"
        else:
            output = io.StringIO()
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
            content = output.getvalue()
    else:
        print(f"Unsupported format '{args.format}'. Supported: json, csv", file=sys.stderr)
        return 1

    try:
        with open(args.output, 'w') as f:
            f.write(content)
        print(f"Simulation report exported to {args.output}")
    except Exception as exc:
        print(f"Error writing file: {exc}", file=sys.stderr)
        return 1

    return 0


# --- Phase 41: Policy simulation gate CLI commands ---


def _parse_gate_rules(path: str) -> list:
    """Parse gate rules from a YAML file."""
    import yaml
    from agent_app.governance.policy_gate import PolicyGateRule

    with open(path) as f:
        data = yaml.safe_load(f)

    if data is None:
        return []

    if isinstance(data, dict):
        data = data.get("gate_rules", data.get("gates", [data]))

    if not isinstance(data, list):
        data = [data]

    rules = []
    for item in data:
        rules.append(PolicyGateRule(
            name=item.get("name", ""),
            description=item.get("description"),
            max_changed_decisions=item.get("max_changed_decisions"),
            max_changed_ratio=item.get("max_changed_ratio"),
            max_failed_replays=item.get("max_failed_replays"),
            max_new_denies=item.get("max_new_denies"),
            max_new_approvals=item.get("max_new_approvals"),
            fail_on_missing_required_context=item.get("fail_on_missing_required_context", False),
        ))
    return rules


async def _cmd_policy_simulation_gate(args: argparse.Namespace) -> int:
    """Run simulation gate: validate candidate rules, replay, and evaluate gate.

    Exit 0 if gate passes, non-zero if gate fails.
    """
    from agent_app.config.loader import build_app
    from agent_app.runtime.policy_simulation_service import PolicySimulationService

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    # Parse candidate rules
    try:
        candidate_rules = _parse_candidate_rules(args.rules_file)
    except Exception as exc:
        print(f"Error parsing rules file: {exc}", file=sys.stderr)
        return 1

    if not candidate_rules:
        print("No rules found in the rules file.", file=sys.stderr)
        return 1

    # Parse gate rules from file or app config
    gate_rules = None
    if args.gate_rules_file:
        try:
            gate_rules = _parse_gate_rules(args.gate_rules_file)
        except Exception as exc:
            print(f"Error parsing gate rules file: {exc}", file=sys.stderr)
            return 1
    else:
        # Try to get gate rules from app config
        evaluator = getattr(app, "simulation_gate_evaluator", None)
        if evaluator is not None:
            gate_rules = getattr(evaluator, "_rules", None)
            if gate_rules is None:
                inner = getattr(evaluator, "_gate_evaluator", None)
                if inner is not None:
                    gate_rules = getattr(inner, "_rules", None)

    if not gate_rules:
        print("No gate rules available. Provide --gate-rules-file or configure gates in app config.", file=sys.stderr)
        return 1

    # Parse time window
    window_start, window_end, parse_error = _parse_window(args)
    if parse_error:
        return 1

    # Get or build simulation service
    service = getattr(app, "policy_simulation_service", None)
    if service is None:
        audit_logger = getattr(app, "_audit_logger", None)
        runtime_policy_store = getattr(app, "_runtime_policy_store", None)
        service = PolicySimulationService(
            audit_logger=audit_logger,
            runtime_policy_store=runtime_policy_store,
        )

    # Run validate + replay + gate
    try:
        sim_report, val_report, gate_result = await service.validate_and_gate(
            candidate_rules=candidate_rules,
            gate_rules=gate_rules,
            window_start=window_start,
            window_end=window_end,
            limit=args.limit,
        )
    except Exception as exc:
        print(f"Error running simulation gate: {exc}", file=sys.stderr)
        return 1

    # Prepare output data
    output_data = {
        "simulation_report": {
            "simulation_id": sim_report.simulation_id,
            "generated_at": sim_report.generated_at.isoformat(),
            "candidate_rule_ids": sim_report.candidate_rule_ids,
            "summary": {
                "total": sim_report.summary.total,
                "unchanged": sim_report.summary.unchanged,
                "would_allow": sim_report.summary.would_allow,
                "would_deny": sim_report.summary.would_deny,
                "would_require_approval": sim_report.summary.would_require_approval,
                "would_change": sim_report.summary.would_change,
                "errors": sim_report.summary.errors,
            },
        },
        "validation_report": {
            "valid": val_report.valid,
            "issues_count": len(val_report.issues),
        },
        "gate_result": {
            "gate_result_id": gate_result.gate_result_id,
            "status": gate_result.status,
            "passed": gate_result.passed,
            "total_decisions": gate_result.total_decisions,
            "changed_decisions": gate_result.changed_decisions,
            "failed_replays": gate_result.failed_replays,
            "changed_ratio": gate_result.changed_ratio,
            "new_denies": gate_result.new_denies,
            "new_approvals": gate_result.new_approvals,
            "missing_context_count": gate_result.missing_context_count,
            "rule_results": gate_result.rule_results,
        },
    }

    # Output
    if getattr(args, "json", False):
        print(json.dumps(output_data, indent=2, default=str))
    else:
        # Text output
        s = sim_report.summary
        print("Simulation Gate Report")
        print("=" * 40)
        print(f"Simulation ID: {sim_report.simulation_id}")
        print()
        print("Simulation Summary:")
        print(f"  Total:                  {s.total}")
        print(f"  Unchanged:              {s.unchanged}")
        print(f"  Would Allow:            {s.would_allow}")
        print(f"  Would Deny:             {s.would_deny}")
        print(f"  Would Require Approval: {s.would_require_approval}")
        print(f"  Would Change:           {s.would_change}")
        print(f"  Errors:                 {s.errors}")
        print()
        print(f"Validation Issues: {len(val_report.issues)}")
        print()
        print(f"Gate Status: {gate_result.status.upper()}")
        print(f"Gate Passed: {gate_result.passed}")

        if gate_result.rule_results:
            failed_rules = [r for r in gate_result.rule_results if r.get("status") == "failed"]
            if failed_rules:
                print()
                print("Failed Gate Rules:")
                for r in failed_rules:
                    print(f"  {r['rule_name']}: {', '.join(r.get('failures', []))}")

    # Write output file if requested
    if getattr(args, "output", None):
        try:
            with open(args.output, "w") as f:
                json.dump(output_data, f, indent=2, default=str)
            if not getattr(args, "json", False):
                print(f"\nOutput written to {args.output}")
        except Exception as exc:
            print(f"Error writing output file: {exc}", file=sys.stderr)
            return 1

    return 0 if gate_result.passed else 1


async def _cmd_policy_runtime_list(args: argparse.Namespace) -> int:
    """List runtime policy rules."""
    from agent_app.config.loader import build_app
    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    store = _get_runtime_policy_store(app)
    if store is None:
        print("Runtime policy not configured.", file=sys.stderr)
        return 1

    action_type = None
    if args.action_type:
        from agent_app.governance.policy_enforcement import PolicyActionType
        try:
            action_type = PolicyActionType(args.action_type)
        except ValueError:
            print(f"Invalid action type '{args.action_type}'. Valid: {[a.value for a in PolicyActionType]}", file=sys.stderr)
            return 1

    rules = await store.list(action_type=action_type)

    if not rules:
        if args.json:
            print(json.dumps([]))
        else:
            print("No runtime policy rules found.")
        return 0

    if args.json:
        print(json.dumps([_rule_to_dict(r) for r in rules], indent=2, default=str))
    else:
        print(f"{'Rule ID':<15} {'Name':<30} {'Effect':<18} {'Status':<10} {'Tool':<20}")
        print("-" * 95)
        for r in rules:
            print(f"{r.rule_id:<15} {r.name:<30} {r.effect.value:<18} {r.status.value:<10} {r.tool_name or '':<20}")
    return 0


async def _cmd_policy_runtime_create(args: argparse.Namespace) -> int:
    """Create a runtime policy rule."""
    from agent_app.config.loader import build_app
    from agent_app.governance.runtime_policy import RuntimePolicyRule, RuntimePolicyEffect, RuntimePolicyRuleStatus
    from agent_app.governance.policy_enforcement import PolicyActionType
    import uuid as _uuid

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    store = _get_runtime_policy_store(app)
    if store is None:
        print("Runtime policy not configured.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions, roles=getattr(args, 'roles', []))

    try:
        action_type = PolicyActionType(args.action_type)
    except ValueError:
        print(f"Invalid action type '{args.action_type}'. Valid: {[a.value for a in PolicyActionType]}", file=sys.stderr)
        return 1

    try:
        effect = RuntimePolicyEffect(args.effect)
    except ValueError:
        print(f"Invalid effect '{args.effect}'. Valid: {[e.value for e in RuntimePolicyEffect]}", file=sys.stderr)
        return 1

    # Build approval_policy if provided
    approval_policy = None
    if args.approval_policy_type:
        from agent_app.governance.policy_rollout_approval import RolloutApprovalPolicy, RolloutApprovalPolicyType
        approval_policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType(args.approval_policy_type),
            required_approvals=args.required_approvals or 1,
        )

    rule = RuntimePolicyRule(
        rule_id=f"rpr_{_uuid.uuid4().hex[:12]}",
        name=args.name,
        action_type=action_type,
        effect=effect,
        tool_name=args.tool_name,
        risk_level=args.risk_level,
        required_permissions=args.required_permissions or [],
        required_roles=args.required_roles or [],
        approval_policy=approval_policy,
        reason=args.reason,
    )

    try:
        rule = await store.create(rule)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(_rule_to_dict(rule), indent=2, default=str))
    return 0


async def _cmd_policy_runtime_enable(args: argparse.Namespace) -> int:
    """Enable a runtime policy rule."""
    from agent_app.config.loader import build_app
    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    store = _get_runtime_policy_store(app)
    if store is None:
        print("Runtime policy not configured.", file=sys.stderr)
        return 1

    try:
        rule = await store.enable(args.rule_id)
    except KeyError:
        print(f"Rule '{args.rule_id}' not found.", file=sys.stderr)
        return 1

    print(json.dumps(_rule_to_dict(rule), indent=2, default=str))
    return 0


async def _cmd_policy_runtime_disable(args: argparse.Namespace) -> int:
    """Disable a runtime policy rule."""
    from agent_app.config.loader import build_app
    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    store = _get_runtime_policy_store(app)
    if store is None:
        print("Runtime policy not configured.", file=sys.stderr)
        return 1

    try:
        rule = await store.disable(args.rule_id)
    except KeyError:
        print(f"Rule '{args.rule_id}' not found.", file=sys.stderr)
        return 1

    print(json.dumps(_rule_to_dict(rule), indent=2, default=str))
    return 0


async def _cmd_policy_runtime_evaluate(args: argparse.Namespace) -> int:
    """Evaluate a runtime policy enforcement decision."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_enforcement import PolicyActionType
    from agent_app.runtime.runtime_policy_evaluator import RuntimePolicyEvaluationRequest

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_policy_enforcement_service(app)
    if service is None:
        print("Runtime policy not configured.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions or [], roles=getattr(args, 'roles', []))

    try:
        action_type = PolicyActionType(args.action_type)
    except ValueError:
        print(f"Invalid action type '{args.action_type}'.", file=sys.stderr)
        return 1

    request = RuntimePolicyEvaluationRequest(
        action_type=action_type,
        tool_name=args.tool_name,
        risk_level=args.risk_level,
        context=context,
    )

    decision = await service.enforce(request)
    print(json.dumps(_decision_to_dict(decision), indent=2, default=str))
    return 0


# --- Phase 42: Policy promotion gate CLI commands ---


def _get_gate_automation_service(app) -> object | None:
    """Get the release gate automation service from the app."""
    return getattr(app, "_release_gate_automation_service", None)


async def _cmd_policy_promotion_gate_require(args: argparse.Namespace) -> int:
    """Create a gate requirement for a promotion."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_release_gate import ReleaseGateRequirementStatus

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_gate_automation_service(app)
    if service is None:
        print("Release gate automation not configured.", file=sys.stderr)
        return 1

    try:
        req = await service.require_gate_for_promotion(
            promotion_id=args.promotion_id,
            max_age_seconds=args.max_age_seconds,
        )
    except Exception as exc:
        print(f"Error creating gate requirement: {exc}", file=sys.stderr)
        return 1

    status_str = req.status.value if hasattr(req.status, "value") else req.status
    print("Gate requirement created")
    print()
    print(f"Requirement ID:   {req.requirement_id}")
    print(f"Promotion ID:     {req.source_id}")
    print(f"Status:           {status_str}")
    if req.max_age_seconds is not None:
        print(f"Max Age Seconds:  {req.max_age_seconds}")
    return 0


async def _cmd_policy_promotion_gate_run(args: argparse.Namespace) -> int:
    """Run simulation + gate and attach to promotion."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_release_gate import ReleaseGateRequirementStatus

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_gate_automation_service(app)
    if service is None:
        print("Release gate automation not configured.", file=sys.stderr)
        return 1

    # Parse candidate rules
    try:
        candidate_rules = _parse_candidate_rules(args.rules_file)
    except Exception as exc:
        print(f"Error parsing rules file: {exc}", file=sys.stderr)
        return 1

    if not candidate_rules:
        print("No rules found in the rules file.", file=sys.stderr)
        return 1

    # Parse gate rules from file or app config
    gate_rules = None
    if args.gate_rules_file:
        try:
            gate_rules = _parse_gate_rules(args.gate_rules_file)
        except Exception as exc:
            print(f"Error parsing gate rules file: {exc}", file=sys.stderr)
            return 1
    else:
        # Try to get gate rules from app config
        evaluator = getattr(app, "simulation_gate_evaluator", None)
        if evaluator is not None:
            gate_rules = getattr(evaluator, "_rules", None)
            if gate_rules is None:
                inner = getattr(evaluator, "_gate_evaluator", None)
                if inner is not None:
                    gate_rules = getattr(inner, "_rules", None)

    if not gate_rules:
        print("No gate rules available. Provide --gate-rules-file or configure gates in app config.", file=sys.stderr)
        return 1

    # Parse time window
    window_start, window_end, parse_error = _parse_window(args)
    if parse_error:
        return 1

    # Create requirement first if none exists
    try:
        existing = await service.check_requirement("promotion", args.promotion_id)
        if existing.status == ReleaseGateRequirementStatus.NOT_REQUIRED:
            await service.require_gate_for_promotion(
                promotion_id=args.promotion_id,
            )
    except Exception:
        # If check fails, try to create anyway
        try:
            await service.require_gate_for_promotion(
                promotion_id=args.promotion_id,
            )
        except Exception:
            pass

    # Build context
    context = _build_context(
        args.actor_id or "cli_gate_run",
        args.permissions or [],
    )

    # Run simulation + gate + attach
    try:
        req = await service.run_and_attach_simulation_gate_for_promotion(
            promotion_id=args.promotion_id,
            candidate_rules=candidate_rules,
            gate_rules=gate_rules,
            context=context,
            window_start=window_start,
            window_end=window_end,
            limit=args.limit,
        )
    except Exception as exc:
        print(f"Error running simulation gate for promotion: {exc}", file=sys.stderr)
        return 1

    status_str = req.status.value if hasattr(req.status, "value") else req.status
    print("Simulation gate completed for promotion")
    print()
    print(f"Requirement ID:   {req.requirement_id}")
    print(f"Promotion ID:     {req.source_id}")
    print(f"Status:           {status_str}")
    if req.gate_result_id:
        print(f"Gate Result ID:   {req.gate_result_id}")
    if req.simulation_id:
        print(f"Simulation ID:    {req.simulation_id}")

    # Exit 0 on SATISFIED, non-zero otherwise
    if req.status == ReleaseGateRequirementStatus.SATISFIED:
        return 0
    return 1


async def _cmd_policy_promotion_gate_attach(args: argparse.Namespace) -> int:
    """Attach an existing gate result to a promotion."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_release_gate import ReleaseGateRequirementStatus

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_gate_automation_service(app)
    if service is None:
        print("Release gate automation not configured.", file=sys.stderr)
        return 1

    try:
        req = await service.attach_gate_result(
            source_type="promotion",
            source_id=args.promotion_id,
            gate_result_id=args.gate_result_id,
            simulation_id=args.simulation_id,
            actor_id=args.actor_id,
        )
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error attaching gate result: {exc}", file=sys.stderr)
        return 1

    status_str = req.status.value if hasattr(req.status, "value") else req.status
    print("Gate result attached to promotion")
    print()
    print(f"Requirement ID:   {req.requirement_id}")
    print(f"Promotion ID:     {req.source_id}")
    print(f"Status:           {status_str}")
    if req.gate_result_id:
        print(f"Gate Result ID:   {req.gate_result_id}")

    # Exit 0 on SATISFIED, non-zero otherwise
    if req.status == ReleaseGateRequirementStatus.SATISFIED:
        return 0
    return 1


async def _cmd_policy_promotion_gate_status(args: argparse.Namespace) -> int:
    """Show gate requirement status for a promotion."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_gate_automation_service(app)
    if service is None:
        print("Release gate automation not configured.", file=sys.stderr)
        return 1

    try:
        req = await service.check_requirement("promotion", args.promotion_id)
    except Exception as exc:
        print(f"Error checking gate requirement: {exc}", file=sys.stderr)
        return 1

    status_str = req.status.value if hasattr(req.status, "value") else req.status

    if args.json:
        data = {
            "requirement_id": req.requirement_id,
            "promotion_id": req.source_id,
            "status": status_str,
            "gate_result_id": req.gate_result_id,
            "simulation_id": req.simulation_id,
            "max_age_seconds": req.max_age_seconds,
            "satisfied_at": req.satisfied_at.isoformat() if req.satisfied_at else None,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print("Gate requirement status")
        print()
        print(f"Requirement ID:   {req.requirement_id}")
        print(f"Promotion ID:     {req.source_id}")
        print(f"Status:           {status_str}")
        if req.gate_result_id:
            print(f"Gate Result ID:   {req.gate_result_id}")
        if req.simulation_id:
            print(f"Simulation ID:    {req.simulation_id}")
        if req.max_age_seconds is not None:
            print(f"Max Age Seconds:  {req.max_age_seconds}")
        if req.satisfied_at:
            print(f"Satisfied At:     {req.satisfied_at.isoformat()}")

    return 0


# --- Phase 43: Rollout gate CLI commands ---


def _get_rollout_gate_automation_service(app) -> object | None:
    """Get the rollout gate automation service from the app."""
    return getattr(app, "rollout_gate_automation_service", None) or getattr(app, "_rollout_gate_automation_service", None)


async def _cmd_policy_rollout_gate_run(args: argparse.Namespace) -> int:
    """Run simulation gate for a rollout step."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_rollout_gate import RolloutGateExecutionStatus

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_rollout_gate_automation_service(app)
    if service is None:
        print("Rollout gate automation not configured.", file=sys.stderr)
        return 1

    # Find the rollout plan
    rollout_store = getattr(app, "rollout_store", None) or getattr(app, "_rollout_store", None)
    if rollout_store is None:
        print("Rollout store not configured.", file=sys.stderr)
        return 1

    try:
        plan = await rollout_store.get(args.rollout_id)
    except Exception as exc:
        print(f"Error fetching rollout plan: {exc}", file=sys.stderr)
        return 1

    if plan is None:
        print(f"Rollout plan '{args.rollout_id}' not found.", file=sys.stderr)
        return 1

    # Find the step
    step = None
    for s in plan.steps:
        if s.step_id == args.step_id:
            step = s
            break

    if step is None:
        print(f"Step '{args.step_id}' not found in rollout '{args.rollout_id}'.", file=sys.stderr)
        return 1

    context = _build_context(args.actor_id, args.permissions)

    try:
        result = await service.run_step_gate(plan, step, context)
    except Exception as exc:
        print(f"Error running step gate: {exc}", file=sys.stderr)
        return 1

    status_str = result.status.value if hasattr(result.status, "value") else result.status
    print("Rollout step gate execution completed")
    print()
    print(f"Rollout ID:       {result.rollout_id}")
    print(f"Step ID:          {result.step_id}")
    print(f"Gate Mode:        {getattr(step, 'simulation_gate_mode', 'N/A')}")
    print(f"Execution Status: {status_str}")
    if result.requirement_id:
        print(f"Requirement ID:   {result.requirement_id}")
    if result.gate_result_id:
        print(f"Gate Result ID:   {result.gate_result_id}")
    if result.simulation_id:
        print(f"Simulation ID:    {result.simulation_id}")
    if result.action_taken:
        print(f"Action Taken:     {result.action_taken}")
    if result.reason:
        print(f"Reason:           {result.reason}")
    if result.error:
        print(f"Error:            {result.error}")

    # Exit non-zero on FAILED status
    if result.status == RolloutGateExecutionStatus.FAILED:
        return 1
    return 0


async def _cmd_policy_rollout_gate_status(args: argparse.Namespace) -> int:
    """Check gate status for a rollout step."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = _get_rollout_gate_automation_service(app)
    if service is None:
        print("Rollout gate automation not configured.", file=sys.stderr)
        return 1

    # Find the rollout plan
    rollout_store = getattr(app, "rollout_store", None) or getattr(app, "_rollout_store", None)
    if rollout_store is None:
        print("Rollout store not configured.", file=sys.stderr)
        return 1

    try:
        plan = await rollout_store.get(args.rollout_id)
    except Exception as exc:
        print(f"Error fetching rollout plan: {exc}", file=sys.stderr)
        return 1

    if plan is None:
        print(f"Rollout plan '{args.rollout_id}' not found.", file=sys.stderr)
        return 1

    # Find the step
    step = None
    for s in plan.steps:
        if s.step_id == args.step_id:
            step = s
            break

    if step is None:
        print(f"Step '{args.step_id}' not found in rollout '{args.rollout_id}'.", file=sys.stderr)
        return 1

    try:
        result = await service.check_step_gate(plan, step)
    except Exception as exc:
        print(f"Error checking step gate: {exc}", file=sys.stderr)
        return 1

    status_str = result.status.value if hasattr(result.status, "value") else result.status

    if args.json:
        data = {
            "rollout_id": result.rollout_id,
            "step_id": result.step_id,
            "gate_mode": getattr(step, "simulation_gate_mode", None),
            "execution_status": status_str,
            "requirement_id": result.requirement_id,
            "gate_result_id": result.gate_result_id,
            "simulation_id": result.simulation_id,
            "action_taken": result.action_taken,
            "reason": result.reason,
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print("Rollout step gate status")
        print()
        print(f"Rollout ID:       {result.rollout_id}")
        print(f"Step ID:          {result.step_id}")
        print(f"Gate Mode:        {getattr(step, 'simulation_gate_mode', 'N/A')}")
        print(f"Execution Status: {status_str}")
        if result.requirement_id:
            print(f"Requirement ID:   {result.requirement_id}")
        if result.gate_result_id:
            print(f"Gate Result ID:   {result.gate_result_id}")
        if result.simulation_id:
            print(f"Simulation ID:    {result.simulation_id}")
        if result.action_taken:
            print(f"Action Taken:     {result.action_taken}")
        if result.reason:
            print(f"Reason:           {result.reason}")

    return 0


async def _cmd_policy_rollout_gate_attach(args: argparse.Namespace) -> int:
    """Attach an existing gate result to a rollout step."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_release_gate import ReleaseGateRequirementStatus

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    gate_service = _get_rollout_gate_automation_service(app)
    if gate_service is None:
        print("Rollout gate automation not configured.", file=sys.stderr)
        return 1

    release_gate_service = _get_gate_automation_service(app)
    if release_gate_service is None:
        print("Release gate automation not configured.", file=sys.stderr)
        return 1

    # Find the rollout plan
    rollout_store = getattr(app, "rollout_store", None) or getattr(app, "_rollout_store", None)
    if rollout_store is None:
        print("Rollout store not configured.", file=sys.stderr)
        return 1

    try:
        plan = await rollout_store.get(args.rollout_id)
    except Exception as exc:
        print(f"Error fetching rollout plan: {exc}", file=sys.stderr)
        return 1

    if plan is None:
        print(f"Rollout plan '{args.rollout_id}' not found.", file=sys.stderr)
        return 1

    # Find the step
    step = None
    for s in plan.steps:
        if s.step_id == args.step_id:
            step = s
            break

    if step is None:
        print(f"Step '{args.step_id}' not found in rollout '{args.rollout_id}'.", file=sys.stderr)
        return 1

    source_id = f"{args.rollout_id}:{args.step_id}"

    # First ensure a gate requirement exists
    try:
        await release_gate_service.require_gate_for_promotion(
            promotion_id=source_id,
        )
    except Exception:
        pass  # Requirement may already exist

    # Attach the gate result
    try:
        req = await release_gate_service.attach_gate_result(
            source_type="rollout_step",
            source_id=source_id,
            gate_result_id=args.gate_result_id,
            simulation_id=args.simulation_id,
            actor_id=args.actor_id,
        )
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error attaching gate result: {exc}", file=sys.stderr)
        return 1

    status_str = req.status.value if hasattr(req.status, "value") else req.status
    print("Gate result attached to rollout step")
    print()
    print(f"Rollout ID:     {args.rollout_id}")
    print(f"Step ID:        {args.step_id}")
    print(f"Requirement ID: {req.requirement_id}")
    print(f"Status:         {status_str}")
    if req.gate_result_id:
        print(f"Gate Result ID: {req.gate_result_id}")
    if req.simulation_id:
        print(f"Simulation ID:  {req.simulation_id}")

    # Exit 0 on SATISFIED, non-zero otherwise
    if req.status == ReleaseGateRequirementStatus.SATISFIED:
        return 0
    return 1


# -- Phase 44: Notification and expiration commands --


async def _cmd_policy_notification_list(args: argparse.Namespace) -> int:
    """List policy notifications."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_notification import PolicyNotificationStatus

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = getattr(app, "notification_service", None)
    if service is None:
        print("Notification service not configured.", file=sys.stderr)
        return 1

    status_filter = None
    if args.status:
        try:
            status_filter = PolicyNotificationStatus(args.status)
        except ValueError:
            print(f"Invalid status: {args.status}. Valid values: {', '.join(s.value for s in PolicyNotificationStatus)}", file=sys.stderr)
            return 1

    try:
        notifications = await service.list_notifications(
            status=status_filter,
            event_type=args.event_type,
            limit=args.limit,
        )
    except Exception as exc:
        print(f"Error listing notifications: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = []
        for n in notifications:
            data.append({
                "notification_id": n.notification_id,
                "event_type": n.event_type,
                "severity": n.severity.value if hasattr(n.severity, "value") else str(n.severity),
                "title": n.title,
                "status": n.status.value if hasattr(n.status, "value") else str(n.status),
                "created_at": n.created_at.isoformat() if n.created_at else None,
                "sent_at": n.sent_at.isoformat() if n.sent_at else None,
            })
        print(json.dumps(data, indent=2, default=str))
    else:
        if not notifications:
            print("No notifications found.")
            return 0
        print(f"{'ID':<20} {'Event Type':<30} {'Severity':<10} {'Title':<30} {'Status':<12} {'Created At'}")
        print("-" * 120)
        for n in notifications:
            nid = n.notification_id[:20]
            etype = n.event_type[:30]
            sev = (n.severity.value if hasattr(n.severity, "value") else str(n.severity))[:10]
            title = n.title[:30]
            status = (n.status.value if hasattr(n.status, "value") else str(n.status))[:12]
            created = n.created_at.isoformat()[:19] if n.created_at else "?"
            print(f"{nid:<20} {etype:<30} {sev:<10} {title:<30} {status:<12} {created}")

    return 0


async def _cmd_policy_notification_send_pending(args: argparse.Namespace) -> int:
    """Send pending notifications."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = getattr(app, "notification_service", None)
    if service is None:
        print("Notification service not configured.", file=sys.stderr)
        return 1

    try:
        sent = await service.send_pending(limit=args.limit)
    except Exception as exc:
        print(f"Error sending pending notifications: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = []
        for n in sent:
            data.append({
                "notification_id": n.notification_id,
                "status": n.status.value if hasattr(n.status, "value") else str(n.status),
                "sent_at": n.sent_at.isoformat() if n.sent_at else None,
            })
        print(json.dumps({"sent_count": len(sent), "notifications": data}, indent=2, default=str))
    else:
        print(f"Sent {len(sent)} pending notification(s).")
        for n in sent:
            status_str = n.status.value if hasattr(n.status, "value") else str(n.status)
            print(f"  {n.notification_id}: {status_str}")

    return 0


async def _cmd_policy_notification_rule_list(args: argparse.Namespace) -> int:
    """List notification rules."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = getattr(app, "notification_service", None)
    if service is None:
        print("Notification service not configured.", file=sys.stderr)
        return 1

    rule_store = getattr(service, "_rule_store", None)
    if rule_store is None:
        print("Notification rule store not available.", file=sys.stderr)
        return 1

    try:
        rules = await rule_store.list()
    except Exception as exc:
        print(f"Error listing notification rules: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = []
        for r in rules:
            data.append({
                "rule_id": r.rule_id,
                "name": r.name,
                "event_types": list(r.event_types) if hasattr(r.event_types, "__iter__") else [],
                "severity": r.severity.value if hasattr(r.severity, "value") else str(r.severity),
                "channels": list(r.channels) if hasattr(r.channels, "__iter__") else [],
                "status": r.status.value if hasattr(r.status, "value") else str(r.status),
            })
        print(json.dumps(data, indent=2, default=str))
    else:
        if not rules:
            print("No notification rules found.")
            return 0
        print(f"{'ID':<20} {'Name':<25} {'Event Types':<30} {'Severity':<10} {'Channels':<20} {'Status':<10}")
        print("-" * 115)
        for r in rules:
            rid = r.rule_id[:20]
            name = r.name[:25]
            etypes = ", ".join(r.event_types)[:30] if hasattr(r.event_types, "__iter__") else ""
            sev = (r.severity.value if hasattr(r.severity, "value") else str(r.severity))[:10]
            ch = ", ".join(r.channels)[:20] if hasattr(r.channels, "__iter__") else ""
            st = (r.status.value if hasattr(r.status, "value") else str(r.status))[:10]
            print(f"{rid:<20} {name:<25} {etypes:<30} {sev:<10} {ch:<20} {st:<10}")

    return 0


async def _cmd_policy_notification_rule_enable(args: argparse.Namespace) -> int:
    """Enable a notification rule."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = getattr(app, "notification_service", None)
    if service is None:
        print("Notification service not configured.", file=sys.stderr)
        return 1

    rule_store = getattr(service, "_rule_store", None)
    if rule_store is None:
        print("Notification rule store not available.", file=sys.stderr)
        return 1

    try:
        rule = await rule_store.enable(args.rule_id)
    except KeyError:
        print(f"Rule '{args.rule_id}' not found.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error enabling rule: {exc}", file=sys.stderr)
        return 1

    status_str = rule.status.value if hasattr(rule.status, "value") else str(rule.status)
    print(f"Rule '{rule.rule_id}' ({rule.name}) enabled. Status: {status_str}")
    return 0


async def _cmd_policy_notification_rule_disable(args: argparse.Namespace) -> int:
    """Disable a notification rule."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = getattr(app, "notification_service", None)
    if service is None:
        print("Notification service not configured.", file=sys.stderr)
        return 1

    rule_store = getattr(service, "_rule_store", None)
    if rule_store is None:
        print("Notification rule store not available.", file=sys.stderr)
        return 1

    try:
        rule = await rule_store.disable(args.rule_id)
    except KeyError:
        print(f"Rule '{args.rule_id}' not found.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error disabling rule: {exc}", file=sys.stderr)
        return 1

    status_str = rule.status.value if hasattr(rule.status, "value") else str(rule.status)
    print(f"Rule '{rule.rule_id}' ({rule.name}) disabled. Status: {status_str}")
    return 0


async def _cmd_policy_expiration_sweep(args: argparse.Namespace) -> int:
    """Run expiration sweep."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = getattr(app, "expiration_service", None)
    if service is None:
        print("Expiration service not configured.", file=sys.stderr)
        return 1

    try:
        report = await service.sweep()
    except Exception as exc:
        print(f"Error running expiration sweep: {exc}", file=sys.stderr)
        return 1

    if args.json:
        data = {
            "sweep_id": report.sweep_id,
            "started_at": report.started_at.isoformat() if report.started_at else None,
            "completed_at": report.completed_at.isoformat() if report.completed_at else None,
            "total_results": len(report.results),
            "results": [
                {
                    "target_type": r.target_type.value if hasattr(r.target_type, "value") else str(r.target_type),
                    "target_id": r.target_id,
                    "action": r.action.value if hasattr(r.action, "value") else str(r.action),
                    "reason": r.reason,
                }
                for r in report.results
            ],
        }
        print(json.dumps(data, indent=2, default=str))
    else:
        print("Expiration sweep completed")
        print()
        print(f"Sweep ID:     {report.sweep_id}")
        print(f"Started At:   {report.started_at.isoformat() if report.started_at else '?'}")
        print(f"Completed At: {report.completed_at.isoformat() if report.completed_at else '?'}")
        print(f"Total Results: {len(report.results)}")

        expired_count = sum(1 for r in report.results if hasattr(r.action, "value") and r.action.value == "expired")
        error_count = sum(1 for r in report.results if hasattr(r.action, "value") and r.action.value == "error")
        skipped_count = len(report.results) - expired_count - error_count

        print(f"Expired: {expired_count}")
        print(f"Skipped: {skipped_count}")
        print(f"Errors:  {error_count}")

        if report.results:
            print()
            for r in report.results:
                target_type = r.target_type.value if hasattr(r.target_type, "value") else str(r.target_type)
                action = r.action.value if hasattr(r.action, "value") else str(r.action)
                print(f"  [{action}] {target_type}: {r.target_id}")
                if r.reason:
                    print(f"    Reason: {r.reason}")

    return 0


async def _cmd_policy_expiration_run_once(args: argparse.Namespace) -> int:
    """Run expiration sweep once (same as sweep)."""
    return await _cmd_policy_expiration_sweep(args)


# ---------------------------------------------------------------------------
# Phase 45: Rollout history / timeline / analytics CLI commands
# ---------------------------------------------------------------------------


async def _cmd_policy_rollout_history(args: argparse.Namespace) -> int:
    """Show history events for a rollout."""
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_rollout_history import RolloutHistoryEventType

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = getattr(app, "rollout_history_service", None)
    if service is None:
        print("Rollout history not configured.", file=sys.stderr)
        return 1

    event_type_filter = None
    if args.event_type:
        try:
            event_type_filter = RolloutHistoryEventType(args.event_type)
        except ValueError:
            valid = [e.value for e in RolloutHistoryEventType]
            print(f"Invalid event type: {args.event_type}. Valid values: {', '.join(valid)}", file=sys.stderr)
            return 1

    try:
        events = await service.list_history_events(
            rollout_id=args.rollout_id,
            event_type=event_type_filter,
            limit=args.limit,
        )
    except Exception as exc:
        print(f"Error listing rollout history: {exc}", file=sys.stderr)
        return 1

    if not events:
        print(f"No history events found for rollout '{args.rollout_id}'.")
        return 0

    print(f"{'Event ID':<22} {'Type':<35} {'Step ID':<12} {'Actor':<15} {'Message':<30} {'Time'}")
    print("-" * 130)
    for e in events:
        eid = e.history_event_id[:22]
        etype = (e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type))[:35]
        step = (e.step_id or "")[:12]
        actor = (e.actor_id or "")[:15]
        msg = (e.message or "")[:30]
        ts = e.created_at.isoformat()[:19] if e.created_at else "?"
        print(f"{eid:<22} {etype:<35} {step:<12} {actor:<15} {msg:<30} {ts}")

    return 0


async def _cmd_policy_rollout_timeline(args: argparse.Namespace) -> int:
    """Show rollout timeline."""
    from agent_app.config.loader import build_app

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = getattr(app, "rollout_history_service", None)
    if service is None:
        print("Rollout history not configured.", file=sys.stderr)
        return 1

    try:
        timeline = await service.get_timeline(args.rollout_id)
    except Exception as exc:
        print(f"Error fetching rollout timeline: {exc}", file=sys.stderr)
        return 1

    if args.json:
        from agent_app.runtime.policy_compliance_export import rollout_timeline_to_json
        print(rollout_timeline_to_json(timeline))
        return 0

    # Text output
    name = timeline.name or args.rollout_id
    status = timeline.status or "unknown"
    print(f"Rollout: {name} ({args.rollout_id})")
    print(f"Status: {status}")
    if timeline.bundle_id:
        print(f"Bundle: {timeline.bundle_id}")
    if timeline.started_at:
        print(f"Started: {timeline.started_at.isoformat()[:19]}")
    if timeline.completed_at:
        print(f"Completed: {timeline.completed_at.isoformat()[:19]}")
    if timeline.duration_seconds is not None:
        print(f"Duration: {timeline.duration_seconds:.1f}s")

    if timeline.steps:
        print()
        print(f"{'Step':<12} {'Status':<12} {'Gate':<12} {'Approval':<12} {'Duration':<10}")
        print("-" * 60)
        for step in timeline.steps:
            sid = step.step_id[:12]
            st = (step.status or "-")[:12]
            gate = (step.gate_status or "-")[:12]
            approval = (step.approval_status or "-")[:12]
            dur = f"{step.duration_seconds:.1f}s" if step.duration_seconds is not None else "-"
            print(f"{sid:<12} {st:<12} {gate:<12} {approval:<12} {dur:<10}")

    return 0


async def _cmd_policy_rollout_analytics(args: argparse.Namespace) -> int:
    """Show rollout analytics report."""
    from agent_app.config.loader import build_app
    from datetime import datetime

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = getattr(app, "rollout_history_service", None)
    if service is None:
        print("Rollout history not configured.", file=sys.stderr)
        return 1

    window_start = None
    window_end = None
    if args.since:
        try:
            window_start = datetime.fromisoformat(args.since)
        except ValueError:
            print(f"Invalid --since datetime: {args.since}", file=sys.stderr)
            return 1
    if args.until:
        try:
            window_end = datetime.fromisoformat(args.until)
        except ValueError:
            print(f"Invalid --until datetime: {args.until}", file=sys.stderr)
            return 1

    try:
        report = await service.generate_report(
            window_start=window_start,
            window_end=window_end,
        )
    except Exception as exc:
        print(f"Error generating analytics report: {exc}", file=sys.stderr)
        return 1

    # Summary cards
    print("Rollout Analytics Report")
    print(f"  Report ID: {report.report_id}")
    print(f"  Total: {report.total_rollouts}  Completed: {report.completed_rollouts}  "
          f"Failed: {report.failed_rollouts}  Blocked: {report.blocked_rollouts}  "
          f"Cancelled: {report.cancelled_rollouts}")

    # Gate outcomes
    print(f"\nGate Outcomes: {report.gate_outcomes.total} total")
    print(f"  Satisfied: {report.gate_outcomes.satisfied}  Blocked: {report.gate_outcomes.blocked}  "
          f"Failed: {report.gate_outcomes.failed}  Skipped: {report.gate_outcomes.skipped}  "
          f"Expired: {report.gate_outcomes.expired}")

    # Approval outcomes
    avg_lat = f"{report.approval_outcomes.average_latency_seconds:.1f}s" if report.approval_outcomes.average_latency_seconds is not None else "-"
    print(f"\nApproval Outcomes: {report.approval_outcomes.total} total")
    print(f"  Pending: {report.approval_outcomes.pending}  Approved: {report.approval_outcomes.approved}  "
          f"Rejected: {report.approval_outcomes.rejected}  Expired: {report.approval_outcomes.expired}  "
          f"Avg Latency: {avg_lat}")

    # Top blocked steps
    if report.top_blocked_steps:
        print("\nTop Blocked Steps:")
        for item in report.top_blocked_steps:
            print(f"  {item.get('step_id', '?')}: {item.get('count', 0)}")

    # Top failed gates
    if report.top_failed_gates:
        print("\nTop Failed Gates:")
        for item in report.top_failed_gates:
            print(f"  {item.get('step_id', '?')}: {item.get('count', 0)}")

    return 0


async def _cmd_policy_rollout_analytics_export(args: argparse.Namespace) -> int:
    """Export rollout analytics report to file."""
    from agent_app.config.loader import build_app
    from agent_app.runtime.policy_compliance_export import (
        rollout_analytics_report_to_json,
        rollout_analytics_report_to_csv_rows,
    )
    from datetime import datetime

    try:
        app = build_app(args.config)
    except Exception as exc:
        print(f"Error loading config: {exc}", file=sys.stderr)
        return 1

    service = getattr(app, "rollout_history_service", None)
    if service is None:
        print("Rollout history not configured.", file=sys.stderr)
        return 1

    window_start = None
    window_end = None
    if args.since:
        try:
            window_start = datetime.fromisoformat(args.since)
        except ValueError:
            print(f"Invalid --since datetime: {args.since}", file=sys.stderr)
            return 1
    if args.until:
        try:
            window_end = datetime.fromisoformat(args.until)
        except ValueError:
            print(f"Invalid --until datetime: {args.until}", file=sys.stderr)
            return 1

    try:
        report = await service.generate_report(
            window_start=window_start,
            window_end=window_end,
        )
    except Exception as exc:
        print(f"Error generating analytics report: {exc}", file=sys.stderr)
        return 1

    fmt = args.format
    try:
        if fmt == "json":
            content = rollout_analytics_report_to_json(report)
        elif fmt == "csv":
            rows = rollout_analytics_report_to_csv_rows(report)
            if rows:
                import csv as csv_mod
                from io import StringIO
                # Collect all unique fieldnames across all rows
                all_keys: set[str] = set()
                for r in rows:
                    all_keys.update(r.keys())
                fieldnames = sorted(all_keys)
                buf = StringIO()
                writer = csv_mod.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
                content = buf.getvalue()
            else:
                content = ""
        else:
            print(f"Unsupported export format: {fmt}", file=sys.stderr)
            return 1
    except Exception as exc:
        print(f"Error formatting export: {exc}", file=sys.stderr)
        return 1

    try:
        with open(args.output, "w") as f:
            f.write(content)
    except OSError as exc:
        print(f"Error writing to {args.output}: {exc}", file=sys.stderr)
        return 1

    print(f"Analytics report exported to {args.output} ({fmt}).")
    return 0


# ---------------------------------------------------------------------------
# Phase 46: Federation CLI helpers and commands
# ---------------------------------------------------------------------------


def _permissions_from_arg(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _labels_from_args(values: list[str] | None) -> dict[str, str]:
    labels: dict[str, str] = {}
    for item in values or []:
        if "=" not in item:
            raise ValueError(f"Invalid label '{item}'. Expected key=value.")
        key, val = item.split("=", 1)
        labels[key.strip()] = val.strip()
    return labels


def _federation_context(args: argparse.Namespace, actor_attr: str = "actor_id"):
    from agent_app.core.context import RunContext
    actor_id = getattr(args, actor_attr, None) or "cli"
    return RunContext(
        run_id="cli-policy-federation",
        user_id=actor_id,
        tenant_id=getattr(args, "tenant_id", None) or "default",
        permissions=_permissions_from_arg(getattr(args, "permissions", None)),
    )


def _format_federation_plan(plan) -> None:
    print(f"Federation: {plan.federation_id}")
    print(f"Name: {plan.name}")
    print(f"Bundle: {plan.bundle_id}")
    print(f"Strategy: {plan.strategy.value if hasattr(plan.strategy, 'value') else plan.strategy}")
    print(f"Status: {plan.status.value if hasattr(plan.status, 'value') else plan.status}")
    print(f"{'Execution':<18} {'Target':<18} {'Status':<12} {'Rollout':<18}")
    print("-" * 72)
    for execution in plan.executions:
        status = execution.status.value if hasattr(execution.status, "value") else str(execution.status)
        print(f"{execution.execution_id:<18} {execution.target_id:<18} {status:<12} {(execution.rollout_id or '-'):<18}")


def _load_yaml_file(path: str):
    import yaml
    with open(path, "r") as fh:
        return yaml.safe_load(fh) or []


async def _cmd_policy_federation_target_create(args: argparse.Namespace) -> int:
    from agent_app.config.loader import build_app
    try:
        labels = _labels_from_args(getattr(args, "label", None))
        app = build_app(args.config)
        service = getattr(app, "rollout_federation_service", None)
        if service is None:
            print("Rollout federation not configured.", file=sys.stderr)
            return 1
        target = await service.create_target(
            name=args.name,
            environment=args.environment,
            tenant_id=args.tenant_id,
            ring_name=args.ring,
            region=args.region,
            labels=labels,
            actor_id=args.actor_id,
            context=_federation_context(args),
        )
        print(f"Created target {target.target_id}: {target.name} ({target.environment}/{target.ring_name or '-'})")
        return 0
    except Exception as exc:
        print(f"Error creating federation target: {exc}", file=sys.stderr)
        return 1


async def _cmd_policy_federation_target_list(args: argparse.Namespace) -> int:
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_rollout_federation import FederatedTargetStatus
    try:
        app = build_app(args.config)
        store = getattr(app, "federated_rollout_target_store", None)
        if store is None:
            print("Federation target store not configured.", file=sys.stderr)
            return 1
        status_filter = FederatedTargetStatus(args.status) if args.status else None
        targets = await store.list(
            tenant_id=args.tenant_id,
            environment=args.environment,
            ring_name=args.ring,
            status=status_filter,
        )
        if not targets:
            print("No federation targets found.")
            return 0
        print(f"{'Target ID':<18} {'Name':<20} {'Tenant':<12} {'Environment':<12} {'Ring':<10} {'Region':<10} {'Status':<10}")
        print("-" * 98)
        for t in targets:
            tid = t.target_id[:18]
            name = t.name[:20]
            tenant = (t.tenant_id or "")[:12]
            env = t.environment[:12]
            ring = (t.ring_name or "")[:10]
            region = (t.region or "")[:10]
            status = t.status.value[:10]
            print(f"{tid:<18} {name:<20} {tenant:<12} {env:<12} {ring:<10} {region:<10} {status:<10}")
        return 0
    except Exception as exc:
        print(f"Error listing federation targets: {exc}", file=sys.stderr)
        return 1


async def _cmd_policy_federation_target_enable(args: argparse.Namespace) -> int:
    from agent_app.config.loader import build_app
    try:
        app = build_app(args.config)
        store = getattr(app, "federated_rollout_target_store", None)
        if store is None:
            print("Federation target store not configured.", file=sys.stderr)
            return 1
        target = await store.enable(args.target_id)
        print(f"Target {target.target_id} enabled.")
        return 0
    except Exception as exc:
        print(f"Error enabling federation target: {exc}", file=sys.stderr)
        return 1


async def _cmd_policy_federation_target_disable(args: argparse.Namespace) -> int:
    from agent_app.config.loader import build_app
    try:
        app = build_app(args.config)
        store = getattr(app, "federated_rollout_target_store", None)
        if store is None:
            print("Federation target store not configured.", file=sys.stderr)
            return 1
        target = await store.disable(args.target_id)
        print(f"Target {target.target_id} disabled.")
        return 0
    except Exception as exc:
        print(f"Error disabling federation target: {exc}", file=sys.stderr)
        return 1


async def _cmd_policy_federation_plan_create(args: argparse.Namespace) -> int:
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_rollout import RolloutStep
    from agent_app.governance.policy_rollout_federation import FederationExecutionStrategy
    try:
        app = build_app(args.config)
        service = getattr(app, "rollout_federation_service", None)
        if service is None:
            print("Rollout federation not configured.", file=sys.stderr)
            return 1
        target_data = _load_yaml_file(args.targets_file)
        target_ids = target_data if isinstance(target_data, list) else target_data.get("target_ids", [])
        steps_data = _load_yaml_file(args.steps_file)
        steps = [RolloutStep(**item) for item in steps_data]
        strategy = FederationExecutionStrategy(args.strategy)
        plan = await service.create_federated_plan(
            name=args.name,
            bundle_id=args.bundle_id,
            target_ids=target_ids,
            rollout_template_steps=steps,
            created_by=args.actor_id,
            context=_federation_context(args),
            strategy=strategy,
            reason=args.reason,
        )
        _format_federation_plan(plan)
        return 0
    except Exception as exc:
        print(f"Error creating federated plan: {exc}", file=sys.stderr)
        return 1


async def _cmd_policy_federation_plan_list(args: argparse.Namespace) -> int:
    from agent_app.config.loader import build_app
    try:
        app = build_app(args.config)
        store = getattr(app, "federated_rollout_plan_store", None)
        if store is None:
            print("Federation plan store not configured.", file=sys.stderr)
            return 1
        plans = await store.list()
        if not plans:
            print("No federated plans found.")
            return 0
        print(f"{'Federation ID':<18} {'Name':<20} {'Status':<12} {'Strategy':<10} {'Bundle':<12} {'Targets':<8}")
        print("-" * 86)
        for p in plans:
            pid = p.federation_id[:18]
            name = p.name[:20]
            status = p.status.value[:12]
            strategy = p.strategy.value[:10]
            bundle = p.bundle_id[:12]
            targets = str(len(p.target_ids))
            print(f"{pid:<18} {name:<20} {status:<12} {strategy:<10} {bundle:<12} {targets:<8}")
        return 0
    except Exception as exc:
        print(f"Error listing federated plans: {exc}", file=sys.stderr)
        return 1


async def _cmd_policy_federation_plan_show(args: argparse.Namespace) -> int:
    from agent_app.config.loader import build_app
    try:
        app = build_app(args.config)
        store = getattr(app, "federated_rollout_plan_store", None)
        if store is None:
            print("Federation plan store not configured.", file=sys.stderr)
            return 1
        plan = await store.get(args.federation_id)
        if plan is None:
            print(f"Federated plan '{args.federation_id}' not found.", file=sys.stderr)
            return 1
        _format_federation_plan(plan)
        return 0
    except Exception as exc:
        print(f"Error showing federated plan: {exc}", file=sys.stderr)
        return 1


async def _cmd_policy_federation_plan_start(args: argparse.Namespace) -> int:
    from agent_app.config.loader import build_app
    try:
        app = build_app(args.config)
        service = getattr(app, "rollout_federation_service", None)
        if service is None:
            print("Rollout federation not configured.", file=sys.stderr)
            return 1
        plan = await service.start_federated_plan(
            args.federation_id,
            actor_id=args.actor_id,
            context=_federation_context(args),
        )
        _format_federation_plan(plan)
        return 0
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error starting federated plan: {exc}", file=sys.stderr)
        return 1


async def _cmd_policy_federation_plan_run_next(args: argparse.Namespace) -> int:
    from agent_app.config.loader import build_app
    try:
        app = build_app(args.config)
        service = getattr(app, "rollout_federation_service", None)
        if service is None:
            print("Rollout federation not configured.", file=sys.stderr)
            return 1
        plan = await service.run_next_target(
            args.federation_id,
            actor_id=args.actor_id,
            context=_federation_context(args),
        )
        _format_federation_plan(plan)
        return 0
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error running next target: {exc}", file=sys.stderr)
        return 1


async def _cmd_policy_federation_plan_run_all(args: argparse.Namespace) -> int:
    from agent_app.config.loader import build_app
    try:
        app = build_app(args.config)
        service = getattr(app, "rollout_federation_service", None)
        if service is None:
            print("Rollout federation not configured.", file=sys.stderr)
            return 1
        plan = await service.run_all_available(
            args.federation_id,
            actor_id=args.actor_id,
            context=_federation_context(args),
        )
        _format_federation_plan(plan)
        return 0
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error running all targets: {exc}", file=sys.stderr)
        return 1


async def _cmd_policy_federation_plan_cancel(args: argparse.Namespace) -> int:
    from agent_app.config.loader import build_app
    try:
        app = build_app(args.config)
        service = getattr(app, "rollout_federation_service", None)
        if service is None:
            print("Rollout federation not configured.", file=sys.stderr)
            return 1
        plan = await service.cancel_federated_plan(
            args.federation_id,
            actor_id=args.actor_id,
            context=_federation_context(args),
            reason=args.reason,
        )
        _format_federation_plan(plan)
        return 0
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error cancelling federated plan: {exc}", file=sys.stderr)
        return 1


async def _cmd_policy_federation_plan_conflicts(args: argparse.Namespace) -> int:
    from agent_app.config.loader import build_app
    from agent_app.governance.policy_rollout_federation import RolloutConflictSeverity
    try:
        app = build_app(args.config)
        service = getattr(app, "rollout_federation_service", None)
        if service is None:
            print("Rollout federation not configured.", file=sys.stderr)
            return 1
        conflicts = await service.detect_conflicts(args.federation_id)
        if not conflicts:
            print("No conflicts found.")
            return 0
        print(f"{'Conflict ID':<30} {'Type':<28} {'Severity':<10} {'Target':<18} {'Message':<40}")
        print("-" * 130)
        has_error = False
        for c in conflicts:
            cid = c.conflict_id[:30]
            ctype = c.conflict_type.value[:28]
            severity = c.severity.value.upper()[:10]
            target = (c.target_id or "")[:18]
            msg = c.message[:40]
            print(f"{cid:<30} {ctype:<28} {severity:<10} {target:<18} {msg:<40}")
            if c.severity == RolloutConflictSeverity.ERROR:
                has_error = True
        return 1 if has_error else 0
    except Exception as exc:
        print(f"Error detecting conflicts: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
