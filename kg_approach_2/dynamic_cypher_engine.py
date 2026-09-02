import os
import time
from dotenv import load_dotenv

load_dotenv()

from cypher_validator import validate_cypher
from groq import Groq
from neo4j import GraphDatabase

from schema import SCHEMA


# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────

client = Groq(api_key=os.environ["GROQ_API_KEY"])

URI = "bolt://localhost:7687"

USERNAME = os.environ["NEO4J_USERNAME"]
PASSWORD = os.environ["NEO4J_PASSWORD"]

DATABASE = "neo4j"

driver = GraphDatabase.driver(
    URI,
    auth=(USERNAME, PASSWORD)
)


# ─────────────────────────────────────────────
# Clean generated Cypher
# ─────────────────────────────────────────────

def clean_cypher(text):

    text = text.strip()

    # Remove markdown code fences
    if text.startswith("```"):

        lines = text.splitlines()

        if lines[0].strip().startswith("```"):
            lines = lines[1:]

        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]

        text = "\n".join(lines)

    # Remove unnecessary "CYPHER READ ONLY"
    lines = text.splitlines()

    if lines and lines[0].strip().upper() == "CYPHER READ ONLY":
        lines = lines[1:]

    return "\n".join(lines).strip()


# ─────────────────────────────────────────────
# Groq retry / rate-limit handling
# ─────────────────────────────────────────────

def groq_completion_with_retry(messages, max_retries=2):

    for attempt in range(max_retries + 1):

        try:

            return client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=messages,
                temperature=0
            )

        except Exception as e:

            error_text = str(e)

            # Retry only for Groq rate-limit errors
            if "429" not in error_text and "rate_limit" not in error_text.lower():
                raise

            if attempt >= max_retries:
                raise

            wait_time = 1.0 * (attempt + 1)

            print(
                f"Groq rate limit detected. "
                f"Retrying in {wait_time:.1f}s..."
            )

            time.sleep(wait_time)


# ─────────────────────────────────────────────
# Main dynamic Cypher engine
# ─────────────────────────────────────────────

