import csv
from pathlib import Path

folder = Path(__file__).parent

for file in sorted(folder.glob("kg_*_curated.csv")):
    print("\n" + "=" * 80)
    print(file.name)
    print("=" * 80)

    with open(file, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        print("Columns:")
        for column in reader.fieldnames or []:
            print(f"  - {column}")

        first_row = next(reader, None)

        if first_row:
            print("\nFirst row:")
            for key, value in first_row.items():
                print(f"  {key}: {value}")