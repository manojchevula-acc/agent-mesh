"""Metadata KG retrieval — fused signal ranking, node vectors, and join-path retrieval.

Implements the KG doc's §4.1 "Metadata KG Lookup" and "Join Path Retrieval". The strategy is
measured, not assumed (design §8):

  S1  template    regex entity id -> anchor column -> HAS_COLUMN
  S2  semantic    question -> :Term vectors -> DEFINES -> columns
  S3  exact       glossary regex + column names + enum values
  S4  lexical     question vs table NAME / search_terms / purpose   <- strongest signal
  S5  ranked      selector.ranked_core() — the EXISTING dense+BM25+RRF table ranking
  ---- fuse into one score per table, cut to kg_candidate_top_k ----
  S6  closure     +1 hop to BASE-table neighbours
  S7  joinpath    view-scope-filtered BFS -> exact ON keys

WHY FUSE RATHER THAN UNION. An earlier draft unioned every signal. That also reaches 100%
recall but at 17.6 of 21 candidates — worse precision than today and a ~10x planner prompt.
Scoring every table and cutting keeps the recall while handing the planner a SMALLER set than
it gets now (100% at ~10-12 candidates vs today's 86% at 10).

WHY S5 IS selector.ranked_core AND NOT A NEW INDEX. The existing selector already fuses dense
and BM25 with RRF over table documents. Re-implementing that inside the KG would lose the
sparse half and create a second retrieval system to keep in sync. Folding its ranking in as
one signal means there is ONE retrieval path, and the KG's output is the FINAL candidate set —
query_engine unions nothing on top of it.

NO :Column VECTOR INDEX. Measured at +0 recall on top of table-level retrieval (design §8) —
table documents already aggregate the same column descriptions. Only :Term and :Scenario
documents are embedded: ~44 vectors in one collection.

NO SIGNAL IS A GATE. Every signal contributes to a score; none can veto another. The exact
(regex) signal matches nothing at all on 46% of questions, so as a gate it would drop the
right table for nearly half of all traffic. As one weighted contributor it adds precision and
costs nothing.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from functools import lru_cache

from sql_agent.config import settings
from sql_agent.kg.client import get_kg_client
from sql_agent.kg.schema import ForeignKeyEdge
from sql_agent.kg.templates import match_template, run_template
from sql_agent.logging_config import get_logger
from sql_agent.semantic_layer.glossary import matched_terms

log = get_logger("kg.retrieval")

SIGNAL_TEMPLATE = "template"
SIGNAL_SEMANTIC = "semantic_term"
SIGNAL_EXACT = "exact_literal"
SIGNAL_LEXICAL = "lexical_scenario"
SIGNAL_RANKED = "table_ranking"
SIGNAL_CLOSURE = "join_closure"

_TERM_PREFIX = "term::"
_SCENARIO_PREFIX = "scen::"

_ENTITY_ANCHOR = {"CUST": "customer_id", "DEAL": "deal_id", "PROD": "product_id"}
_ENTITY_RE = re.compile(r"\b(CUST|DEAL|PROD)\d{2,}\b", re.IGNORECASE)

# Enum members shorter than this are too generic to match on ("AED" is fine, "Y" is not).
_MIN_ENUM_LEN = 3
# Words shorter than this are too common to serve as a table trigger.
_MIN_TRIGGER_LEN = 5


def _word(haystack: str, needle: str) -> bool:
    return re.search(rf"\b{re.escape(needle)}\b", haystack) is not None


@dataclass(frozen=True)
class KGLookup:
    """The KG's answer for one question — and the whole audit record of how it got there.

    ``attribution`` maps each surviving table to the signals that found it and ``scores`` to
    its fused score. Together they make the per-signal contribution measurable over time, so
    you can see which signal to invest in (design §10).
    """

    terms: list[str] = field(default_factory=list)
    definitions: dict[str, str] = field(default_factory=dict)
    columns: list[str] = field(default_factory=list)        # "table.column"
    tables: list[str] = field(default_factory=list)         # FINAL set, best-ranked first
    attribution: dict[str, list[str]] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)  # table -> fused score
    term_scores: dict[str, float] = field(default_factory=dict)  # term -> sim (exact = 1.0)
    join_edges: list[ForeignKeyEdge] = field(default_factory=list)
    template: str = ""
    params: dict = field(default_factory=dict)
    fingerprint: str = ""
    latency_ms: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.tables and not self.columns and not self.terms

    @property
    def signals_used(self) -> list[str]:
        return sorted({s for sigs in self.attribution.values() for s in sigs})

    def as_dict(self) -> dict:
        return {
            "signals": self.signals_used,
            "template": self.template,
            "params": self.params,
            "terms": self.terms,
            "definitions": self.definitions,
            "term_scores": {k: round(v, 4) for k, v in self.term_scores.items()},
            "columns": self.columns,
            "tables": self.tables,
            "attribution": self.attribution,
            "scores": {k: round(v, 4) for k, v in self.scores.items()},
            "join_edges": [
                {"from": e.from_table, "to": e.to_table, "on": e.on_clause(),
                 "cardinality": e.cardinality, "source": e.source}
                for e in self.join_edges
            ],
            "kg_fingerprint": self.fingerprint,
            "latency_ms": self.latency_ms,
        }

    def render_block(self) -> str:
        """The prompt block: resolved business vocabulary first (the disambiguation the
        generator most often gets wrong), then the validated join path.

        Deliberately terse — the rendered schema context already lists every column of every
        selected table. This block says what the question's BUSINESS VOCABULARY resolves to
        and how the tables legally connect, which a schema dump cannot say.
        """
        if self.is_empty:
            return ""
        client = get_kg_client()
        graph = client.snapshot() if client else None
        lines = ["KNOWLEDGE GRAPH — resolved from the governed schema metadata:"]
        if self.terms and graph is not None:
            lines.append("  Business terms in this question resolve to these columns:")
            for term in self.terms:
                cols = ", ".join(graph.columns_for_term(term)) or "(no governed column)"
                definition = self.definitions.get(term, "")
                lines.append(f"    - {term} -> {cols}"
                             + (f"  — {definition}" if definition else ""))
        if self.join_edges:
            lines.append("  Validated join path (these are the ONLY keys you may join on):")
            for edge in self.join_edges:
                lines.append(f"    - {edge.from_table} JOIN {edge.to_table} "
                             f"ON {edge.on_clause()}   [{edge.cardinality}]")
        if self.template:
            bound = ", ".join(f"{k}={v!r}" for k, v in self.params.items())
            lines.append(f"  Matched access pattern: {self.template}({bound})")
        return "\n".join(lines)


# --- Node vectors: :Term and :Scenario in one small collection ---------------------------


def _node_documents(graph) -> dict[str, str]:
    """One document per :Term and per :Scenario (a table viewed as "what it is FOR").

    A :Term document leans on ``definition``. Without one, a term embeds as roughly
    "gearing risk debt_to_equity_ratio" — a thin vector matching only near-verbatim phrasing,
    which is precisely the weakness this signal exists to fix (design §7.3). builder.py warns
    about terms that lack a definition.

    A :Scenario document is name + purpose + search_terms + grain — deliberately NOT the
    column list, which selector's table documents already cover via S5.
    """
    docs: dict[str, str] = {}
    for name, term in graph.terms.items():
        mapped = [c.rsplit(".", 1)[-1] for c in graph.columns_for_term(name)]
        docs[_TERM_PREFIX + name] = " ".join(
            p for p in [name, term.category, term.definition, *mapped] if p)
    for name, table in graph.tables.items():
        docs[_SCENARIO_PREFIX + name] = " ".join(
            p for p in [name.replace("_", " "), table.purpose, table.search_terms,
                        table.grain] if p)
    return docs


@lru_cache(maxsize=1)
def _node_index():
    """The built :Term + :Scenario vector index, or None when dense retrieval is off."""
    from sql_agent.semantic_layer.embeddings import get_backend
    from sql_agent.semantic_layer.vector_index import get_kg_node_index

    client, backend = get_kg_client(), get_backend()
    if client is None or backend is None:
        return None
    docs = _node_documents(client.snapshot())
    if not docs:
        return None
    names = list(docs)
    index = get_kg_node_index()
    index.build(names, backend.embed([docs[n] for n in names]))
    log.info("KG node index built | %d vector(s)", len(names))
    return index


def build_node_index(force: bool = False) -> int:
    """Build/refresh the node index; returns the vector count. Used by the build script."""
    if force:
        _node_index.cache_clear()
    if _node_index() is None:
        return 0
    client = get_kg_client()
    return len(_node_documents(client.snapshot())) if client else 0


def _node_hits(question: str) -> dict[str, float]:
    """Raw prefixed node hits (both :Term and :Scenario), unfiltered."""
    index = _node_index()
    if index is None:
        return {}
    from sql_agent.semantic_layer.embeddings import get_backend

    vector = get_backend().embed_query(question, prefix=settings.kg_embedding_query_prefix)
    k = settings.kg_term_top_k + settings.kg_scenario_top_k
    return {name: float(score) for name, score in index.search(vector, k)}


# --- Lexical surfaces --------------------------------------------------------------------


@lru_cache(maxsize=1)
def _literal_maps() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(bare column name -> owning tables, enum value -> owning tables)."""
    client = get_kg_client()
    if client is None:
        return {}, {}
    graph = client.snapshot()
    columns: dict[str, list[str]] = {}
    enums: dict[str, list[str]] = {}
    for col in graph.columns.values():
        columns.setdefault(col.name.lower(), []).append(col.table)
        for value in col.enum_values:
            if len(str(value)) >= _MIN_ENUM_LEN:
                enums.setdefault(str(value).lower(), []).append(col.table)
    return columns, enums


