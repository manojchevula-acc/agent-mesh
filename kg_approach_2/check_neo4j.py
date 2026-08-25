from neo4j import GraphDatabase

URI = "bolt://localhost:7687"
USERNAME = "neo4j"
PASSWORD = "Pnaik@96"
DATABASE = "neo4j"

driver = GraphDatabase.driver(
    URI,
    auth=(USERNAME, PASSWORD)
)

try:
    driver.verify_connectivity()

    result = driver.execute_query(
        """
        MATCH (n)
        RETURN labels(n) AS labels, count(n) AS count
        ORDER BY count DESC
        """,
        database_=DATABASE
    )

    print("\nNodes currently in Neo4j:\n")

    for record in result.records:
        print(f"{record['labels']} -> {record['count']}")

finally:
    driver.close()