"""Customer support example — tools for the support agent."""

from agent_app import tool
import re


@tool(
    name="order.query",
    description="Query order details by order ID. Returns status, amount, and items.",
    risk_level="low",
    permissions=["order:read"],
)
async def query_order(**kwargs) -> dict:
    """Query an order by ID.

    Args:
        order_id: The order identifier (e.g. "123", "ORD-456").
    """
    order_id = kwargs.get("order_id", kwargs.get("id", "unknown"))
    # Fallback: extract order ID from input text if not provided as kwarg
    if order_id == "unknown":
        input_text = kwargs.get("input", "")
        if isinstance(input_text, str):
            m = re.search(r'order\s+(\w+)', input_text, re.IGNORECASE)
            if m:
                order_id = m.group(1)
    orders = {
        "123": {"order_id": "123", "status": "paid", "amount": 199.0, "items": ["Widget A"], "used_coupon": False},
        "456": {"order_id": "456", "status": "shipped", "amount": 59.9, "items": ["Gadget B"], "used_coupon": True},
    }
    order = orders.get(str(order_id))
    if order is None:
        return {"order_id": order_id, "status": "not_found", "message": "Order not found."}
    return order


@tool(
    name="refund.request",
    description="Create a refund request for an order. Requires manager approval.",
    risk_level="high",
    requires_approval=True,
    permissions=["refund:create"],
)
async def request_refund(**kwargs) -> dict:
    """Create a refund request (simulated).

    Args:
        order_id: The order to refund.
        amount: Refund amount.
        reason: Reason for refund.
    """
    order_id = kwargs.get("order_id", kwargs.get("id", "unknown"))
    amount = kwargs.get("amount", 0.0)
    reason = kwargs.get("reason", "customer request")
    return {
        "refund_id": f"rf_{order_id}",
        "order_id": order_id,
        "amount": amount,
        "reason": reason,
        "status": "pending_approval",
    }


@tool(
    name="customer.lookup",
    description="Look up customer details by customer ID or email.",
    risk_level="low",
    permissions=["customer:read"],
)
async def lookup_customer(**kwargs) -> dict:
    """Look up customer information (simulated).

    Args:
        customer_id: The customer identifier.
        email: Customer email address.
    """
    customer_id = kwargs.get("customer_id", kwargs.get("email", "unknown"))
    customers = {
        "cust_001": {"customer_id": "cust_001", "name": "Alice", "tier": "gold"},
        "cust_002": {"customer_id": "cust_002", "name": "Bob", "tier": "silver"},
    }
    customer = customers.get(str(customer_id))
    if customer is None:
        return {"customer_id": customer_id, "name": "Unknown", "tier": "standard"}
    return customer


# ---------------------------------------------------------------------------
# Phase 13.4: Workflow functions for FUNCTION DAG nodes
# ---------------------------------------------------------------------------

from agent_app.workflows import workflow_function  # noqa: E402


@workflow_function(
    name="order.extract_order_id",
    description="Extract order ID from free-text input",
)
def extract_order_id(text: str) -> dict:
    """Extract an order ID from free-text input using regex.

    Args:
        text: Free-text input that may contain an order ID.

    Returns:
        Dict with extracted order_id and the original text.
    """
    import re
    m = re.search(r'order\s+(\w+)', text, re.IGNORECASE)
    order_id = m.group(1) if m else "unknown"
    return {"order_id": order_id, "text": text}


@workflow_function(
    name="refund.calculate_amount",
    description="Calculate refundable amount for an order based on coupon usage.",
    permissions=["refund:calculate"],
    risk_level="medium",
)
def calculate_refund_amount(order_total: float, used_coupon: bool = False) -> dict:
    """Calculate the refundable amount based on order total and coupon usage.

    Args:
        order_total: Total order amount.
        used_coupon: Whether a coupon was applied.

    Returns:
        Dict with calculated refund amount and details.
    """
    discount = 0.1 if used_coupon else 0.0
    refund_amount = round(order_total * (1 - discount), 2)
    return {
        "order_total": order_total,
        "used_coupon": used_coupon,
        "discount": discount,
        "refund_amount": refund_amount,
    }


# ---------------------------------------------------------------------------
# Phase 13.9: Compensation handlers
# ---------------------------------------------------------------------------

@workflow_function(
    name="order.revert_extraction",
    description="Revert order ID extraction (compensation handler)",
)
def revert_extraction(**kwargs) -> dict:
    """Compensation handler: marks extraction as reverted."""
    return {"status": "reverted", "extraction_cancelled": True}


@workflow_function(
    name="refund.revert_calculation",
    description="Revert refund amount calculation (compensation handler)",
)
def revert_calculation(**kwargs) -> dict:
    """Compensation handler: marks refund calculation as reverted."""
    return {"status": "reverted", "refund_cancelled": True}
