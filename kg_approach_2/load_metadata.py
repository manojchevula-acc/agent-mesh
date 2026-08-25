import csv
from pathlib import Path
from neo4j import GraphDatabase

URI = "neo4j://localhost:7687"
USERNAME = "neo4j"
PASSWORD = "Pnaik@96"
DATABASE = "neo4j"

FOLDER = Path(__file__).parent


def load_entities(driver):

    file = FOLDER / "metadata_entities.csv"

    with open(file, "r", encoding="utf-8-sig", newline="") as f:

        reader = csv.DictReader(f)

        rows = list(reader)

    query = """
    UNWIND $rows AS row

    MERGE (e:MetadataEntity {
        name: row.entity_name
    })

    SET e.description = row.description,
        e.source_file = row.source_file,
        e.primary_key = row.primary_key

    RETURN count(e) AS count
    """

    result = driver.execute_query(
        query,
        rows=rows,
        database_=DATABASE
    )

    print(f"Metadata entities loaded: {result.records[0]['count']}")


def load_relationships(driver):

    relationships = [
        ("Customer", "HAS_DEAL", "Deal", "customer_id"),
        ("Deal", "FOR_PRODUCT", "Product", "product_id"),
        ("Deal", "HAS_OUTCOME", "DealOutcome", "deal_id"),
        ("Deal", "HAS_RECOMMENDATION", "Recommendation", "deal_id"),
        ("Deal", "HAS_POLICY_EXCEPTION", "PolicyException", "deal_id"),
        ("PolicyException", "TRIGGERED_BY", "BusinessRule", "rule_id"),
        ("PolicyException", "GOVERNED_BY", "PricingPolicy", "policy_id"),
        ("PolicyException", "HAS_SEVERITY", "ApprovalLevel", "severity=level_code"),
    ]

    for from_entity, relationship, to_entity, join_key in relationships:

        query = f"""
        MATCH (a:MetadataEntity {{name: $from_entity}})
        MATCH (b:MetadataEntity {{name: $to_entity}})
        MERGE (a)-[r:{relationship}]->(b)
        SET r.join_key = $join_key
        RETURN count(r) AS count
        """

        driver.execute_query(
            query,
            from_entity=from_entity,
            to_entity=to_entity,
            join_key=join_key,
            database_=DATABASE
        )

        print(
            f"{from_entity} -[:{relationship}]-> {to_entity}"
        )
def main():

    print("Connecting to Neo4j...")

    driver = GraphDatabase.driver(
        URI,
        auth=(USERNAME, PASSWORD)
    )

    try:
        driver.verify_connectivity()

        print("Connected to Neo4j successfully!")

        load_entities(driver)

        print("\nCreating metadata relationships...")

        load_relationships(driver)

        print("\n==============================")
        print("METADATA GRAPH LOADED")
        print("==============================")

    finally:
        driver.close()

if __name__ == "__main__":
    main()