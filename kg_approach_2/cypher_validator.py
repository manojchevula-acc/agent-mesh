import re


ALLOWED_LABELS = {
    "Customer",
    "Deal",
    "Product",
    "PolicyException",
    "BusinessRule"
}


ALLOWED_RELATIONSHIPS = {
    "HAS_DEAL",
    "FOR_PRODUCT",
    "HAS_POLICY_EXCEPTION",
    "CAUSED_BY"
}


ALLOWED_PROPERTIES = {
    "customer_id",
    "customer_name",
    "customer_segment",
    "industry",
    "region",
    "deal_outcome",
    "deal_id",
    "product_id",
    "product_type",
    "currency",
    "product_name",
    "pricing_method",
    "rule_id",
    "rule_version",
    "expected_margin_pct",
    "severity",
    "reason",
    "policy_id",
    "margin_shortfall",
    "policy_min_margin_pct",
    "description",
    "result"
}


FORBIDDEN_KEYWORDS = {
    "CREATE",
    "DELETE",
    "SET",
    "REMOVE",
    "DROP",
    "MERGE",
    "DETACH",
    "ALTER",
    "GRANT",
    "DENY",
    "REVOKE"
}


def validate_cypher(cypher):

    if not cypher:
        return False

    query = cypher.strip()

    # Reject Markdown code fences
    if "```" in query:
        return False

    upper_query = query.upper()

    # Reject write/admin operations
    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", upper_query):
            return False

    # Query must contain MATCH
    if not re.search(r"\bMATCH\b", upper_query):
        return False

    # Query must contain RETURN
    if not re.search(r"\bRETURN\b", upper_query):
        return False

    # Check node labels
    node_labels = re.findall(
        r"\([A-Za-z_][A-Za-z0-9_]*\s*:\s*([A-Za-z_][A-Za-z0-9_]*)",
        query
    )

    for label in node_labels:
        if label not in ALLOWED_LABELS:
            return False

    # Check anonymous node labels
    anonymous_labels = re.findall(
        r"\(\s*:\s*([A-Za-z_][A-Za-z0-9_]*)",
        query
    )

    for label in anonymous_labels:
        if label not in ALLOWED_LABELS:
            return False

    # Check relationships
    relationships = re.findall(
        r"\[\s*:\s*([A-Za-z_][A-Za-z0-9_]*)",
        query
    )

    for relationship in relationships:
        if relationship not in ALLOWED_RELATIONSHIPS:
            return False

        # Check properties such as:
    # c.customer_id
    # p.product_name
    dotted_properties = re.findall(
        r"\.\s*([A-Za-z_][A-Za-z0-9_]*)",
        query
    )

    for prop in dotted_properties:
        if prop not in ALLOWED_PROPERTIES:
            return False

    # Check properties inside map literals only.
    # Do not treat Cypher subqueries such as:
    # WHERE NOT EXISTS { MATCH ... }
    # as property maps.

    map_blocks = re.findall(
        r"\{([^{}]*)\}",
        query,
        flags=re.DOTALL
    )

    for block in map_blocks:

        # Skip Cypher subqueries
        if re.search(r"\bMATCH\b|\bRETURN\b|\bWHERE\b", block, re.IGNORECASE):
            continue

        properties = re.findall(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\s*:",
            block
        )

        for prop in properties:
            if prop not in ALLOWED_PROPERTIES:
                return False

    return True