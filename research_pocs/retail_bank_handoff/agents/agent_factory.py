import os
from agent_framework.openai import OpenAIChatCompletionClient
from tools.account_tools import get_account_balance, get_mini_statement, update_contact_details
from tools.card_tools import get_card_status, block_card, raise_transaction_dispute
from tools.loan_tools import check_loan_eligibility, get_loan_status
from tools.transfer_tools import fraud_screen_transfer, authorize_large_transfer
from tools.fraud_tools import flag_suspicious_transaction, freeze_account


def create_chat_client() -> OpenAIChatCompletionClient:
    return OpenAIChatCompletionClient(
        model=os.environ["GROQ_MODEL"],
        api_key=os.environ["GROQ_API_KEY"],
        base_url=os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1"),
    )


def create_agents(chat_client: OpenAIChatCompletionClient):
    # HandoffBuilder requires this flag on every participant so local history
    # stays consistent with the service across handoff tool-call short-circuits.
    h = {"require_per_service_call_history_persistence": True}

    triage_agent = chat_client.as_agent(
        name="triage_agent",
        description="First point of contact. Identifies customer intent and routes to the correct specialist.",
        instructions=(
            "You are the first point of contact at a retail bank. "
            "Greet the customer, understand their issue, and hand off to the correct specialist. "
            "Routing rules:\n"
            "  - Account balance/statement/contact updates -> account_agent\n"
            "  - Card status/blocking/disputes           -> card_agent\n"
            "  - Loan eligibility or status              -> loan_agent\n"
            "  - Fund transfers above Rs.50,000          -> transfer_agent\n"
            "  - Suspected fraud or suspicious activity  -> fraud_agent\n"
            "Never handle specialised requests yourself -- always hand off. "
            "Once a specialist has resolved the issue and handed back to you, "
            "ask if there is anything else you can help with. "
            "If the customer says no or thanks you, say goodbye warmly -- "
            "end your reply with 'Thank you for banking with us. Have a great day!'"
        ),
        **h,
    )

    account_agent = chat_client.as_agent(
        name="account_agent",
        description="Handles account balance enquiries, mini statements, and contact detail updates.",
        instructions=(
            "You are an account services specialist at a retail bank. "
            "Help customers with balance enquiries, mini statements, and contact detail updates. "
            "If the customer raises a fraud concern during the conversation, hand off to fraud_agent. "
            "Once you have resolved the customer's request, hand off back to triage_agent."
        ),
        tools=[get_account_balance, get_mini_statement, update_contact_details],
        **h,
    )

    card_agent = chat_client.as_agent(
        name="card_agent",
        description="Handles card status checks, blocking lost or stolen cards, and transaction disputes.",
        instructions=(
            "You are a card services specialist at a retail bank. "
            "Handle card status checks, block lost or stolen cards, and raise transaction disputes. "
            "If the customer suspects fraud on their card, hand off to fraud_agent. "
            "Once you have resolved the customer's request, hand off back to triage_agent."
        ),
        tools=[get_card_status, block_card, raise_transaction_dispute],
        **h,
    )

    loan_agent = chat_client.as_agent(
        name="loan_agent",
        description="Handles loan eligibility checks and existing loan application status.",
        instructions=(
            "You are a loans specialist at a retail bank. "
            "Check eligibility for personal, home, and auto loans, and track existing applications. "
            "Once you have resolved the customer's request, hand off back to triage_agent."
        ),
        tools=[check_loan_eligibility, get_loan_status],
        **h,
    )

    transfer_agent = chat_client.as_agent(
        name="transfer_agent",
        description="Handles high-value fund transfers (above Rs.50,000) requiring dual-gate approval.",
        instructions=(
            "You handle high-value fund transfers above Rs.50,000. "
            "Step 1 -- always call fraud_screen_transfer first. This requires human approval before it runs. "
            "Step 2 -- only after the fraud screen has passed, call authorize_large_transfer. "
            "This also requires human approval. "
            "Both steps pause for a human reviewer -- wait for their decision before proceeding. "
            "If the fraud screen is rejected or indicates high risk, hand off to fraud_agent immediately. "
            "Once the transfer is fully authorised and complete, hand off back to triage_agent."
        ),
        tools=[fraud_screen_transfer, authorize_large_transfer],
        **h,
    )

    fraud_agent = chat_client.as_agent(
        name="fraud_agent",
        description="Investigates suspicious transactions and can freeze accounts with human approval.",
        instructions=(
            "You are the fraud investigation specialist at a retail bank. "
            "Flag suspicious transactions and, if necessary, freeze accounts. "
            "Account freezes require human approval -- wait for the reviewer's decision before proceeding. "
            "Once investigation steps are complete, hand off back to triage_agent."
        ),
        tools=[flag_suspicious_transaction, freeze_account],
        **h,
    )

    return triage_agent, account_agent, card_agent, loan_agent, transfer_agent, fraud_agent
