"""Typed exceptions for the validation pipeline and agent. Technical Spec §8.1."""


class SQLAgentError(Exception):
    pass


class GraphRecursionError(Exception):
    """The ReAct loop hit MAX_SUPERSTEPS without reaching a stop condition.

    Named after LangGraph's exception on purpose: service/api.py puts
    type(exc).__name__ into the error envelope, so keeping the name keeps the
    HTTP response contract identical for callers that match on it.

    Deliberately NOT a subclass of SQLAgentError. LangGraph's version wasn't either,
    so it fell through api.ask()'s `except SQLAgentError` into the generic handler —
    which logs at ERROR ("ASK error"), not WARNING ("ASK failed"). Subclassing
    SQLAgentError would silently downgrade that log line and move the event to a
    different alerting path. The response body is identical either way; the log is not.
    """


class ParseError(SQLAgentError):
    pass


class StatementTypeError(SQLAgentError):
    pass


class TableNotAllowedError(SQLAgentError):
    pass


class ColumnBlockedError(SQLAgentError):
    pass


class InjectionDetectedError(SQLAgentError):
    pass


class RowCapExceededWarning(SQLAgentError):
    pass  # non-fatal, informational


class QueryTimeoutError(SQLAgentError):
    pass


class MaxRetriesError(SQLAgentError):
    pass


class AuthError(SQLAgentError):
    pass


class JoinNotAllowedError(SQLAgentError):
    pass  # retryable — a join predicate is not in the declared relationship graph


class ColumnNotInTableError(SQLAgentError):
    pass  # retryable — a qualified column does not belong to the table it is attached to


class AnswerMismatchError(SQLAgentError):
    pass  # retryable — generated SQL does not answer the question (answer judge)


class ExecutionCostError(SQLAgentError):
    pass  # hard reject — EXPLAIN plan too expensive / unbounded scan on a large table


# --- KG-constrained validation (checks #10-#12) ------------------------------------
# All four are RETRYABLE: each names the exact correction the generator needs (the right
# join keys, the right table for a column, the declared enum members, the fan-out risk),
# which is precisely the input the self-correction loop consumes. The first two subclass
# their non-KG counterparts so the existing RETRYABLE tuple already covers them and the
# retry semantics cannot drift apart.


class KGJoinNotAllowedError(JoinNotAllowedError):
    pass  # a join predicate does not match the KG edge's declared column pair(s)


class KGColumnUnknownError(ColumnNotInTableError):
    pass  # a qualified column does not exist on that table according to the KG


class KGTypeMismatchError(SQLAgentError):
    pass  # a literal compared to a column of an incompatible type, or outside its enum


class CardinalityRiskError(SQLAgentError):
    pass  # an aggregate would double-count across a one-to-many / many-to-many join
