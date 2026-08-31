"""A small MetadataGraph mirroring the real schema's awkward shapes.

Chosen so the tests exercise what actually breaks in production rather than a toy graph:

  * customer_master is the HUB (as it is in reality — 11 of the 15 real edges touch it).
  * customer_master <-> pricing_policy is COMPOSITE (customer_segment AND risk_category).
  * margin_analysis and pricing_recommendation_view are VIEWS carrying overlapping columns,
    so a naive BFS between them produces the view -> customer_master -> view path that
    validator check #9 rejects.
  * "policy minimum" DEFINES three differently-named columns on three objects — the exact
    disambiguation case from the design doc's worked example.

Every table/column name used here also exists in the real schema.yaml, so SQL written against
this fixture still passes validator checks #1-#9 (which read the real semantic layer) and the
KG checks can be tested in isolation.
"""

from sql_agent.kg.schema import (
    MANY_TO_ONE,
    ColumnNode,
    ForeignKeyEdge,
    MetadataGraph,
    TableNode,
    TermNode,
)


def _col(table, name, **kw):
    return ColumnNode(table=table, name=name, **kw)


def build_graph() -> MetadataGraph:
    tables = {
        "customer_master": TableNode(name="customer_master", db_schema="fab_curated",
                                     primary_key="customer_id", grain="one row per customer"),
        "historical_deals": TableNode(name="historical_deals", db_schema="fab_curated",
                                      primary_key="deal_id", grain="one row per deal"),
        "product_master": TableNode(name="product_master", db_schema="fab_curated",
                                    primary_key="product_id", grain="one row per product"),
        "pricing_policy": TableNode(name="pricing_policy", db_schema="fab_curated",
                                    primary_key="policy_id",
                                    grain="one row per segment x product x risk"),
        "margin_analysis": TableNode(name="margin_analysis", db_schema="fab_semantic",
                                     is_view=True, primary_key="deal_id",
                                     grain="one row per deal"),
        "pricing_recommendation_view": TableNode(
            name="pricing_recommendation_view", db_schema="fab_semantic", is_view=True,
            primary_key="deal_id", grain="one row per deal"),
    }

    columns = {}
    for col in [
        _col("customer_master", "customer_id", logical_type="str", is_primary_key=True,
             is_unique=True),
        _col("customer_master", "customer_segment", logical_type="enum",
             enum_values=("Corporate", "SME")),
        _col("customer_master", "risk_category", logical_type="enum",
             enum_values=("Low", "Medium", "High")),
        _col("customer_master", "existing_exposure_aed", logical_type="int", unit="AED"),
        _col("customer_master", "debt_to_equity_ratio", logical_type="float"),
        _col("historical_deals", "deal_id", logical_type="str", is_primary_key=True,
             is_unique=True),
        _col("historical_deals", "customer_id", logical_type="str"),
        _col("historical_deals", "product_id", logical_type="str"),
        _col("historical_deals", "expected_margin_pct", logical_type="float", unit="pct"),
        _col("product_master", "product_id", logical_type="str", is_primary_key=True,
             is_unique=True),
        _col("product_master", "product_type", logical_type="enum",
             enum_values=("Loan", "Trade Finance", "Treasury", "Deposit")),
        _col("pricing_policy", "policy_id", logical_type="str", is_primary_key=True,
             is_unique=True),
        _col("pricing_policy", "customer_segment", logical_type="enum",
             enum_values=("Corporate", "SME")),
        _col("pricing_policy", "risk_category", logical_type="enum",
             enum_values=("Low", "Medium", "High")),
        _col("pricing_policy", "min_expected_margin_pct", logical_type="float", unit="pct"),
        _col("margin_analysis", "deal_id", logical_type="str", is_primary_key=True,
             is_unique=True),
        _col("margin_analysis", "customer_id", logical_type="str"),
        _col("margin_analysis", "expected_margin_pct", logical_type="float", unit="pct"),
        _col("margin_analysis", "min_expected_margin_pct", logical_type="float", unit="pct"),
        _col("pricing_recommendation_view", "deal_id", logical_type="str",
             is_primary_key=True, is_unique=True),
        _col("pricing_recommendation_view", "customer_id", logical_type="str"),
        _col("pricing_recommendation_view", "policy_min_expected_margin_pct",
             logical_type="float", unit="pct"),
    ]:
        columns[col.key] = col

    edges = [
        # customer_master is UNIQUE on customer_id, so deals -> customer is many-to-one and
        # the reverse (customer -> deals) is one-to-many: the fan-out direction.
        ForeignKeyEdge("historical_deals", "customer_master",
                       (("customer_id", "customer_id"),), cardinality=MANY_TO_ONE),
        ForeignKeyEdge("historical_deals", "product_master",
                       (("product_id", "product_id"),), cardinality=MANY_TO_ONE),
        # COMPOSITE: both predicates are required to keep this join from fanning out.
        ForeignKeyEdge("customer_master", "pricing_policy",
                       (("customer_segment", "customer_segment"),
                        ("risk_category", "risk_category"))),
        ForeignKeyEdge("margin_analysis", "customer_master",
                       (("customer_id", "customer_id"),), cardinality=MANY_TO_ONE),
        ForeignKeyEdge("pricing_recommendation_view", "customer_master",
                       (("customer_id", "customer_id"),), cardinality=MANY_TO_ONE),
    ]

    terms = {
        "policy minimum": TermNode(name="policy minimum", category="pricing",
                                   definition="The floor margin a deal may be priced at."),
        "gearing": TermNode(name="gearing", category="risk",
                            definition="Ratio of a customer's total debt to equity."),
    }
    defines = [
        ("policy minimum", "pricing_policy.min_expected_margin_pct"),
        ("policy minimum", "margin_analysis.min_expected_margin_pct"),
        ("policy minimum", "pricing_recommendation_view.policy_min_expected_margin_pct"),
        ("gearing", "customer_master.debt_to_equity_ratio"),
    ]

    graph = MetadataGraph(tables=tables, columns=columns, terms=terms,
                          foreign_keys=edges, defines=defines)
    graph.fingerprint = graph.compute_fingerprint()
    return graph


def build_client():
    """A loaded MemoryGraph over the fixture — bypasses get_kg_client (which reads the
    on-disk artifact), so tests never depend on a build having been run."""
    from sql_agent.kg.client import MemoryGraph

    client = MemoryGraph()
    client.load(build_graph())
    return client
