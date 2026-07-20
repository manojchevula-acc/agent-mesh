"""Query engine — parameterised / clause-builder / dynamic.

Parameterised and semi-dynamic queries are assembled inside their tools (Sections 4-5).
This module owns the tier-3 DYNAMIC path: generate SQL from the semantic layer, run the
six-check validator, and — on a retryable failure — self-correct, bounded to
``max_self_correction_attempts`` (Design Document §7.1, Technical Spec §9.2 / §11).
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from sql_agent.agent.prompts import (
    DYNAMIC_SQL_GENERATION_PROMPT,
    SELF_CORRECTION_PROMPT,
)
from sql_agent.config import settings
from sql_agent.db import db
from sql_agent.db.dialect import dialect_label, dialect_notes
from sql_agent.formatting import format_error, format_response
from sql_agent.llm import Step, get_llm, log_usage
from sql_agent.logging_config import get_logger
from sql_agent.memory import relevant_examples, render_examples_block
from sql_agent.routing.entity_resolver import resolve_customer_hint
from sql_agent.semantic_layer.glossary import matched_terms, render_glossary_block
from sql_agent.semantic_layer.joins import resolve_joins
from sql_agent.semantic_layer.loader import join_closure
from sql_agent.semantic_layer.renderer import render_schema_context
from sql_agent.semantic_layer.schema_link import link_schema
from sql_agent.semantic_layer.selector import select_tables
from sql_agent.validation.answer_validator import judge_sql
from sql_agent.validation.exceptions import (
    ColumnBlockedError,
    ColumnNotInTableError,
    ExecutionCostError,
    InjectionDetectedError,
    JoinNotAllowedError,
    MaxRetriesError,
    ParseError,
    StatementTypeError,
    TableNotAllowedError,
)

log = get_logger("query")

# Retryable validator failures engage the self-correction loop; everything else is a
# hard reject (Design Document §7.1). JoinNotAllowedError and ColumnNotInTableError are
# retryable — the model can re-emit using a declared relationship / the correct table.
RETRYABLE = (ParseError, TableNotAllowedError, JoinNotAllowedError, ColumnNotInTableError)
HARD_REJECT = (
    StatementTypeError, ColumnBlockedError, InjectionDetectedError, ExecutionCostError,
)


def _generate_sql(prompt: str, step: Step) -> str:
    """Generate SQL using the model configured for this step.

    First attempt uses the GENERATION model; self-correction retries use the
    CORRECTION model (both configurable per provider/model in config).
    """
    response = get_llm(step).invoke(prompt)
    log_usage(step, response)
    text = response.content if hasattr(response, "content") else str(response)
    # Model is instructed to return ONLY SQL, but strip any stray fences defensively.
    return text.strip().strip("`").removeprefix("sql").strip()


def _plan_schema(question: str, tables_hint: list[str] | None):
    """Scoped-schema-retrieval front end for the dynamic tier (Component B).

    Returns (schema_context, join_clauses, allowed_join_pairs, planned_tables) —
    ``planned_tables`` is the schema-link plan's chosen table set (plus join bridges),
    passed to the example ranker as its table signal. When schema retrieval is disabled
    this is byte-identical to the original behaviour: full schema, no join hints, no
    join constraint (allowed_join_pairs is None so validator check #7 is skipped), and
    no planned tables (the ranker falls back to the pre-flight tables_hint).
    """
    if not settings.schema_retrieval_enabled:
        return render_schema_context(), [], None, None

    # 1. RETRIEVE candidates (index lookup, no LLM) -> 2. SCHEMA-LINK plan (planner LLM).
    # Feed the planner the ranked candidates WITHOUT the join-closure expansion: the plan
    # only picks among ranked tables, and rendering the full closure (customer_master
    # neighbours nearly every view) can exceed the provider's per-minute token limit. Any
    # bridge the plan needs is re-added by resolve_joins below.
    candidates = select_tables(question, tables_hint, apply_closure=False)
    plan = link_schema(question, candidates)
    # 3. JOIN-RESOLVE minimal path (+ bridge tables) with real keys from the graph.
    join_clauses, allowed_pairs, used_tables = resolve_joins(plan.tables, plan.join_pairs)
    # 4. RENDER only the plan's tables plus any bridge tables the join path needs.
    schema_context = render_schema_context(tables=used_tables)
    return schema_context, join_clauses, allowed_pairs, sorted(used_tables)


def _widen_schema(question: str, tables_hint: list[str] | None):
    """Bounded widen for a self-correction retry.

    Re-slices the SAME already-computed retrieval ranking (selector.py) to
    ``schema_retrieval_widen_top_k`` candidates instead of falling back to literally
    every table/view in the schema — rendering the full schema is the single largest
    prompt component and was blowing the provider's per-minute token budget on retries.
    No extra LLM call: schema-link is deliberately skipped here (a little precision
    traded for materially fewer tokens on what is already a retry). join_closure is
    still applied so a widened join target never ends up missing its bridge table.
    """
    candidates = select_tables(question, tables_hint, top_k=settings.schema_retrieval_widen_top_k)
    used_tables = join_closure(candidates)
    schema_context = render_schema_context(tables=used_tables)
    return schema_context, [], None, sorted(used_tables)


def run_dynamic_pipeline(question: str, tables_hint: list[str] | None = None) -> dict:
    """Retrieve -> link -> resolve joins -> render -> generate -> validate ->
    (self-correct) -> execute -> judge -> format, for tier-3.

    The validator is the hard gate; generation quality is grounded in the semantic
    layer, never invented relationships.
    """
    schema_context, join_clauses, allowed_pairs, planned_tables = _plan_schema(question, tables_hint)

    # Resolve any customer NAME in the question to its id up front (mirrors the view
    # tools' _resolve_customer_id) so the generator filters on the id. Computed once and
    # reused across self-correction retries. Empty string => no-op on the template.
    entity_hint = (
        resolve_customer_hint(question) if settings.dynamic_entity_resolution_enabled else ""
    )
    entity_block = f"\n{entity_hint}" if entity_hint else ""

    def _join_hints_block(joins: list[str]) -> str:
        if not joins:
            return ""
        return "\n\nJOIN HINTS — use ONLY these declared relationships:\n" + "\n".join(joins) + "\n"

    def _glossary_block_for(q: str) -> str:
        # Business glossary (Phase 11): only the terms actually present in THIS
        # question, so the block stays short and doesn't pad the prompt with
        # irrelevant vocabulary. "" when nothing matches -> no-op on the template.
        terms = matched_terms(q)
        log.info("GLOSSARY block | matched_terms=%s", terms or "none")
        block = render_glossary_block(terms) if terms else ""
        return f"\n\n{block}\n" if block else ""

    def _examples_block_for(q: str) -> str:
        # Intent-aware few-shot (Pattern Retriever): the most RELEVANT approved worked
        # examples for THIS question. The table signal is the schema-link PLANNER's
        # chosen tables (planned_tables — the most precise "which tables does this
        # question touch" available, and it exists even when the pre-flight intent
        # classifier abstained) merged with the pre-flight tables_hint. planned_tables
        # is read at CALL time, so a widened retry re-ranks examples against the
        # widened table set automatically. No-op when examples are disabled.
        if not settings.examples_enabled:
            return ""
        hint = list(dict.fromkeys([*(planned_tables or []), *(tables_hint or [])]))
        block = render_examples_block(
            relevant_examples(q, tier="full_dynamic", tables_hint=hint or None)
        )
        return f"\n\n{block}\n" if block else ""

    def _base_prompt(schema: str, joins: list[str]) -> str:
        # The generator is told which engine it targets, so it writes correct SQL. Join
        # hints, the business glossary, and few-shot examples are rendered BEFORE the
        # question (Phase 11 prompt ordering) rather than appended after formatting, so
        # the model sees "how to answer" immediately ahead of "what to answer".
        return DYNAMIC_SQL_GENERATION_PROMPT.format(
            schema_context=schema, question=question,
            dialect=dialect_label(), dialect_notes=dialect_notes(),
            join_hints_block=_join_hints_block(joins),
            glossary_block=_glossary_block_for(question),
            examples_block=_examples_block_for(question),
            entity_block=entity_block,
        )

    prompt = _base_prompt(schema_context, join_clauses)
    max_attempts = settings.max_self_correction_attempts
    previous_sql = ""
    last_error = "unknown"
    # True until we've widened the schema slice at least once. A CANNOT_ANSWER verdict
    # against the original retrieval-narrowed slice (Component B) may simply mean the
    # slice is missing the table/view that actually covers the question, not that no
    # coverage exists at all — so the first such verdict earns one bounded widen-and-
    # retry, same as a validator/DB error does below. A second CANNOT_ANSWER, now
    # against the widened slice, is trusted as a genuine "no coverage" verdict.
    narrowed = settings.schema_retrieval_enabled
    # A validator-clean result the judge was not satisfied with. It is the best-effort
    # answer to fall back to if self-correction exhausts — an answer-alignment doubt must
    # never turn a safe, executable result into a hard failure.
    best_effort = None
    best_effort_reason = ""

    log.info("DYNAMIC pipeline start | question=%r", question)
    for attempt in range(1, max_attempts + 1):
        # First attempt uses the GENERATION model; retries use the CORRECTION model.
        step = Step.GENERATION if attempt == 1 else Step.CORRECTION
        sql = _generate_sql(prompt, step)
        log.info("DYNAMIC attempt %d/%d | step=%s | sql=%s",
                 attempt, max_attempts, step.value, sql)

        if sql.startswith("-- CANNOT_ANSWER"):
            reason = sql.split(":", 1)[-1].strip() if ":" in sql else "out of allowed schema"
            if narrowed:
                log.info("DYNAMIC cannot-answer on narrowed schema | widening "
                         "(bounded) and retrying | reason=%s", reason)
                schema_context, join_clauses, allowed_pairs, planned_tables = _widen_schema(question, tables_hint)
                narrowed = False
                prompt = _base_prompt(schema_context, join_clauses)
                continue
            log.info("DYNAMIC cannot answer | reason=%s", reason)
            return format_error("ValidationError",
                                f"Cannot answer from allowed schema: {reason}",
                                retryable=False, attempts_used=attempt)

        try:
            # db.execute runs the safety validator (join-graph #7 + column-binding #8)
            # before touching the DB. allowed_pairs is None when retrieval is off -> #7
            # skipped; strict_columns=True enables #8 for this LLM-generated SQL.
            result = db.execute(sql, allowed_join_pairs=allowed_pairs, strict_columns=True)
        except HARD_REJECT as exc:
            log.warning("DYNAMIC hard reject | %s: %s", type(exc).__name__, exc)
            return format_error(type(exc).__name__, str(exc),
                                retryable=False, attempts_used=attempt)
        except RETRYABLE as exc:
            log.warning("DYNAMIC retryable | %s: %s | self-correcting",
                        type(exc).__name__, exc)
            previous_sql, last_error = sql, str(exc)
            # Widen (bounded — see _widen_schema) so an under-selection cannot starve
            # the fix; drop the narrow join constraint accordingly (invented joins are
            # still rejected by the table whitelist). Append the validator's exact
            # error and regenerate.
            schema_context, join_clauses, allowed_pairs, planned_tables = _widen_schema(question, tables_hint)
            narrowed = False
            prompt = (
                _base_prompt(schema_context, join_clauses)
                + "\n\n"
                + SELF_CORRECTION_PROMPT.format(
                    previous_sql=previous_sql, error_message=last_error
                )
            )
            continue
        except SQLAlchemyError as exc:
            # The SQL passed the safety validator but the DATABASE rejected it at execution
            # (e.g. a column referenced on the wrong table -> "Unknown column 'hd.x'"). The
            # validator only checks the global column allow-list, not column-to-table
            # binding, so these surface here. Feed the DB's own error back for a bounded
            # self-correction instead of failing hard. Widened (bounded) schema so the
            # model can see which table actually owns the column.
            db_error = str(getattr(exc, "orig", exc))
            log.warning("DYNAMIC db-exec error | %s | self-correcting", db_error)
            previous_sql, last_error = sql, db_error
            schema_context, join_clauses, allowed_pairs, planned_tables = _widen_schema(question, tables_hint)
            narrowed = False
            prompt = (
                _base_prompt(schema_context, join_clauses)
                + "\n\n"
                + SELF_CORRECTION_PROMPT.format(
                    previous_sql=previous_sql, error_message=last_error
                )
            )
            continue

        # Answer-alignment judge (Component C). Shadow-first: judge_sql is a no-op pass
        # when disabled; only a confident mismatch under enforcement triggers a retry.
        columns = list(result.rows[0].keys()) if result.rows else []
        verdict = judge_sql(question, result.sql, columns, len(result),
                            schema_context=schema_context)
        if settings.answer_validation_enforced and not verdict.passes:
            log.warning("DYNAMIC answer mismatch | %s | self-correcting", verdict.reason)
            previous_sql, last_error = result.sql, f"answer mismatch: {verdict.reason}"
            # Remember this validator-clean result: if no attempt satisfies the judge, we
            # return this rather than raising, so a semantic doubt degrades to a caveated
            # answer instead of a hard error.
            best_effort, best_effort_reason = result, verdict.reason
            prompt = (
                _base_prompt(schema_context, join_clauses)
                + "\n\n"
                + SELF_CORRECTION_PROMPT.format(
                    previous_sql=previous_sql, error_message=last_error
                )
            )
            continue

        # format_response now surfaces `sql` for every tier (from the QueryResult),
        # so the dynamic envelope already carries the validated SELECT the auditor logs.
        response = format_response(result, tier="full_dynamic", tool="analytical_query")
        log.info("DYNAMIC success | attempt=%d | rows=%d", attempt, len(result))
        return response

    # Exhausted the bounded self-correction loop. If the only thing standing in the way was
    # the answer-alignment judge (not a safety/parse/DB failure), we still have a clean,
    # executable result — return it with a caveat rather than failing the whole turn. A hard
    # error is reserved for when NO attempt produced a validator-clean result.
    if best_effort is not None:
        log.warning("DYNAMIC judge-unsatisfied | returning best-effort result | %s",
                    best_effort_reason)
        return format_response(
            best_effort, tier="full_dynamic", tool="analytical_query",
            notice=("Answer-alignment check was not fully satisfied "
                    f"({best_effort_reason}); returning the best available result."),
        )

    log.error("DYNAMIC exhausted | %d attempts | last_error=%s", max_attempts, last_error)
    raise MaxRetriesError(
        f"analytical_query failed after {max_attempts} attempts. Last error: {last_error}"
    )
