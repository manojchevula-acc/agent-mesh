import time

from golden_set import GOLDEN_SET
from dynamic_cypher_engine import ask_question


# Store end-to-end response times
performance_data = []


print("\n==============================")
print("GOLDEN SET REGRESSION TEST")
print("==============================\n")


passed = 0
failed = 0


for test in GOLDEN_SET:

    print(f"\n{test['id']}")
    print(f"Question: {test['question']}")

    try:

        # Start end-to-end timer
        start_time = time.perf_counter()

        result = ask_question(test["question"])

        # End end-to-end timer
        response_time = time.perf_counter() - start_time

        performance_data.append(response_time)

        records = result.get("records", [])

        # Successful execution is a PASS.
        # Zero records can be a valid business result.
        print(f"Neo4j returned {len(records)} row(s)")
        print(f"Runner response time: {response_time:.2f}s")
        print("RESULT: PASS")

        passed += 1

    except Exception as e:

        print(f"RESULT: FAIL - {e}")
        failed += 1


# ---------------------------------------------------------
# Golden Set Summary
# ---------------------------------------------------------

print("\n==============================")
print("GOLDEN SET SUMMARY")
print("==============================")
print(f"TOTAL : {len(GOLDEN_SET)}")
print(f"PASSED: {passed}")
print(f"FAILED: {failed}")
print("==============================")


# ---------------------------------------------------------
# Performance Summary
# ---------------------------------------------------------

print("\n==============================")
print("PERFORMANCE SUMMARY")
print("==============================")

if performance_data:

    average_time = sum(performance_data) / len(performance_data)
    minimum_time = min(performance_data)
    maximum_time = max(performance_data)

    print(f"Average E2E response : {average_time:.2f}s")
    print(f"Minimum E2E response : {minimum_time:.2f}s")
    print(f"Maximum E2E response : {maximum_time:.2f}s")

else:

    print("No successful performance measurements.")

print("==============================")