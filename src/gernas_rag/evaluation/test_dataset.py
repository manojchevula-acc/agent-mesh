"""FAB GERNAS evaluation test cases."""

TestCase = dict[str, str]

TEST_CASES: list[TestCase] = [
    {
        "question": "What is the minimum pricing floor for a BB-rated corporate term loan with 4-year tenor in AED?",
        "ground_truth": "The minimum floor for a BB-rated 3-5 year AED corporate term loan is 260 basis points over FTP.",
    },
    {
        "question": "What approval authority is required for a BBB-rated AED 150 million facility?",
        "ground_truth": "A BBB-rated AED 150 million facility requires approval from the Segment Credit Head because it falls within the AED 100M–500M facility size band and the AAA to BBB rating category."
    },
    {
        "question": "What are the mandatory human-in-the-loop requirements for HIGH-risk AI systems under CBUAE Circular 2024/BSE/047?",
        "ground_truth": "Article 5.1.1 requires mandatory human review and approval for all HIGH-risk AI/ML system outputs before action.",
    },
    {
        "question": "What data grounding evidence must be included in the MRM Evidence Pack for an AI pricing model?",
        "ground_truth": "The MRM Evidence Pack must include RAG architecture documentation including vector database configuration, document sources indexed, embedding model specifications, retrieval strategy, and re-indexing schedule.",
    },
    {
        "question": "What is FAB's hard limit for Real Estate UAE sector concentration?",
        "ground_truth": "The hard limit for Real Estate UAE sector concentration is 25% of total corporate portfolio.",
    },
    {
        "question": "What are the eligible tenors for a FAB Corporate Term Loan?",
        "ground_truth": "Standard eligible tenors are 12, 24, 36, 48, 60, 72, and 84 months. Longer tenors require GCC approval.",
    },
    {
        "question": "What documentation is required when submitting a credit proposal that used the GERNAS Pricing Agent?",
        "ground_truth": "The GERNAS Pricing Agent MRM Evidence Pack is mandatory for all credit approval submissions effective 1 June 2024.",
    },
]
