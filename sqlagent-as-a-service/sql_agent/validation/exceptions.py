"""Typed exceptions for the validation pipeline and agent. Technical Spec §8.1."""


class SQLAgentError(Exception):
    pass


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
