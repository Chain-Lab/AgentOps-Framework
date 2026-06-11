from agent_app.core.context import RunContext


def test_policy_environment_defaults_none():
    ctx = RunContext(run_id="r1", user_id="u", tenant_id="t")
    assert ctx.policy_environment is None


def test_policy_environment_can_be_set():
    ctx = RunContext(run_id="r1", user_id="u", tenant_id="t", policy_environment="prod")
    assert ctx.policy_environment == "prod"
