from neo4j import GraphDatabase

URI = "neo4j://localhost:7687"
USERNAME = "neo4j"
PASSWORD = "Pnaik@96"
DATABASE = "neo4j"

driver = GraphDatabase.driver(
    URI,
    auth=(USERNAME, PASSWORD)
)

try:
    driver.verify_connectivity()
    print("Connected to Neo4j successfully!")

    result = driver.execute_query(
        "RETURN 'Hello from VS Code!' AS message",
        database_=DATABASE
    )

    print(result.records[0]["message"])

finally:
    driver.close()
