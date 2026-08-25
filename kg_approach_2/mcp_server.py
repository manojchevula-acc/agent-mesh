from fastmcp import FastMCP

mcp = FastMCP("Neo4J Knowledge Graph")

from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
USERNAME = "neo4j"
PASSWORD = "Pnaik@96"
DATABASE = "neo4j"

driver = GraphDatabase.driver(
    URI,
    auth=(USERNAME, PASSWORD)
)

@mcp.tool
def get_deals_for_customer(customer_id: str):
    """Get all deals associated with a given customer ID."""
    query = """
    MATCH (c:Customer {customer_id: $customer_id})
          -[:HAS_DEAL]->(d:Deal)
    RETURN d.deal_id AS deal_id
    ORDER BY d.deal_id
    """

    with driver.session(database=DATABASE) as session:
        result = session.run(query, customer_id=customer_id)
        return [record["deal_id"] for record in result]

if __name__ == "__main__":
    mcp.run()