@lru_cache(maxsize=1)
def _lexical_triggers() -> dict[str, frozenset[str]]:
    """Per table, the words whose presence in a question implicates it — S4.

    Sources: the table NAME (with a naive singular/plural pair, so "deals" reaches
    historical_deals), plus every sufficiently distinctive word from ``purpose`` and
    ``search_terms``. Those two fields are the closest thing this schema has to a business
    scenario description, and matching them is the single strongest signal measured (§8).

    SCALE CAVEAT: at 21 tables, "any purpose word over five characters" is precise enough. At
    500 tables it would match nearly everything and would need IDF weighting or a curated
    trigger list. Revisit before this graph grows an order of magnitude.
    """
    client = get_kg_client()
    if client is None:
        return {}
    triggers: dict[str, frozenset[str]] = {}
    for name, table in client.snapshot().tables.items():
        words = set(re.split(r"[_\s]+", name.lower()))
        for word in list(words):
            words.add(word[:-1] if word.endswith("s") else word + "s")
        for text in (table.search_terms or "", table.purpose or ""):
            words |= {w for w in re.split(r"[^a-z]+", text.lower())
                      if len(w) > _MIN_TRIGGER_LEN}
        triggers[name] = frozenset(w for w in words if len(w) > 3)
    return triggers


def _exact_literals(question: str) -> tuple[set[str], set[str]]:
    """S3 — (tables, columns) whose NAME or ENUM VALUE appears verbatim in the question.

    Not paraphrase-sensitive the way a glossary term is: "Won", "Corporate", "SME",
    "product type" are literal tokens from the data domain that users genuinely type.
    """
    lower = question.lower()
    column_map, enum_map = _literal_maps()
    tables: set[str] = set()
    columns: set[str] = set()
    for name, owners in column_map.items():
        spaced = name.replace("_", " ")
        stripped = re.sub(r"_(pct|aed|years|ratio)$", "", name).replace("_", " ")
        if _word(lower, name) or _word(lower, spaced) or _word(lower, stripped):
            tables.update(owners)
            columns.update(f"{t}.{name}" for t in owners)
    for value, owners in enum_map.items():
        if _word(lower, value):
            tables.update(owners)
    return tables, columns


