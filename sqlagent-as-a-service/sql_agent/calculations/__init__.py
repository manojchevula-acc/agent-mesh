"""Calculation module — pure banking formulas. Zero SQL, zero LLM calls.

The LLM may decide WHICH attribute to compute and on WHICH rows, but the arithmetic
itself is always executed by this deterministic, unit-tested code. An LLM must never
be the calculator for a regulated number (Design Document §5 hard rule).
"""
