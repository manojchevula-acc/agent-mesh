import os
from groq import Groq

client = Groq(api_key=os.environ["GROQ_API_KEY"])

schema = """
Neo4j Schema:

Nodes:
Customer(customer_id)
Deal(deal_id, customer_id, product_id)
Product(product_id, product_name, product_type)
PolicyException(deal_id, rule_id, policy_id)
BusinessRule(rule_id, rule_version)

Relationships:
Customer -[:HAS_DEAL]-> Deal
Deal -[:FOR_PRODUCT]-> Product
Deal -[:HAS_POLICY_EXCEPTION]-> PolicyException
PolicyException -[:CAUSED_BY]-> BusinessRule
"""

question = "Which deals are associated with Customer CUST001?"

prompt = f"""
You are a Neo4j Cypher query generator.

Use ONLY the following schema:

{schema}

User question:
{question}

Rules:
- Generate a READ-ONLY Cypher query.
- Use only existing nodes, properties and relationships.
- Do not invent schema elements.
- Return only the Cypher query.
"""

response = client.chat.completions.create(
    model="openai/gpt-oss-120b",
    messages=[
        {"role": "user", "content": prompt}
    ],
    temperature=0
)

cypher = response.choices[0].message.content

print("Generated Cypher:")
print(cypher)