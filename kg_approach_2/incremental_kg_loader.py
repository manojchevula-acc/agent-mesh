import hashlib
import os
import csv

from dotenv import load_dotenv
from neo4j import GraphDatabase


# Load environment variables from .env
load_dotenv()


# --------------------------------------------------
# KG node configuration
# --------------------------------------------------

NODE_CONFIG = {
    "Customer": "customer_id",
    "Deal": "deal_id",
    "Product": "product_id",
    "BusinessRule": "rule_id",
}


# --------------------------------------------------
# Neo4j configuration
# --------------------------------------------------

URI = "bolt://localhost:7687"
USERNAME = os.environ["NEO4J_USERNAME"]
PASSWORD = os.environ["NEO4J_PASSWORD"]
DATABASE = "neo4j"

driver = GraphDatabase.driver(
    URI,
    auth=(USERNAME, PASSWORD)
)


# --------------------------------------------------
# Get existing record from Neo4j
# --------------------------------------------------

def get_existing_record(node_type, record_id):
    """
    Read an existing node from Neo4j using its business key.
    """

    key_field = NODE_CONFIG[node_type]

    query = f"""
    MATCH (n:{node_type})
    WHERE n.{key_field} = $record_id
    RETURN n
    LIMIT 1
    """

    with driver.session(database=DATABASE) as session:

        result = session.run(
            query,
            record_id=record_id
        )

        record = result.single()

        if record is None:
            return None

        return dict(record["n"])


# --------------------------------------------------
# Generate record hash
# --------------------------------------------------

def generate_record_hash(record):
    """
    Generate a fingerprint for the source record.
    """

    record_string = "|".join(
        f"{key}={record[key]}"
        for key in sorted(record)
    )

    return hashlib.sha256(
        record_string.encode("utf-8")
    ).hexdigest()


# --------------------------------------------------
# Detect NEW / CHANGED / UNCHANGED
# --------------------------------------------------

def detect_record_status(node_type, existing_record, new_record):
    """
    Determine whether a source record is
    NEW, CHANGED, or UNCHANGED.
    """

    key_field = NODE_CONFIG[node_type]

    record_id = new_record[key_field]

    # Record does not exist in Neo4j
    if existing_record is None:
        return "NEW"

    # Compare existing KG record with incoming source record
    old_hash = generate_record_hash(existing_record)
    new_hash = generate_record_hash(new_record)

    if old_hash == new_hash:
        return "UNCHANGED"

    return "CHANGED"

def get_load_action(status):
    """
    Convert the record status into the action
    that the incremental loader should take.
    """

    if status == "NEW":
        return "CREATE"

    if status == "CHANGED":
        return "UPDATE"

    if status == "UNCHANGED":
        return "SKIP"

    return "UNKNOWN"
# --------------------------------------------------
# Convert status into load action
# --------------------------------------------------

def create_node(node_type, record):
    """
    Create a new node in Neo4j using the configured node properties.
    """

    properties = {
        key: value
        for key, value in record.items()
    }

    query = f"""
    CREATE (n:{node_type})
    SET n = $properties
    """

    with driver.session(database=DATABASE) as session:
        session.run(
            query,
            properties=properties
        )


def update_node(node_type, record):
    """
    Update an existing node in Neo4j using its business key.
    """

    key_field = NODE_CONFIG[node_type]
    record_id = record[key_field]

    properties = {
        key: value
        for key, value in record.items()
        if key != key_field
    }

    query = f"""
    MATCH (n:{node_type})
    WHERE n.{key_field} = $record_id
    SET n += $properties
    """

    with driver.session(database=DATABASE) as session:
        session.run(
            query,
            record_id=record_id,
            properties=properties
        )

# --------------------------------------------------
# Test the incremental-load decision
# --------------------------------------------------

if __name__ == "__main__":

    node_type = "Customer"
    csv_file = "incremental_customers.csv"

    with open(csv_file, mode="r", encoding="utf-8") as file:

        reader = csv.DictReader(file)

        for new_record in reader:

            record_id = new_record["customer_id"]

            # Get existing record from Neo4j
            existing_record = get_existing_record(
                node_type,
                record_id
            )

            # Detect NEW / CHANGED / UNCHANGED
            status = detect_record_status(
                node_type,
                existing_record,
                new_record
            )

            # Decide CREATE / UPDATE / SKIP
action = get_load_action(status)

print("--------------------------------")
print("Node Type:", node_type)
print("Record ID:", record_id)
print("Status:", status)
print("Action:", action)

# Execute the selected action
if action == "CREATE":

    create_node(node_type, new_record)
    print("Neo4j Action: Node created")

elif action == "UPDATE":

    update_node(node_type, new_record)
    print("Neo4j Action: Node updated")

elif action == "SKIP":

    print("Neo4j Action: No change required")