import csv
from pathlib import Path

folder = Path(__file__).parent
output_file = folder / "metadata_columns.csv"

rows = []

# Primary keys
primary_keys = {
    "Customer": ["customer_id"],
    "Deal": ["deal_id"],
    "Product": ["product_id"],
    "BusinessRule": ["rule_id"],
    "PricingPolicy": ["policy_id"],
    "ApprovalLevel": ["level_code"],
    "TreasuryRate": ["rate_id"],
}

# Foreign keys
foreign_keys = {
    "Deal": {
        "customer_id": "Customer",
        "product_id": "Product"
    },
    "DealOutcome": {
        "deal_id": "Deal"
    },
    "Recommendation": {
        "deal_id": "Deal"
    },
    "PolicyException": {
        "deal_id": "Deal",
        "rule_id": "BusinessRule",
        "policy_id": "PricingPolicy"
    }
}

# Process all curated CSV files
for file in sorted(folder.glob("kg_*_curated.csv")):

    with open(file, "r", encoding="utf-8-sig", newline="") as f:

        reader = csv.DictReader(f)

        if not reader.fieldnames:
            continue

        # Convert filename to entity name
        entity_name = (
            file.stem
            .replace("kg_", "")
            .replace("_curated", "")
        )

        entity_name = "".join(
            word.capitalize()
            for word in entity_name.split("_")
        )

        entity_mapping = {
            "Customers": "Customer",
             "Deals": "Deal",
             "Products": "Product",
            "BusinessRules": "BusinessRule",
             "PricingPolicies": "PricingPolicy",
            "ApprovalLevels": "ApprovalLevel",
             "TreasuryRates": "TreasuryRate",
             "DealOutcomes": "DealOutcome",
            "Recommendations": "Recommendation",
            "PolicyExceptions": "PolicyException",
    }

        entity_name = entity_mapping.get(entity_name, entity_name)

        # Process every column
        for column in reader.fieldnames:

            # Basic data type inference
            data_type = "string"

            if column.endswith("_id") or column in [
                "rule_id",
                "policy_id",
                "level_code",
                "rate_id"
            ]:
                data_type = "string"

            elif column.endswith("_pct"):
                data_type = "float"

            elif column.endswith("_amount") or column.endswith("_aed"):
                data_type = "float"

            elif (
                column.endswith("_years")
                or column.endswith("_order")
                or column in ["credit_score"]
            ):
                data_type = "integer"

            elif column in ["debt_to_equity_ratio"]:
                data_type = "float"

            elif column.endswith("_date") or column.endswith("_timestamp"):
                data_type = "datetime"

            # Identify primary / foreign key
            key_type = ""

            if column in primary_keys.get(entity_name, []):
                key_type = "PRIMARY_KEY"

            elif column in foreign_keys.get(entity_name, {}):
                key_type = "FOREIGN_KEY"

            # Add metadata record
            rows.append({
                "entity_name": entity_name,
                "column_name": column,
                "data_type": data_type,
                "description": "",
                "source_file": file.name,
                "key_type": key_type
            })


# Write metadata CSV
with open(
    output_file,
    "w",
    encoding="utf-8",
    newline=""
) as f:

    fieldnames = [
        "entity_name",
        "column_name",
        "data_type",
        "description",
        "source_file",
        "key_type"
    ]

    writer = csv.DictWriter(
        f,
        fieldnames=fieldnames
    )

    writer.writeheader()
    writer.writerows(rows)


print(f"Created: {output_file.name}")
print(f"Total column metadata records: {len(rows)}")