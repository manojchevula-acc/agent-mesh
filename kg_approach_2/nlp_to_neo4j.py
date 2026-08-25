from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
USERNAME = "neo4j"
PASSWORD = "Pnaik@96"
DATABASE = "neo4j"

driver = GraphDatabase.driver(
    URI,
    auth=(USERNAME, PASSWORD)
)

schema = """
Neo4j Graph Schema:

Nodes:
Customer(customer_id)
Deal(deal_id, customer_id, product_id)
Product(product_id, product_name, product_type)
PolicyException(deal_id, rule_id, policy_id)
BusinessRule(rule_id, rule_version)

Relationships:
Customer -[:HAS_DEAL]-> Deal
Deal -[:FOR_PRODUCT]-> Product
Deal -[:HAS_POLICY_EXCEPTION]-> PolicyException
PolicyException -[:CAUSED_BY]-> BusinessRule
"""

question = "Which deals are associated with Customer CUST001?"

print(schema)
print(question)

driver.close()