SCHEMA = """

Nodes:

Customer(
    customer_id,
    customer_name,
    customer_segment,
    industry,
    region
)

Deal(
    deal_id,
    customer_id,
    product_id,
    deal_outcome,
    currency,
    pricing_method,
    expected_margin_pct
)

Product(
    product_id,
    product_name,
    product_type
)

PolicyException(

    deal_id,
    rule_id,
    policy_id,
    severity,              # exception severity: CRITICAL, HIGH, STANDARD, etc.
    reason,                # reason for the policy exception
    margin_shortfall,      # calculated margin shortfall
    policy_min_margin_pct, # minimum policy margin percentage
    result                 # policy evaluation result/status
)

BusinessRule(
    rule_id,
    rule_version,
    description
)


Relationships:

Customer -[:HAS_DEAL]-> Deal

Deal -[:FOR_PRODUCT]-> Product

Deal -[:HAS_POLICY_EXCEPTION]-> PolicyException

PolicyException -[:CAUSED_BY]-> BusinessRule

"""