def _lexical_tables(question: str) -> set[str]:
    """S4 — tables whose name/purpose/search_terms vocabulary appears in the question."""
    lower = question.lower()
    return {name for name, words in _lexical_triggers().items()
            if any(_word(lower, w) for w in words)}


def _ranked_tables(question: str, tables_hint: list[str] | None) -> list[str]:
    """S5 — the EXISTING selector ranking (dense + BM25, RRF-fused), best first.

    Reused rather than reimplemented, so the sparse half is preserved and table-level
    retrieval stays tuned in exactly one place. Returns [] when retrieval is disabled or
    raises — the other four signals then carry the question on their own.
    """
    from sql_agent.semantic_layer.selector import ranked_core

    try:
        return ranked_core(question, tables_hint) or []
    except Exception as exc:  # noqa: BLE001 — one signal failing must not fail the lookup
        log.warning("KG S5 ranking unavailable | %s", exc)
        return []


# --- Join closure and join-path retrieval -------------------------------------------------


def kg_join_closure(tables: set[str], client=None) -> set[str]:
    """S6 — expand by one hop to BASE-table neighbours only.

    Worth ~25 points of recall on its own (design §8): the base lookup tables are frequently
    the answer but rank poorly, because the deal-grain views duplicate their descriptive
    columns. Restricted to non-view neighbours for the reason
    semantic_layer.loader.base_join_closure gives — customer_master neighbours most views, so
    a view-inclusive closure drags in the whole schema.
    """
    if client is None:
        client = get_kg_client()
    if client is None:
        return set(tables)
    graph = client.snapshot()
    views, adjacency = graph.views(), graph.adjacency()
    closed = set(tables)
    for table in list(tables):
        closed.update(n for n in adjacency.get(table, {}) if n not in views)
    return {t for t in closed if t in graph.tables}


