import csv


def validate_schema(csv_file, required_columns):
    with open(csv_file, mode="r", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        actual_columns = reader.fieldnames

        missing_columns = [
            column
            for column in required_columns
            if column not in actual_columns
        ]

        unexpected_columns = [
            column
            for column in actual_columns
            if column not in required_columns
        ]

        if missing_columns or unexpected_columns:
            return {
                "status": "FAILED",
                "missing_columns": missing_columns,
                "unexpected_columns": unexpected_columns,
            }

        return {
            "status": "PASSED",
            "missing_columns": [],
            "unexpected_columns": [],
        }

def validate_required_values(csv_file, required_columns):
    missing_values = []

    with open(csv_file, mode="r", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        for row_number, row in enumerate(reader, start=2):
            for column in required_columns:
                if not row.get(column, "").strip():
                    missing_values.append(
                        {
                            "row": row_number,
                            "column": column,
                        }
                    )

    if missing_values:
        return {
            "status": "FAILED",
            "missing_values": missing_values,
        }

    return {
        "status": "PASSED",
        "missing_values": [],
    }

def validate_duplicate_keys(csv_file, key_column):
    seen_keys = set()
    duplicate_keys = []

    with open(csv_file, mode="r", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        for row in reader:
            key_value = row.get(key_column, "").strip()

            if key_value in seen_keys:
                duplicate_keys.append(key_value)
            else:
                seen_keys.add(key_value)

    if duplicate_keys:
        return {
            "status": "FAILED",
            "duplicate_keys": duplicate_keys,
        }

    return {
        "status": "PASSED",
        "duplicate_keys": [],
    }
def validate_allowed_values(csv_file, column, allowed_values):
    invalid_values = []

    with open(csv_file, mode="r", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        for row_number, row in enumerate(reader, start=2):
            value = row.get(column, "").strip()

            if value not in allowed_values:
                invalid_values.append(
                    {
                        "row": row_number,
                        "column": column,
                        "value": value,
                    }
                )

    if invalid_values:
        return {
            "status": "FAILED",
            "invalid_values": invalid_values,
        }

    return {
        "status": "PASSED",
        "invalid_values": [],
    }

def validate_row_count(csv_file, expected_count):
    with open(csv_file, mode="r", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        actual_count = sum(1 for _ in reader)

    if actual_count != expected_count:
        return {
            "status": "FAILED",
            "expected_count": expected_count,
            "actual_count": actual_count,
        }

    return {
        "status": "PASSED",
        "expected_count": expected_count,
        "actual_count": actual_count,
    }

def validate_references(
    source_file,
    reference_file,
    source_key,
    reference_key
):
    reference_ids = set()

    with open(reference_file, mode="r", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        for row in reader:
            reference_ids.add(
                row.get(reference_key, "").strip()
            )

    invalid_references = []

    with open(source_file, mode="r", encoding="utf-8") as file:
        reader = csv.DictReader(file)

        for row_number, row in enumerate(reader, start=2):
            source_id = row.get(source_key, "").strip()

            if source_id and source_id not in reference_ids:
                invalid_references.append(
                    {
                        "row": row_number,
                        "value": source_id,
                    }
                )

    if invalid_references:
        return {
            "status": "FAILED",
            "invalid_references": invalid_references,
        }

    return {
        "status": "PASSED",
        "invalid_references": [],
    }

def get_remediation_action(check_name):

    actions = {
        "Schema": (
            "Check the source schema and column mapping."
        ),

        "Required Values": (
            "Check the source data or transformation logic "
            "and populate the required field."
        ),

        "Duplicate Keys": (
            "Check for duplicate business keys in the source "
            "data and correct the duplicate records."
        ),

        "Allowed Values": (
            "Check the source value and correct it according "
            "to the allowed business values."
        ),

        "Row Count": (
            "Check the source-to-target record counts and "
            "review filtering or transformation logic."
        ),

        "References": (
            "Check the referenced entity data or correct "
            "the business key before loading to the KG."
        ),
    }

    return actions.get(
        check_name,
        "Review the validation failure and source data."
    )

if __name__ == "__main__":

    csv_file = "incremental_customers.csv"

    required_columns = [
        "customer_id",
        "customer_name",
        "customer_segment",
        "industry",
        "region",
    ]

    validation_results = {}
    validation_issues = []

    # 1. Schema validation
    result = validate_schema(
        csv_file,
        required_columns
    )
    validation_results["Schema"] = result["status"]

    if result["status"] == "FAILED":
        validation_issues.append({
            "check": "Schema",
            "details": {
                "missing_columns": result["missing_columns"],
                "unexpected_columns": result["unexpected_columns"],
            },
            "action": get_remediation_action("Schema")
        })

    # 2. Required value validation
    result = validate_required_values(
        csv_file,
        ["customer_id", "customer_name"]
    )
    validation_results["Required Values"] = result["status"]

    if result["status"] == "FAILED":
        validation_issues.append({
            "check": "Required Values",
            "details": result["missing_values"],
            "action": get_remediation_action("Required Values")
        })

    # 3. Duplicate key validation
    result = validate_duplicate_keys(
        csv_file,
        "customer_id"
    )
    validation_results["Duplicate Keys"] = result["status"]

    if result["status"] == "FAILED":
        validation_issues.append({
            "check": "Duplicate Keys",
            "details": result["duplicate_keys"],
            "action": get_remediation_action("Duplicate Keys")
        })

    # 4. Allowed value validation
    result = validate_allowed_values(
        csv_file,
        "customer_segment",
        ["SME", "Corporate"]
    )
    validation_results["Allowed Values"] = result["status"]

    if result["status"] == "FAILED":
        validation_issues.append({
            "check": "Allowed Values",
            "details": result["invalid_values"],
            "action": get_remediation_action("Allowed Values")
        })

    # 5. Row count validation
    result = validate_row_count(
        csv_file,
        5
    )
    validation_results["Row Count"] = result["status"]

    if result["status"] == "FAILED":
        validation_issues.append({
            "check": "Row Count",
            "details": {
                "expected": result["expected_count"],
                "actual": result["actual_count"],
            },
            "action": get_remediation_action("Row Count")
        })

    # 6. Reference validation
    result = validate_references(
        "deals_debug_test.csv",
        "incremental_customers.csv",
        "customer_id",
        "customer_id"
    )
    validation_results["References"] = result["status"]

    if result["status"] == "FAILED":
        for issue in result["invalid_references"]:
            validation_issues.append({
                "check": "References",
                "details": (
                    f"Row {issue['row']}: "
                    f"Customer ID {issue['value']} "
                    f"does not exist in the Customer data."
                ),
            "action": (
                "Check the Customer source data or correct "
                "the Deal customer_id before loading to the KG."
            )
        })

    # Validation summary
    print("\nETL VALIDATION SUMMARY")
    print("----------------------")

    for check, status in validation_results.items():
        print(f"{check:<20}: {status}")

    overall_status = (
        "FAILED"
        if validation_issues
        else "PASSED"
    )

    print("----------------------")
    print("Overall Status       :", overall_status)

    # Issue details
    if validation_issues:
        print("\nISSUES DETECTED")
        print("---------------")

        for issue in validation_issues:
            print(f"{issue['check']}:")
            print(f"  {issue['details']}")
            print(f"  Action  : {issue['action']}")