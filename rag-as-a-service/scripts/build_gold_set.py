"""Build the gold Q&A evaluation set for the FAB GERNAS RAG system.

Each gold item records:
  - a question,
  - the expected_sources it must be answerable from (document_name +
    clause_reference + modality, as stored in Qdrant payloads),
  - the expected_answer a correct system should produce.

Every expected_source below was picked by hand from a live scroll of the
``fab_gernas_docs`` collection (see scripts/verify_chunks.py and
scripts/view_media_chunks.py) — only documents whose text AND image chunks
were fully and cleanly stored (real VLM captions, resolvable artifacts, no
orphan/degraded warnings) were used. Two documents were deliberately excluded:
  - FAB_Portfolio_Risk_Analytics_Report_Q2_2024: its text references
    "Figure 1/3/4" but zero image chunks exist for it — partially multimodal.
  - FAB_Executed_Term_Sheet_Scanned_Sample: its only image chunk is a blank/
    illegible signature scribble with no verifiable content.

Usage:
    python scripts/build_gold_set.py                  # validate + write
    python scripts/build_gold_set.py --no-validate     # write without checking Qdrant
    python scripts/build_gold_set.py --output data/eval/gold_qa.json
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

GOLD_SET: list[dict] = [
    {
        "id": "1",
        "name": "cbuae-text-dates",
        "question": "What circular does CBUAE/BSE/2024/047 supersede, and what are its issue and effective dates?",
        "expected_answer": (
            "It supersedes CBUAE/BSE/2022/031 (Guidance on Automated Decision-Making). "
            "Issue date: 15 March 2024. Effective date: 1 July 2024."
        ),
        "expected_sources": [
            {"document": "CBUAE_Circular_2024_BSE_047_AI_Governance", "clause_reference": "2.1.1", "modality": "text"}
        ],
    },
    {
        "id": "2",
        "name": "cbuae-image-timeline",
        "question": "Per the compliance timeline figure in the CBUAE circular, what is the Tier 1 model validation deadline, and when must all other LFIs complete theirs?",
        "expected_answer": (
            "Tier 1 Validation Deadline: 31-Dec-2024. All Other LFIs Validation Deadline: 30-Jun-2025. "
            "(Gap Assessment is due 30-Aug-2024, 60 days post-effective-date.)"
        ),
        "expected_sources": [
            {"document": "CBUAE_Circular_2024_BSE_047_AI_Governance", "clause_reference": "figure p.1", "modality": "figure"}
        ],
    },
    {
        "id": "3",
        "name": "cbuae-image-risk-pyramid",
        "question": "Which AI/ML risk tier do credit pricing agents and loan approval models fall into under Article 4, and what validation does that require?",
        "expected_answer": "HIGH tier — mandatory validation before deployment, with annual re-validation.",
        "expected_sources": [
            {"document": "CBUAE_Circular_2024_BSE_047_AI_Governance", "clause_reference": "Article 4", "modality": "figure"}
        ],
    },
    {
        "id": "4",
        "name": "term-loan-text-eligibility",
        "question": "What minimum internal credit rating and turnover does a client need to be eligible for FAB's Corporate Term Loan?",
        "expected_answer": (
            "Internal credit rating of B or above (CCC and below are not eligible without a GCC waiver); "
            "annual turnover exceeding AED 50 million; at least two years of audited financial statements; "
            "and clean KYC/AML status."
        ),
        "expected_sources": [
            {"document": "FAB_Corporate_Term_Loan_Product_Manual_v2_0", "clause_reference": "2.1", "modality": "text"}
        ],
    },
    {
        "id": "5",
        "name": "term-loan-image-fee-schedule",
        "question": "What is the Standard Rate versus Floor Rate arrangement fee (in bps) for a facility between AED 50M and 250M?",
        "expected_answer": "Standard Rate: 50 bps. Floor Rate: 35 bps.",
        "expected_sources": [
            {"document": "FAB_Corporate_Term_Loan_Product_Manual_v2_0", "clause_reference": "figure p.4", "modality": "figure"}
        ],
    },
    {
        "id": "6",
        "name": "approval-diagrams-image-escalation",
        "question": "Per the Pricing Approval Escalation Path diagram, which committee approves facilities of AED 500M to 1 billion for clients of all ratings?",
        "expected_answer": "The Country Credit Committee (CCC).",
        "expected_sources": [
            {"document": "FAB_Credit_Approval_Governance_Diagrams", "clause_reference": "5.1", "modality": "figure"}
        ],
    },
    {
        "id": "7",
        "name": "approval-diagrams-image-orgchart",
        "question": "Per the AI Governance Reporting Structure diagram, which team is organisationally separate from model developers, and under which article?",
        "expected_answer": "The Independent Validation Team, per Article 3.2.1.",
        "expected_sources": [
            {"document": "FAB_Credit_Approval_Governance_Diagrams", "clause_reference": "2.1.2", "modality": "figure"}
        ],
    },
    {
        "id": "8",
        "name": "conc-limits-text-ownership",
        "question": "Who owns FAB's Credit Concentration & Exposure Limits Policy, and what is its effective date and approving body?",
        "expected_answer": (
            "Owner: Chief Credit Officer. Effective Date: 1 April 2024. "
            "Approved by the Board Risk & Compliance Committee."
        ),
        "expected_sources": [
            {"document": "FAB_Credit_Concentration_Limits_Policy_v1_8", "clause_reference": "1.8", "modality": "text"}
        ],
    },
    {
        "id": "9",
        "name": "conc-limits-image-single-obligor",
        "question": "What is the maximum exposure limit for a Foreign GRE counterparty, and what percentage of Tier 1 capital does that represent?",
        "expected_answer": "AED 2.0 billion, representing 8% of Tier 1 capital.",
        "expected_sources": [
            {"document": "FAB_Credit_Concentration_Limits_Policy_v1_8", "clause_reference": "2.2", "modality": "figure"}
        ],
    },
    {
        "id": "10",
        "name": "conc-limits-image-sector",
        "question": "What are the current utilisation, soft limit, and hard limit for the Real Estate – UAE sector?",
        "expected_answer": "Current utilisation: 18.4%. Soft limit: 20%. Hard limit: 25%.",
        "expected_sources": [
            {"document": "FAB_Credit_Concentration_Limits_Policy_v1_8", "clause_reference": "18.4", "modality": "figure"}
        ],
    },
    {
        "id": "11",
        "name": "pricing-image-ftp-curve",
        "question": "What is the illustrative FTP rate for the 3-5 year tenor bucket (as of Jun-2024)?",
        "expected_answer": (
            "5.85% — made up of a 35 bps liquidity premium and a 25 bps tenor premium over the base rate."
        ),
        "expected_sources": [
            {"document": "FAB_Credit_Pricing_Policy_v2_4", "clause_reference": "4.1", "modality": "figure"}
        ],
    },
    {
        "id": "12",
        "name": "mrm-image-dashboard",
        "question": "In the §4.1 Performance Monitoring dashboard, what was the Human Override Rate in April, and does it breach the 20% threshold?",
        "expected_answer": "21% in April — this breaches the 20% reference threshold and is the highest point in the six-month series.",
        "expected_sources": [
            {"document": "FAB_Model_Risk_Management_Framework_v3_1", "clause_reference": "4.1", "modality": "figure"}
        ],
    },
    # ── Natural-phrasing questions — deliberately do NOT name the source
    # document/policy, so these test whether retrieval finds the right chunk
    # from semantic content alone, the way a real user would ask.
    {
        "id": "13",
        "name": "text-rcf-floor",
        "question": "What's the minimum drawn margin floor and commitment fee floor for a revolving credit facility with a BB-rated client?",
        "expected_answer": "160 bps drawn margin floor and 40 bps commitment fee floor (plus a 20 bps facility fee floor).",
        "expected_sources": [
            {"document": "FAB_Credit_Pricing_Policy_v2_4", "clause_reference": "2.4", "modality": "text"}
        ],
    },
    {
        "id": "14",
        "name": "text-mrm-tier",
        "question": "Which model risk tier do IFRS 9 impairment models and pricing AI agents fall under, and what validation do they need?",
        "expected_answer": "Tier 1 (High) — full independent validation before deployment, and annual re-validation thereafter.",
        "expected_sources": [
            {"document": "FAB_Model_Risk_Management_Framework_v3_1", "clause_reference": "Section  2", "modality": "text"}
        ],
    },
    {
        "id": "15",
        "name": "text-consumer-transparency",
        "question": "If a customer asks why their pricing was set the way it was, and AI played a role in that decision, does the institution have to explain it to them?",
        "expected_answer": (
            "Yes — the institution must give a meaningful explanation of the factors that influenced the outcome, "
            "though it is not required to disclose the proprietary model architecture or training data."
        ),
        "expected_sources": [
            {"document": "CBUAE_Circular_2024_BSE_047_AI_Governance", "clause_reference": "6.2", "modality": "text"}
        ],
    },
    {
        "id": "16",
        "name": "text-regulatory-reporting",
        "question": "How often must an AI/ML systems return be submitted to the regulator, and by what date each year?",
        "expected_answer": "Annually, by 31 March each year, covering the preceding calendar year.",
        "expected_sources": [
            {"document": "CBUAE_Circular_2024_BSE_047_AI_Governance", "clause_reference": "6.2", "modality": "text"}
        ],
    },
    {
        "id": "17",
        "name": "text-material-business-process",
        "question": "Does using an AI model to recommend loan pricing count as a 'material business process' under the AI governance rules?",
        "expected_answer": "Yes — pricing of credit facilities using AI-generated recommendations is explicitly classified as a material business process.",
        "expected_sources": [
            {"document": "CBUAE_Circular_2024_BSE_047_AI_Governance", "clause_reference": "Article 1.4", "modality": "text"}
        ],
    },
    {
        "id": "18",
        "name": "text-worked-example",
        "question": "For a BBB-rated client taking a 4-year AED term loan, if the FTP is 4.85%, what's the minimum all-in rate they should get?",
        "expected_answer": "6.65% — the 4.85% FTP plus the 180 bps minimum floor spread for a BBB-rated, 3-5 year facility.",
        "expected_sources": [
            {"document": "FAB_Credit_Pricing_Policy_v2_4", "clause_reference": "2.4", "modality": "text"}
        ],
    },
    {
        "id": "19",
        "name": "text-agent-input-params",
        "question": "When using the pricing agent for a term loan, which input parameters does it need to retrieve the correct floor rate?",
        "expected_answer": "product_type, tenor_months, currency, and client_rating.",
        "expected_sources": [
            {"document": "FAB_Corporate_Term_Loan_Product_Manual_v2_0", "clause_reference": "3.2", "modality": "text"}
        ],
    },
    {
        "id": "20",
        "name": "text-commitment-fee",
        "question": "What's the commitment fee charged on the undrawn portion of a corporate term loan, and is there a minimum?",
        "expected_answer": "35% of the margin as the standard rate, with a minimum floor of 20 bps per annum on the undrawn portion.",
        "expected_sources": [
            {"document": "FAB_Corporate_Term_Loan_Product_Manual_v2_0", "clause_reference": "3.2", "modality": "text"}
        ],
    },
    {
        "id": "21",
        "name": "image-recommendation-acceptance",
        "question": "What was the recommendation acceptance rate in April, and how does that compare to the minimum threshold?",
        "expected_answer": "65% in April — comfortably above the 60% minimum threshold shown as the dashed reference line.",
        "expected_sources": [
            {"document": "FAB_Model_Risk_Management_Framework_v3_1", "clause_reference": "4.1", "modality": "figure"}
        ],
    },
    # ── Ported from the frontend's built-in "Sample questions" panel
    # (FAB-RAG-Layer/frontend/src/config/constants.ts, SAMPLE_QUESTIONS) so they
    # can be tested the same way as the rest of the gold set.
    {
        "id": "22",
        "name": "ui-pricing-floor-bb",
        "question": "What is the pricing floor for BB-rated AED corporate loans?",
        "expected_answer": "165 bps (≤1yr), 210 bps (1-3yr), 260 bps (3-5yr), and 320 bps (>5yr) — all over the applicable FTP rate.",
        "expected_sources": [
            {"document": "FAB_Credit_Pricing_Policy_v2_4", "clause_reference": "2.4", "modality": "text"}
        ],
    },
    {
        "id": "23",
        "name": "ui-interest-rate-components",
        "question": "What are the interest rate components for term loans?",
        "expected_answer": "Base Rate (FTP, set monthly by Treasury), Credit Spread/Margin (65-450 bps over FTP based on rating/security/tenor), and the Arrangement Fee.",
        "expected_sources": [
            {"document": "FAB_Corporate_Term_Loan_Product_Manual_v2_0", "clause_reference": "3.1", "modality": "text"}
        ],
    },
    {
        "id": "24",
        "name": "ui-credit-spread-by-rating",
        "question": "How is credit spread determined for different risk ratings?",
        "expected_answer": (
            "It's a risk-based spread of 65-450 bps over FTP, reflecting the client's credit rating, security, and "
            "facility tenor, subject to the minimum floors in the Credit Pricing Policy."
        ),
        "expected_sources": [
            {"document": "FAB_Corporate_Term_Loan_Product_Manual_v2_0", "clause_reference": "3.1", "modality": "text"}
        ],
    },
    {
        "id": "25",
        "name": "ui-cbuae-governance-requirements",
        "question": "What are the AI governance requirements under the CBUAE circular?",
        "expected_answer": (
            "They include AI/ML risk-tier classification (Article 4), mandatory human-in-the-loop controls for "
            "HIGH-risk systems (Article 5), independent model validation organisationally separate from developers "
            "(Article 3.2), data governance/quality controls (Article 7), and annual regulatory reporting (Article 8)."
        ),
        "expected_sources": [
            {"document": "CBUAE_Circular_2024_BSE_047_AI_Governance", "clause_reference": "3.2", "modality": "text"}
        ],
    },
    {
        "id": "26",
        "name": "ui-cbuae-notify-incident",
        "question": "When must a bank notify CBUAE about an AI model incident?",
        "expected_answer": (
            "Within 30 calendar days of: (a) initial deployment of a new HIGH-risk AI/ML system; (b) any material "
            "modification to an existing HIGH-risk system; or (c) any AI-related incident causing material consumer "
            "detriment or a regulatory breach."
        ),
        "expected_sources": [
            {"document": "CBUAE_Circular_2024_BSE_047_AI_Governance", "clause_reference": "6.2", "modality": "text"}
        ],
    },
    {
        "id": "27",
        "name": "ui-oversight-controls-credit-ai",
        "question": "What oversight controls are required for AI models in credit decisions?",
        "expected_answer": (
            "Mandatory human-in-the-loop review — a human reviewer must have full access to the inputs/reasoning, "
            "perform a floor check and independent assessment, and approve or override the AI recommendation, with "
            "overrides logged and audited (7-year retention); an override rate above 25% over a rolling 90 days "
            "escalates to the AI Risk Officer."
        ),
        "expected_sources": [
            {"document": "CBUAE_Circular_2024_BSE_047_AI_Governance", "clause_reference": "5.1", "modality": "figure"}
        ],
    },
    {
        "id": "28",
        "name": "ui-model-incident-definition",
        "question": "What constitutes a model incident and what is the reporting deadline?",
        "expected_answer": (
            "A model incident is any AI/ML output that breaches a pricing floor, causes customer detriment, triggers "
            "a regulatory notification, or causes a financial loss from model error. It must be reported to the Model "
            "Risk Management team within 24 hours of detection, and to CBUAE within 30 calendar days if material "
            "(financial impact > AED 1 million or a regulatory breach)."
        ),
        "expected_sources": [
            {"document": "FAB_Model_Risk_Management_Framework_v3_1", "clause_reference": "3.2", "modality": "text"}
        ],
    },
    {
        "id": "29",
        "name": "ui-mrm-validation-requirements",
        "question": "What are the model validation requirements in the MRM framework?",
        "expected_answer": (
            "Tier 1 (High, e.g. credit pricing AI) — full independent validation before deployment, annual "
            "re-validation. Tier 2 (Medium) — targeted validation, biennial. Tier 3 (Low) — self-certification by "
            "the model owner."
        ),
        "expected_sources": [
            {"document": "FAB_Model_Risk_Management_Framework_v3_1", "clause_reference": "Section  2", "modality": "text"}
        ],
    },
    {
        "id": "30",
        "name": "ui-model-risk-escalation",
        "question": "How should model risk be escalated to senior management?",
        "expected_answer": "An override rate exceeding 25% over a rolling 90-day period escalates to the AI Risk Officer (Article 5.2.2).",
        "expected_sources": [
            {"document": "FAB_Credit_Approval_Governance_Diagrams", "clause_reference": "5.1.2", "modality": "figure"}
        ],
    },
    {
        "id": "31",
        "name": "ui-concentration-limits-corporate",
        "question": "What are the credit concentration limits for corporate counterparties?",
        "expected_answer": (
            "Limits vary by classification, e.g. Corporate Investment Grade (AAA-BBB) up to AED 3.5bn (14% of Tier 1 "
            "capital), Corporate Sub-Investment Grade (BB) up to AED 1.2bn (5% T1), and Corporate Speculative (B and "
            "below) up to AED 0.4bn (1.6% T1)."
        ),
        "expected_sources": [
            {"document": "FAB_Credit_Concentration_Limits_Policy_v1_8", "clause_reference": "2.2", "modality": "figure"}
        ],
    },
    {
        "id": "32",
        "name": "ui-concentration-breach-trigger",
        "question": "What triggers a breach of the concentration limit policy?",
        "expected_answer": (
            "Approaching the Soft Limit triggers enhanced monitoring and CCC notification. Breaching the Hard Limit "
            "requires immediate GCC review and a remediation plan — utilisation above 90% of the hard limit must be "
            "reported to the CRO within 24 hours, and no new facilities may be approved in that sector without a GCC "
            "waiver."
        ),
        "expected_sources": [
            {"document": "FAB_Credit_Concentration_Limits_Policy_v1_8", "clause_reference": "3.1", "modality": "text"}
        ],
    },
    {
        "id": "33",
        "name": "ui-term-loan-eligibility",
        "question": "What are the eligibility criteria for a corporate term loan?",
        "expected_answer": (
            "Internal credit rating of B or above (CCC and below require a GCC waiver); annual turnover exceeding "
            "AED 50 million; at least two years of audited financial statements; and clean KYC/AML status."
        ),
        "expected_sources": [
            {"document": "FAB_Corporate_Term_Loan_Product_Manual_v2_0", "clause_reference": "2.1", "modality": "text"}
        ],
    },
    {
        "id": "34",
        "name": "ui-term-loan-documentation",
        "question": "What documentation is required to apply for a term loan?",
        "expected_answer": (
            "Completed Credit Application Form (CAF), audited financial statements (minimum 3 years, most recent "
            "within 18 months), financing-purpose documentation, KYC documentation, the GERNAS Pricing Agent output "
            "(recommendation, RAROC, MRM Evidence Pack), and Pricing Exception documentation if applicable. The MRM "
            "Evidence Pack has been mandatory for all credit approval submissions since 1 June 2024."
        ),
        "expected_sources": [
            {"document": "FAB_Corporate_Term_Loan_Product_Manual_v2_0", "clause_reference": "3.2", "modality": "text"}
        ],
    },
]


async def _validate_against_qdrant(gold_set: list[dict]) -> list[str]:
    """Check every expected_source still exists in the live Qdrant collection.

    Returns a list of warning strings (empty if everything checks out). This
    keeps the gold file honest as the corpus is re-ingested over time — a gold
    question pointing at a chunk that no longer exists is worse than no gold
    question at all.
    """
    from qdrant_client import AsyncQdrantClient

    from gernas_rag.config.settings import get_settings

    settings = get_settings()
    vcfg = settings.vectordb
    client = (
        AsyncQdrantClient(path=vcfg.qdrant_path)
        if vcfg.qdrant_path
        else AsyncQdrantClient(url=vcfg.qdrant_url, api_key=vcfg.qdrant_api_key)
    )

    points: list[dict] = []
    offset = None
    while True:
        batch, offset = await client.scroll(
            collection_name=vcfg.collection_name, limit=256, offset=offset, with_payload=True, with_vectors=False
        )
        points.extend(p.payload or {} for p in batch)
        if offset is None:
            break

    index = {(p.get("document_name", ""), p.get("clause_reference", "")) for p in points}

    warnings: list[str] = []
    for item in gold_set:
        for src in item["expected_sources"]:
            key = (src["document"], src["clause_reference"])
            if key not in index:
                warnings.append(
                    f"[{item['id']} · {item.get('name', '')}] no chunk found for document={src['document']!r} "
                    f"clause_reference={src['clause_reference']!r} — gold source may be stale"
                )
    return warnings


async def main(output: Path, validate: bool, strict: bool) -> None:
    if validate:
        print("Validating expected_sources against the live Qdrant collection...")
        warnings = await _validate_against_qdrant(GOLD_SET)
        if warnings:
            print(f"\n{len(warnings)} WARNING(S):")
            for w in warnings:
                print(f"  WARN  {w}")
            if strict:
                print("\n--strict set: not writing gold file until warnings are resolved.")
                sys.exit(1)
        else:
            print(f"  All {len(GOLD_SET)} gold items validated OK — every expected_source exists in Qdrant.")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(GOLD_SET, indent=2), encoding="utf-8")
    print(f"\nWrote {len(GOLD_SET)} gold questions to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/eval/gold_qa.json"))
    parser.add_argument("--no-validate", action="store_true", help="Skip the live Qdrant check.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero and skip writing if validation warns.")
    args = parser.parse_args()
    asyncio.run(main(args.output, validate=not args.no_validate, strict=args.strict))
