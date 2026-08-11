from typing import Annotated
from agent_framework import tool


@tool
def get_account_balance(account_number: Annotated[str, "Customer account number"]) -> str:
    return (
        f"Account {account_number} balance: Rs. 1,24,500.00. "
        "Available: Rs. 1,20,000.00"
    )


@tool
def get_mini_statement(account_number: Annotated[str, "Account number"]) -> str:
    return (
        f"Last 5 transactions for {account_number}:\n"
        "1. 2026-08-08  UPI/Swiggy          -Rs.450\n"
        "2. 2026-08-07  NEFT/Salary          +Rs.85,000\n"
        "3. 2026-08-06  ATM Withdrawal       -Rs.5,000\n"
        "4. 2026-08-05  UPI/Amazon           -Rs.1,299\n"
        "5. 2026-08-04  IMPS/Rent            -Rs.20,000"
    )


@tool
def update_contact_details(
    account_number: Annotated[str, "Account number"],
    mobile: Annotated[str, "New mobile number"],
    email: Annotated[str, "New email address"],
) -> str:
    return (
        f"Contact details updated for account {account_number}. "
        f"OTP sent to {mobile} for verification."
    )
