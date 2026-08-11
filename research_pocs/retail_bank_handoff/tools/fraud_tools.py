from typing import Annotated
from agent_framework import tool


@tool
def flag_suspicious_transaction(
    account_number: Annotated[str, "Account number"],
    transaction_id: Annotated[str, "Transaction ID to flag"],
) -> str:
    case_id = f"FRAUD-{transaction_id[:6].upper()}"
    return (
        f"Transaction {transaction_id} flagged for investigation. "
        f"Case ID: {case_id} opened."
    )


# Gate — account freeze requires human approval
@tool(approval_mode="always_require")
def freeze_account(
    account_number: Annotated[str, "Account number to freeze"],
    reason: Annotated[str, "Reason for freezing the account"],
) -> str:
    return (
        f"Account {account_number} FROZEN. "
        f"Reason: {reason}. Customer notified via SMS and email."
    )
