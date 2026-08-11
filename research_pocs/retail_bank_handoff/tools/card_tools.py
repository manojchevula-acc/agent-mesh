from typing import Annotated
from agent_framework import tool


@tool
def get_card_status(card_last4: Annotated[str, "Last 4 digits of card"]) -> str:
    return (
        f"Card ending {card_last4}: Active. "
        "Credit limit Rs.2,00,000. Available Rs.1,45,000."
    )


@tool
def block_card(
    card_last4: Annotated[str, "Last 4 digits of card"],
    reason: Annotated[str, "Reason for blocking"],
) -> str:
    return (
        f"Card ending {card_last4} has been blocked. "
        f"Reason: {reason}. New card will arrive in 5-7 days."
    )


@tool
def raise_transaction_dispute(
    card_last4: Annotated[str, "Card last 4 digits"],
    transaction_id: Annotated[str, "Transaction ID to dispute"],
    amount: Annotated[str, "Disputed amount in INR"],
) -> str:
    ref = transaction_id[:6].upper()
    return (
        f"Dispute raised for Rs.{amount} on card {card_last4}. "
        f"Reference: DISP-{ref}. Resolution in 7 working days."
    )
