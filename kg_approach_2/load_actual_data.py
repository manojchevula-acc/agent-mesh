import csv
from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
USERNAME = "neo4j"
PASSWORD = "Pnaik@96"
DATABASE = "neo4j"

driver = GraphDatabase.driver(
    URI,
    auth=(USERNAME, PASSWORD)
)


def load_data(tx):

    # -------------------------
    # 1. LOAD CUSTOMERS
    # -------------------------
    with open("kg_customers_curated.csv",
              encoding="utf-8-sig") as f:

        for row in csv.DictReader(f):
            tx.run("""
                MERGE (c:Customer {
                    customer_id: $customer_id
                })
                SET c.customer_name = $customer_name,
                    c.customer_segment = $customer_segment,
                    c.industry = $industry,
                    c.region = $region
            """, **row)


    # -------------------------
    # 2. LOAD DEALS
    # -------------------------
    with open("kg_deals_curated.csv",
              encoding="utf-8-sig") as f:

        for row in csv.DictReader(f):
            tx.run("""
                MERGE (d:Deal {
                    deal_id: $deal_id
                })
                SET d.customer_id = $customer_id,
                    d.product_id = $product_id,
                    d.product_type = $product_type,
                    d.currency = $currency,
                    d.deal_outcome = $deal_outcome
            """, **row)


    # -------------------------
    # 3. LOAD PRODUCTS
    # -------------------------
    with open("kg_products_curated.csv",
              encoding="utf-8-sig") as f:

        for row in csv.DictReader(f):
            tx.run("""
                MERGE (p:Product {
                    product_id: $product_id
                })
                SET p.product_name = $product_name,
                    p.product_type = $product_type,
                    p.pricing_method = $pricing_method,
                    p.currency = $currency
            """, **row)


    # -------------------------
    # 4. LOAD POLICY EXCEPTIONS
    # -------------------------
    with open("kg_policy_exceptions_curated.csv",
              encoding="utf-8-sig") as f:

        for row in csv.DictReader(f):
            tx.run("""
                MERGE (e:PolicyException {
                    deal_id: $deal_id,
                    rule_id: $rule_id,
                    rule_version: $rule_version
                })
                SET e.policy_id = $policy_id,
                    e.expected_margin_pct = $expected_margin_pct,
                    e.policy_min_margin_pct = $policy_min_margin_pct,
                    e.margin_shortfall = $margin_shortfall,
                    e.severity = $severity,
                    e.reason = $reason
            """, **row)


    # -------------------------
    # 5. LOAD BUSINESS RULES
    # -------------------------
    with open("kg_business_rules_curated.csv",
              encoding="utf-8-sig") as f:

        for row in csv.DictReader(f):
            tx.run("""
                MERGE (r:BusinessRule {
                    rule_id: $rule_id,
                    rule_version: $rule_version
                })
                SET r.description = $description,
                    r.result = $result
            """, **row)


    # -------------------------
    # 6. CUSTOMER → DEAL
    # -------------------------
    tx.run("""
        MATCH (c:Customer), (d:Deal)
        WHERE c.customer_id = d.customer_id
        MERGE (c)-[:HAS_DEAL]->(d)
    """)


    # -------------------------
    # 7. DEAL → PRODUCT
    # -------------------------
    tx.run("""
        MATCH (d:Deal), (p:Product)
        WHERE d.product_id = p.product_id
        MERGE (d)-[:FOR_PRODUCT]->(p)
    """)


    # -------------------------
    # 8. DEAL → POLICY EXCEPTION
    # -------------------------
    tx.run("""
        MATCH (d:Deal), (e:PolicyException)
        WHERE d.deal_id = e.deal_id
        MERGE (d)-[:HAS_POLICY_EXCEPTION]->(e)
    """)
    #-------------------------
    #9. Policy Exception -> Business Rule
    #-------------------------
    

    # -------------------------
    # 9. POLICY EXCEPTION → BUSINESS RULE
    # -------------------------
    tx.run("""
        MATCH (e:PolicyException), (r:BusinessRule)
        WHERE e.rule_id = r.rule_id
          AND e.rule_version = r.rule_version
        MERGE (e)-[:CAUSED_BY]->(r)
    """)


with driver.session(database=DATABASE) as session:
    session.execute_write(load_data)

driver.close()

print("Customer, Deal, Product, PolicyException and BusinessRule data loaded successfully.")