def ask_question(question):

    max_attempts = 3

    # Total request timer
    total_start = time.perf_counter()

    # Initial prompt
    current_prompt = f"""
You are a Neo4j Cypher query generator.

Use ONLY the schema provided below:

{SCHEMA}

User question:
{question}

Rules:
- Generate a READ-ONLY Cypher query.
- Use only existing nodes, properties and relationships in the schema.
- Do not invent labels, properties or relationships.
- Return ONLY the Cypher query.
- Do not use markdown code fences.
- Do not write "CYPHER READ ONLY".
- If a numeric calculation or aggregation is required, make sure the values are converted to numbers when necessary.
- When filtering text values such as severity, status, type or category, handle capitalization differences safely.
- Return only the properties and calculated values required to answer the user's question.
- Always use clear aliases for returned fields, for example:
  d.deal_id AS deal_id, c.customer_id AS customer_id.
- Never return fields using qualified names such as d.deal_id or c.customer_id without an alias.
- Do not RETURN entire nodes or relationships when specific properties are sufficient.
- Avoid returning unnecessary properties.
- For list questions, return concise identifying fields such as IDs and names.
- For aggregation questions, return only the grouping field and calculated value.
- For ranking questions, return only the ranked entity and ranking/calculated value.
- Use the exact property names and meanings defined in the schema.
- Do not substitute a semantically different property for the property required by the question.
- For severity-based questions, use the PolicyException severity property defined in the schema.
"""

    cypher = None

    # ─────────────────────────────────────────
    # Step 1: Generate + validate Cypher
    # ─────────────────────────────────────────

    for attempt in range(max_attempts):

        # Start Cypher-generation timer
        cypher_start = time.perf_counter()

        response = groq_completion_with_retry(
            messages=[
                {
            "role": "user",
            "content": current_prompt
                }
            ]
        )

        cypher = clean_cypher(
            response.choices[0].message.content
        )

        print(
            f"Groq Cypher generation: "
            f"{time.perf_counter() - cypher_start:.2f}s"
        )

        if validate_cypher(cypher):

            print(
                f"CypherValidation : PASSED "
                f"(Attempt {attempt + 1})"
            )

            break

        print(
            f"CypherValidation : FAILED "
            f"(Attempt {attempt + 1})"
        )

        if attempt < max_attempts - 1:

            current_prompt = f"""
The Cypher query you generated is invalid.

Previous query:
{cypher}

Correct the query using ONLY this schema:

{SCHEMA}

User question:
{question}

Rules:
- Generate a READ-ONLY Cypher query.
- Use only existing nodes, properties and relationships.
- Do not invent labels, properties or relationships.
- Do not use markdown code fences.
- Do not write "CYPHER READ ONLY".
- Return ONLY the corrected Cypher query.
"""

    if not cypher or not validate_cypher(cypher):

        raise ValueError(
            "Could not generate a valid Cypher query."
        )

    print("\nGenerated Cypher:")
    print(cypher)

    # ─────────────────────────────────────────
    # Step 2: Execute + self-correct
    # ─────────────────────────────────────────

    records = None

    for attempt in range(max_attempts):

        try:

            # Start Neo4j timer
            neo4j_start = time.perf_counter()

            with driver.session(database=DATABASE) as session:

                result = session.run(cypher)

                records = [
                    dict(record)
                    for record in result
                ]

                MAX_RESULTS = 100

                if len(records) > MAX_RESULTS:
                    records = records[:MAX_RESULTS]

            print(
                f"Neo4j execution: "
                f"{time.perf_counter() - neo4j_start:.2f}s"
            )

            print(
                f"Neo4j Execution : PASSED "
                f"(Attempt {attempt + 1})"
            )

            break

        except Exception as e:

            neo4j_error = str(e)

            print(
                f"Neo4j Execution : FAILED "
                f"(Attempt {attempt + 1})"
            )

            print("Neo4j Error Type:", type(e).__name__)
            print("Neo4j Error:", neo4j_error)

            if attempt < max_attempts - 1:

                correction_prompt = f"""
The Cypher query below failed when executed against Neo4j.

User question:
{question}

Previous Cypher:
{cypher}

Neo4j error:
{neo4j_error}

Use ONLY this schema:

{SCHEMA}

Correct the Cypher query so that it:

- Is valid Neo4j Cypher.
- Answers the user's question.
- Is READ-ONLY.
- Uses only the provided nodes, properties and relationships.
- Handles the Neo4j error shown above.
- If a numeric property is stored as a string, convert it
  to a numeric value before SUM, AVG or other numeric calculations.
- When filtering text values such as severity, status, type or category, handle capitalization differences safely.
- Does not use markdown code fences.
- Does not write "CYPHER READ ONLY".
- Returns ONLY the corrected Cypher query.
"""

                # Start correction LLM timer
                correction_start = time.perf_counter()

                response = groq_completion_with_retry(
                    messages=[
                        {
                        "role": "user",
                        "content": correction_prompt
                        }
                    ]
                )

                cypher = clean_cypher(
                    response.choices[0].message.content
                )

                print(
                    f"Groq correction generation: "
                    f"{time.perf_counter() - correction_start:.2f}s"
                )

                if validate_cypher(cypher):

                    print(
                        f"CypherValidation : PASSED "
                        f"(Correction Attempt {attempt + 2})"
                    )

                else:

                    print(
                        f"CypherValidation : FAILED "
                        f"(Correction Attempt {attempt + 2})"
                    )

    if records is None:

        raise RuntimeError(
            "Could not execute a valid Cypher query after 3 attempts."
        )

    # ─────────────────────────────────────────
    # Step 3: Generate business answer
    # ─────────────────────────────────────────

    print("\nNeo4j Result:")

    for record in records:
        print(record)

    answer_prompt = f"""
Answer the user's question using ONLY the results provided below.

User question:
{question}

Results:
{records}

Give a short, clear business answer.

Rules:
- Answer ONLY from the returned records.
- Treat the Results section as authoritative.
- If records are present, use their values directly.
- Never say information is unavailable when the requested information exists in the records.
- For list questions, list the relevant returned values.
- For ranking questions, preserve the ranking shown in the results.
- For calculation questions, use the calculated values from the results.
- Do not reinterpret, replace, or ignore returned values.
- Do not invent information.
- If zero records are returned, clearly state that no matching records were found.
- Do not mention Cypher, Neo4j, or the LLM.
"""

    # Start final answer timer
    answer_start = time.perf_counter()

    answer_response = groq_completion_with_retry(
        messages=[
            {
            "role": "user",
            "content": answer_prompt
            }
        ]
    )

    answer = answer_response.choices[0].message.content

    print(
        f"Groq business answer: "
        f"{time.perf_counter() - answer_start:.2f}s"
    )

    # Total time
    print(
        f"TOTAL response time: "
        f"{time.perf_counter() - total_start:.2f}s"
    )

    return {
        "question": question,
        "cypher": cypher,
        "records": records,
        "answer": answer
    }