from typing import Annotated
from agent_framework import tool


@tool
def check_loan_eligibility(
    account_number: Annotated[str, "Account number"],
    loan_type: Annotated[str, "Type: personal/home/auto"],
    amount: Annotated[str, "Requested loan amount in INR"],
) -> str:
    return (
        f"Eligibility for {loan_type} loan of Rs.{amount}:\n"
        "Credit Score: 762 (Good)\n"
        "Pre-approved limit: Rs.5,00,000\n"
        "Indicative rate: 10.5% p.a.\n"
        "Status: ELIGIBLE -- proceed to application"
    )


@tool
def get_loan_status(loan_id: Annotated[str, "Loan application ID"]) -> str:
    return (
        f"Loan {loan_id}: Under credit review. "
        "Expected decision by 2026-08-14."
    )