def resolve_kg_joins(
    tables: set[str], wanted_pairs: list[tuple[str, str]] | None = None, client=None,
) -> tuple[list[str], set[frozenset[str]], set[str], list[ForeignKeyEdge]]:
    """S7 — the drop-in counterpart of semantic_layer.joins.resolve_joins.

    Same contract (clauses, allowed_pairs, used_tables) so query_engine can switch sources
    without changing shape, plus the typed edges for the audit record and the validator
    bundle. Paths are view-scope filtered by the client (check #9 — design §4.4).
    """
    if client is None:
        client = get_kg_client()
    if client is None:
        return [], set(), set(tables), []

    graph = client.snapshot()
    known = {t for t in tables if t in graph.tables}
    if wanted_pairs:
        pairs = [tuple(p) for p in wanted_pairs]
    else:
        ordered = sorted(known)
        pairs = [(a, b) for i, a in enumerate(ordered) for b in ordered[i + 1:]]

    clauses: list[str] = []
    allowed_pairs: set[frozenset[str]] = set()
    used_tables: set[str] = set(known)
    edges: list[ForeignKeyEdge] = []
    emitted: set[frozenset[str]] = set()

    for a, b in pairs:
        path = client.join_path(a, b)          # already view-scope filtered
        for left, right in zip(path, path[1:]):
            key = frozenset((left, right))
            if key in emitted:
                continue
            edge = graph.edge_between(left, right)
            if edge is None:
                continue
            clauses.append(f"{left} JOIN {right} ON ({edge.on_clause()})")
            allowed_pairs.add(key)
            emitted.add(key)
            used_tables.update((left, right))   # bridge tables enter the render set
            edges.append(edge)
    return clauses, allowed_pairs, used_tables, edges


# --- Fusion ---------------------------------------------------------------------------------


