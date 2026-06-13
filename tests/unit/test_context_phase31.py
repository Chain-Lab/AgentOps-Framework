from agent_app.core.context import RunContext


def test_policy_environment_defaults_none():
    ctx = RunContext(run_id="r1", user_id="u", tenant_id="t")
    assert ctx.policy_environment is None


def test_policy_environment_can_be_set():
    ctx = RunContext(run_id="r1", user_id="u", tenant_id="t", policy_environment="prod")
    assert ctx.policy_environment == "prod"


def test_policy_ring_defaults_none():
    from agent_app.core.context import RunContext
    ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
    assert ctx.policy_ring is None


def test_policy_ring_can_be_set():
    from agent_app.core.context import RunContext
    ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", policy_ring="canary")
    assert ctx.policy_ring == "canary"
