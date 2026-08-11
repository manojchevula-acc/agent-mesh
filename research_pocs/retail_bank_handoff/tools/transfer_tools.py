from typing import Annotated
from agent_framework import tool


# Gate 1 — Fraud Analyst must approve before this executes
@tool(approval_mode="always_require")
def fraud_screen_transfer(
    from_account: Annotated[str, "Source account number"],
    to_account: Annotated[str, "Destination account number"],
    amount: Annotated[str, "Transfer amount in INR"],
    transfer_type: Annotated[str, "Transfer type: NEFT/RTGS/IMPS"],
) -> str:
    return (
        f"Fraud screening complete for Rs.{amount} {transfer_type}.\n"
        f"From: {from_account} -> To: {to_account}\n"
        "Risk Score: MEDIUM (first-time beneficiary, large amount)\n"
        "Status: PASSED FRAUD SCREEN -- awaiting manager approval"
    )


# Gate 2 — Branch Manager must approve before this executes
@tool(approval_mode="always_require")
def authorize_large_transfer(
    from_account: Annotated[str, "Source account number"],
    to_account: Annotated[str, "Destination account number"],
    amount: Annotated[str, "Transfer amount in INR"],
    fraud_screen_ref: Annotated[str, "Reference ID from fraud screen step"],
) -> str:
    txn_ref = f"TXN-{from_account[-4:]}{amount[:3]}"
    return (
        f"Transfer of Rs.{amount} AUTHORIZED by manager.\n"
        f"Transaction ref: {txn_ref}\n"
        "Funds will credit within 2 hours (RTGS) or next working day (NEFT)."
    )