def _fuse(graph, columns, template_tables, term_tables, exact_tables, lexical_tables,
          rank_of, scenario_rank) -> dict[str, float]:
    """One score per table from all five signals.

    Coverage is normalised against the BEST candidate rather than an absolute count, so a
    question resolving two columns and one resolving eight are scored on the same scale.
    Rank signals decay as ``w / (smoothing + rank)`` — the same shape as the RRF the selector
    already uses, so a first-ranked table is worth meaningfully more than a tenth-ranked one
    without letting rank alone dominate the lexical and coverage evidence.

    WEIGHTS WERE TUNED ON THE EVAL GOLD SETS. They are config-driven precisely so they can be
    re-validated on held-out questions before the measured numbers are trusted forward
    (design §8, caveat 1).
    """
    by_table: dict[str, int] = {}
    for key in columns:
        table = key.rsplit(".", 1)[0]
        if table in graph.tables:
            by_table[table] = by_table.get(table, 0) + 1
    best = max(by_table.values(), default=0) or 1

    smoothing = settings.kg_rank_smoothing
    scores: dict[str, float] = {}
    for table in graph.tables:
        score = (
            settings.kg_weight_lexical * (table in lexical_tables)
            + settings.kg_weight_coverage * (by_table.get(table, 0) / best)
            + settings.kg_weight_exact * (table in exact_tables)
            + settings.kg_weight_term * (table in term_tables)
            + settings.kg_weight_template * (table in template_tables)
        )
        if table in rank_of:
            score += settings.kg_weight_rank / (smoothing + rank_of[table])
        if table in scenario_rank:
            score += settings.kg_weight_rank / (smoothing + scenario_rank[table])
        scores[table] = score
    return scores


# --- The lookup ------------------------------------------------------------------------------


def lookup(question: str, tables_hint: list[str] | None = None) -> KGLookup:
    """Resolve a question against the metadata KG.

    Never raises: any failure logs and returns an empty KGLookup, which every caller treats as
    "no KG grounding this turn" and proceeds on the existing semantic-layer path. A
    metadata-grounding layer that can fail a banking query is worse than no layer at all.
    """
    started = time.time()
    client = get_kg_client()
    if client is None:
        return KGLookup()
    try:
        return _lookup(question, tables_hint, client, started)
    except Exception as exc:  # noqa: BLE001 — grounding must never break the turn
        log.warning("KG lookup failed | %s | continuing without KG grounding", exc)
        return KGLookup(latency_ms=round((time.time() - started) * 1000))


