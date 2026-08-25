import csv
from pathlib import Path
from neo4j import GraphDatabase

# -----------------------------
# Neo4j connection
# -----------------------------
URI = "neo4j://localhost:7687"
USERNAME = "neo4j"
PASSWORD = "Pnaik@96"
DATABASE = "neo4j"

# -----------------------------
# CSV settings
# -----------------------------
FOLDER = Path(__file__).parent
BATCH_SIZE = 500


def clean_label(filename):
    """
    Convert a filename such as:

        kg_customers_curated.csv

    into a Neo4j label:

        Customers
    """
    name = filename.replace("kg_", "").replace("_curated.csv", "")
    return "".join(word.capitalize() for word in name.split("_"))


def load_csv(driver, csv_file):
    label = clean_label(csv_file.name)

    print(f"\nLoading: {csv_file.name}")
    print(f"Neo4j label: :{label}")

    with open(
        csv_file,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        reader = csv.DictReader(f)

        headers = reader.fieldnames

        if not headers:
            print("Skipping empty CSV.")
            return

        # Use the first column as the initial unique identifier
        id_column = headers[0]

        print(f"ID column: {id_column}")

        rows = []

        for row in reader:
            # Remove completely empty values
            cleaned_row = {
                key: value.strip()
                for key, value in row.items()
                if value is not None and value.strip() != ""
            }

            if not cleaned_row:
                continue

            rows.append(cleaned_row)

            if len(rows) >= BATCH_SIZE:
                insert_batch(
                    driver,
                    label,
                    id_column,
                    rows
                )
                rows = []

        # Insert remaining rows
        if rows:
            insert_batch(
                driver,
                label,
                id_column,
                rows
            )


def insert_batch(driver, label, id_column, rows):
    query = f"""
    UNWIND $rows AS row

    MERGE (n:{label} {{_source_id: row._source_id}})

    SET n += row

    RETURN count(n) AS processed
    """

    prepared_rows = []

    for row in rows:
        prepared_row = dict(row)

        # Store the CSV's first column as our temporary graph identifier
        prepared_row["_source_id"] = str(
            row.get(id_column, "")
        )

        prepared_rows.append(prepared_row)

    records, summary, keys = driver.execute_query(
        query,
        parameters_={"rows": prepared_rows},
        database_=DATABASE
    )

    print(
        f"  Processed {len(prepared_rows)} rows"
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

        csv_files = sorted(
            FOLDER.glob("kg_*_curated.csv")
        )

        if not csv_files:
            print("No kg_*_curated.csv files found.")
            return

        print(
            f"\nFound {len(csv_files)} KG CSV files."
        )

        for csv_file in csv_files:
            load_csv(driver, csv_file)

        print("\n===================================")
        print("CSV IMPORT COMPLETED")
        print("===================================")

    finally:
        driver.close()


if __name__ == "__main__":
    main()