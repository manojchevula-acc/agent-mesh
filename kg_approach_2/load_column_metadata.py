import csv
from pathlib import Path
from neo4j import GraphDatabase

URI = "neo4j://localhost:7687"
USERNAME = "neo4j"
PASSWORD = "Pnaik@96"
DATABASE = "neo4j"

folder = Path(__file__).parent


def load_columns(driver):

    file = folder / "metadata_columns.csv"

    with open(file, "r", encoding="utf-8-sig", newline="") as f:

        reader = csv.DictReader(f)
        rows = list(reader)

    query = """
    UNWIND $rows AS row

    MATCH (e:MetadataEntity)
    WHERE e.name = row.entity_name

    MERGE (c:MetadataColumn {
        name: row.column_name,
        entity: row.entity_name
    })

    SET c.data_type = row.data_type,
        c.description = row.description,
        c.source_file = row.source_file,
        c.key_type = row.key_type

    MERGE (e)-[:HAS_COLUMN]->(c)

    RETURN count(c) AS count
    """

    result = driver.execute_query(
        query,
        rows=rows,
        database_=DATABASE
    )

    print(
        f"Column metadata loaded: "
        f"{result.records[0]['count']}"
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

        load_columns(driver)

        print("\n==============================")
        print("COLUMN METADATA LOADED")
        print("==============================")

    finally:

        driver.close()


if __name__ == "__main__":
    main()