def _lookup(question: str, tables_hint, client, started: float) -> KGLookup:
    graph = client.snapshot()
    attribution: dict[str, list[str]] = {}
    columns: set[str] = set()
    terms: list[str] = []
    term_scores: dict[str, float] = {}
    template_name, params = "", {}
    strategy = settings.kg_retrieval_strategy.strip().lower()

    def add(table: str, signal: str) -> None:
        if table in graph.tables:
            sigs = attribution.setdefault(table, [])
            if signal not in sigs:
                sigs.append(signal)

    # -- S1 TEMPLATE ------------------------------------------------------------------
    template_tables: set[str] = set()
    if strategy in ("auto", "template"):
        matched = match_template(question)
        if matched is not None:
            template, params = matched
            template_name = template.name
            for row in run_template(template, params, client):
                template_tables.add(row["table"])
                add(row["table"], SIGNAL_TEMPLATE)
            # The anchor column is itself a resolved column — the question filters on it.
            entity = _ENTITY_RE.search(question)
            anchor = _ENTITY_ANCHOR.get(entity.group(1).upper()) if entity else None
            if anchor:
                columns.update(f"{t}.{anchor}" for t in template_tables
                               if f"{t}.{anchor}" in graph.columns)

    # -- S2 SEMANTIC: :Term hits and the :Scenario ranking ----------------------------
    scenario_rank: dict[str, int] = {}
    if strategy in ("auto", "semantic"):
        scenario_hits: list[tuple[str, float]] = []
        for name, score in _node_hits(question).items():
            if name.startswith(_TERM_PREFIX):
                term = name[len(_TERM_PREFIX):]
                if term in graph.terms and score >= settings.kg_term_min_score:
                    if term not in terms:
                        terms.append(term)
                    term_scores[term] = max(term_scores.get(term, 0.0), score)
            elif name.startswith(_SCENARIO_PREFIX):
                table = name[len(_SCENARIO_PREFIX):]
                if table in graph.tables:
                    scenario_hits.append((table, score))
        scenario_hits.sort(key=lambda kv: -kv[1])
        for rank, (table, _) in enumerate(scenario_hits[:settings.kg_scenario_top_k]):
            scenario_rank[table] = rank
            add(table, SIGNAL_SEMANTIC)

    # -- S3 EXACT literals -------------------------------------------------------------
    exact_tables: set[str] = set()
    if strategy in ("auto", "exact", "template"):
        for term in matched_terms(question):
            if term in graph.terms:
                if term not in terms:
                    terms.append(term)
                term_scores[term] = 1.0            # an exact hit is a certainty
        literal_tables, literal_columns = _exact_literals(question)
        columns.update(literal_columns)
        exact_tables |= literal_tables
        for table in literal_tables:
            add(table, SIGNAL_EXACT)

    # -- DEFINES traversal --------------------------------------------------------------
    # Runs for BOTH the semantic and exact paths, and regardless of a template hit: a
    # template says which TABLES an entity question spans but nothing about which column
    # "policy margin" means. This is the disambiguation step.
    definitions: dict[str, str] = {}
    term_tables: set[str] = set()
    for term in terms:
        node = graph.terms.get(term)
        if node is not None and node.definition:
            definitions[term] = node.definition
        signal = SIGNAL_EXACT if term_scores.get(term) == 1.0 else SIGNAL_SEMANTIC
        for table, key in client.resolve_term(term):
            columns.add(key)
            term_tables.add(table)
            add(table, signal)

    # -- S4 LEXICAL scenario match -------------------------------------------------------
    lexical_tables = _lexical_tables(question) if strategy in ("auto", "exact") else set()
    for table in lexical_tables:
        add(table, SIGNAL_LEXICAL)

    # -- S5 the EXISTING table ranking ---------------------------------------------------
    ranking = _ranked_tables(question, tables_hint)
    rank_of = {t: r for r, t in enumerate(ranking)}
    for table in ranking:
        add(table, SIGNAL_RANKED)
    for table in tables_hint or []:                # advisory, additive, never a filter
        add(table, SIGNAL_EXACT)

    # -- FUSE and CUT ---------------------------------------------------------------------
    scores = _fuse(graph, columns, template_tables, term_tables, exact_tables,
                   lexical_tables, rank_of, scenario_rank)
    ordered = sorted(attribution, key=lambda t: -scores.get(t, 0.0))
    cut = settings.kg_candidate_top_k
    selected = ordered[:cut] if cut and cut > 0 else ordered   # 0 => uncapped

    # -- S6 CLOSURE + S7 JOIN PATHS --------------------------------------------------------
    final = set(selected)
    if final and settings.kg_join_closure_enabled:
        for table in kg_join_closure(final, client) - final:
            add(table, SIGNAL_CLOSURE)
            final.add(table)

    join_edges: list[ForeignKeyEdge] = []
    if len(final) > 1:
        _, _, used, join_edges = resolve_kg_joins(final, client=client)
        for table in used - final:
            add(table, SIGNAL_CLOSURE)             # bridge tables the path required
            final.add(table)

    result = KGLookup(
        terms=terms,
        definitions=definitions,
        columns=sorted(c for c in columns if c in graph.columns),
        tables=sorted(final, key=lambda t: -scores.get(t, 0.0)),
        attribution={t: attribution[t] for t in final if t in attribution},
        scores={t: round(scores.get(t, 0.0), 4) for t in final},
        term_scores=term_scores,
        join_edges=join_edges,
        template=template_name,
        params=params,
        fingerprint=graph.fingerprint,
        latency_ms=round((time.time() - started) * 1000),
    )
    log.info("KG lookup | signals=%s terms=%s scored=%d cut=%d final=%d edges=%d | %dms",
             result.signals_used or "none", result.terms or "none", len(ordered),
             len(selected), len(result.tables), len(result.join_edges), result.latency_ms)
    return result


def reset_kg_caches() -> None:
    """Clear every per-process KG cache (tests, and after a rebuild)."""
    from sql_agent.kg.client import reset_kg_client

    _literal_maps.cache_clear()
    _lexical_triggers.cache_clear()
    _node_index.cache_clear()
    reset_kg_client()


__all__ = ["KGLookup", "SIGNAL_CLOSURE", "SIGNAL_EXACT", "SIGNAL_LEXICAL", "SIGNAL_RANKED",
           "SIGNAL_SEMANTIC", "SIGNAL_TEMPLATE", "build_node_index", "kg_join_closure",
           "lookup", "reset_kg_caches", "resolve_kg_joins"]
