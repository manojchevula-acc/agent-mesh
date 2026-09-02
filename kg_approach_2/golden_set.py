GOLDEN_SET = [

    {
        "id": "GS001",
        "question": "Which products have more than 5 deals, and how many deals does each have?",
        "category": "aggregation",
        "expected_business_result": [
            "Term Loan",
            "Corporate Deposit",
            "Working Capital Loan",
            "FX Forward Facility",
            "Trade Finance LC"
        ]
    },

    {
        "id": "GS002",
        "question": "Which business rule is associated with the most policy exceptions?",
        "category": "aggregation",
        "expected_business_result": "RULE_001 / 1.0.0 / 56 exceptions"
    },

    {
        "id": "GS003",
        "question": "Which customers have the largest total margin shortfall from policy exceptions?",
        "category": "aggregation + numeric calculation",
        "expected_business_result": [
            "CUST012",
            "CUST016",
            "CUST009"
        ]
    },

    {
        "id": "GS004",
        "question": "Which products are used by customers who have policy exceptions?",
        "category": "multi-hop traversal",
        "expected_business_result": [
            "Working Capital Loan",
            "Term Loan",
            "Trade Finance LC",
            "Invoice Discounting",
            "Overdraft Facility",
            "USD Term Loan",
            "FX Forward Facility",
            "Corporate Deposit"
        ]
    },

    {
        "id": "GS005",
        "question": "Which deals have critical policy exceptions?",
        "category": "filtering",
        "expected_business_result": "Deals with critical policy exceptions"
    },

    {
        "id": "GS006",
        "question": "Which customers have more than one policy exception?",
        "category": "aggregation + filtering",
        "expected_business_result": "Customers with multiple policy exceptions"
    },

    {
        "id": "GS007",
        "question": "Which products have no policy exceptions?",
        "category": "negative relationship",
        "expected_business_result": "Products without policy exceptions"
    },

    {
        "id": "GS008",
        "question": "What percentage of deals have policy exceptions?",
        "category": "calculation",
        "expected_business_result": "Percentage of deals with policy exceptions"
    },

    {
        "id": "GS009",
        "question": "Which customer has the most deals and what products are involved?",
        "category": "multi-hop + aggregation",
        "expected_business_result": "Customer with highest deal count and associated products"
    },

    {
        "id": "GS010",
        "question": "Which product type has the highest number of deals?",
        "category": "aggregation + ranking",
        "expected_business_result": "Product type with highest deal count"
    },


    {
        "id": "GS011",
        "question": "Which deals have standard policy exceptions?",
        "category": "filtering",
        "expected_business_result": "Deals with STANDARD policy exceptions"
    },

    {
        "id": "GS012",
        "question": "Which customers have no policy exceptions?",
        "category": "negative relationship",
        "expected_business_result": "Customers without policy exceptions"
    },

    {
        "id": "GS013",
        "question": "How many policy exceptions does each product have?",
        "category": "aggregation",
        "expected_business_result": "Policy exception count by product"
    },

    {
        "id": "GS014",
        "question": "Which customers have at least 3 policy exceptions?",
        "category": "aggregation + filtering",
        "expected_business_result": "Customers with 3 or more policy exceptions"
    },

    {
        "id": "GS015",
        "question": "What is the average margin shortfall from policy exceptions?",
        "category": "numeric calculation",
        "expected_business_result": "Average margin shortfall"
    },

    {
        "id": "GS016",
        "question": "Which business rules have more than 10 policy exceptions?",
        "category": "aggregation + filtering",
        "expected_business_result": "Business rules with more than 10 exceptions"
    },

    {
        "id": "GS017",
        "question": "Which products have critical policy exceptions?",
        "category": "multi-hop + filtering",
        "expected_business_result": "Products associated with critical policy exceptions"
    },

    {
        "id": "GS018",
        "question": "How many deals does each customer have?",
        "category": "aggregation",
        "expected_business_result": "Deal count by customer"
    },

    {
        "id": "GS019",
        "question": "Which policy exceptions have a margin shortfall greater than 1?",
        "category": "numeric filtering",
        "expected_business_result": "Policy exceptions with margin shortfall greater than 1"
    },

    {
        "id": "GS020",
        "question": "Which customers use more than one product?",
        "category": "multi-hop + aggregation",
        "expected_business_result": "Customers associated with multiple products"
    }
]