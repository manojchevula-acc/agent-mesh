"""Schema-aware semantic equivalence — the same entity named two different ways.

THE PROBLEM THIS SOLVES
-----------------------
A purely syntactic result comparison (result_metrics.py) marks these two answers as a
MISMATCH:

    gold : [{product_name: "Term Loan", won_deals: 11, ...}, ...]
    agent: [{product_id:   "PROD002",   deal_count: 11, ...}, ...]

...even though they are the SAME breakdown — the agent just returned product_id where gold
returned product_name (and rows in a different order). This is the case the LLM judge can
see ("EQUIVALENT — different label for the same entity") and the syntactic metrics cannot.
This module closes that gap deterministically.

TWO WAYS TO PROVE IT
--------------------
1. STRUCTURAL (no database needed — the primary path).
   product_id and product_name are BOTH unique keys of product_master, so each is a
   bijection with the entity. If we set the entity column aside and EVERY OTHER column
   matches row-for-row — and those other columns are distinct enough to tag each row
   uniquely — then matching them proves the rows describe the same entities, whatever the
   label. In the example above, (won_deals, won_volume, margin) is a per-product signature:
   two results carrying the same signatures describe the same products by construction.
   This needs no lookup table and works fully offline.

2. LOOKUP-BASED (uses the DB — fallback for when there is no other column to lean on).
   When the entity column is essentially the whole answer (e.g. "list the product names"),
   there is no signature to match on, so structural proof is impossible. Here we resolve
   ids to names through product_master and compare the resolved rows. If the DB is absent,
   this path reports `decidable=False` — never a false EQUIVALENT.

It handles the same pattern for any registered (id, name) pair: customer_id/customer_name,
product_id/product_name, and any others added to LOOKUPS. It is conservative: it only fires
when a gold column and an agent column are the two DIFFERENT halves of ONE registered
lookup, and it degrades to "cannot decide" rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Lookup:
    """One entity's identity pair: `id_col` and `name_col` both live in `table` and both
    UNIQUELY identify the entity, so a result keyed by one is convertible to the other."""
    table: str          # schema-qualified, e.g. "fab_curated.product_master"
    id_col: str
    name_col: str


# The registered identity pairs. Extending schema-aware matching to a new entity (branch
# code <-> branch name, etc.) is a one-line addition here — nothing else changes.
LOOKUPS: tuple[Lookup, ...] = (
    Lookup("fab_curated.product_master", "product_id", "product_name"),
    Lookup("fab_curated.customer_master", "customer_id", "customer_name"),
)


@dataclass
class SemanticVerdict:
    decidable: bool                 # False => cannot tell (no lookup / no DB); defer
    equivalent: bool = False
    confidence: float = 0.0
    reason: str = ""
    method: str = ""                # "structural" | "lookup" | ""
    rewrites: list = field(default_factory=list)   # human-readable id->name substitutions


class SchemaSemanticMatcher:
    """Decides whether two result sets identify the same entities under a different key.

    Stateful only for the lookup cache. Construct once per eval run and reuse across items
    so the DB (if used at all) is hit at most once per registered lookup.
    """

    def __init__(self, lookups: tuple[Lookup, ...] = LOOKUPS, db=None):
        self._lookups = lookups
        self._db = db                 # inject for testing; default resolves lazily
        self._cache: dict[str, dict] = {}   # "id_col->name_col" -> {id: name}

    # ------------------------------------------------------------------ public
    def equivalent(self, gold: list[dict] | None, agent: list[dict] | None,
                   numeric_tolerance: float = 0.01) -> SemanticVerdict:
        """Are these result sets the same entities under a swapped id/name key?

        Tries each registered lookup; fires on the first whose id/name pair spans the two
        results (gold shows one half, agent the other). Returns decidable=False when none
        applies or the evidence is insufficient — the caller then keeps the syntactic
        verdict."""
        if not gold or not agent:
            return SemanticVerdict(decidable=False, reason="empty result set")
        gset = {c.lower() for c in gold[0]}
        aset = {c.lower() for c in agent[0]}
        for lk in self._lookups:
            pair = self._spanning_pair(lk, gset, aset)
            if pair is None:
                continue
            v = self._resolve(gold, agent, pair[0], pair[1], lk, numeric_tolerance)
            if v is not None and (v.decidable or v is not None):
                return v
        return SemanticVerdict(
            decidable=False, reason="no id/name lookup pair spans the two result sets")

    # ------------------------------------------------------------------ matching
    @staticmethod
    def _spanning_pair(lk: Lookup, gset: set, aset: set):
        """(gold_col_key, agent_col_key) if this lookup's two halves sit on OPPOSITE sides.

        Requires gold to expose one half and agent the OTHER, and that neither already
        exposes the other's column — if the agent also returned product_name there is no
        id/name substitution to reconcile, and the plain result comparison already applies.
        """
        for gk, ak in ((lk.name_col, lk.id_col), (lk.id_col, lk.name_col)):
            if gk in gset and ak in aset and gk not in aset and ak not in gset:
                return gk, ak
        return None

    def _resolve(self, gold, agent, gcol_key, acol_key, lk: Lookup,
                 tol: float) -> SemanticVerdict:
        gcol = self._orig(gold, gcol_key)
        acol = self._orig(agent, acol_key)
        pair = f"{lk.id_col}<->{lk.name_col}"
        if len(gold) != len(agent):
            return SemanticVerdict(
                decidable=True, equivalent=False, confidence=0.9,
                reason=f"{pair} spans the results but the row counts differ "
                       f"({len(gold)} vs {len(agent)})")

        g_ent = [self._norm(r[gcol]) for r in gold]
        a_ent = [self._norm(r[acol]) for r in agent]
        ent_bijective = len(set(g_ent)) == len(g_ent) and len(set(a_ent)) == len(a_ent)
        g_rest = [{k: v for k, v in r.items() if k != gcol} for r in gold]
        a_rest = [{k: v for k, v in r.items() if k != acol} for r in agent]
        has_context = bool(g_rest and g_rest[0])

        # ---- 1. STRUCTURAL proof (offline) --------------------------------------------
        if has_context and ent_bijective:
            from eval.deterministic import result_metrics
            rest = result_metrics.compare_results(
                g_rest, a_rest, order_sensitive=False, numeric_tolerance=tol)
            if rest.exact_match and self._rows_distinct(g_rest) and self._rows_distinct(a_rest):
                mp = self._map(lk)   # used only to annotate; verdict does not depend on it
                note = ("" if mp is None else " (id->name lookup available for labels)")
                return SemanticVerdict(
                    decidable=True, equivalent=True, confidence=0.95, method="structural",
                    reason=(f"same {len(gold)} rows once the {pair} label is set aside — "
                            f"`{gcol}` (gold) and `{acol}` (agent) are unique keys of the "
                            f"same {lk.table.split('.')[-1]}, and every other column matches "
                            f"row-for-row{note}"),
                    rewrites=self._rewrites(agent, acol, acol_key, lk, mp))
            if rest.exact_match:
                # non-entity columns match but are not distinct enough to pin the pairing;
                # fall through to the lookup path for a definitive answer.
                pass
            else:
                return SemanticVerdict(
                    decidable=True, equivalent=False, confidence=0.85,
                    reason=f"{pair} spans the results but the non-label columns differ")

        # ---- 2. LOOKUP-BASED proof (needs the DB) -------------------------------------
        mp = self._map(lk)
        if mp is None:
            return SemanticVerdict(
                decidable=False,
                reason=f"{pair} spans the results but there is no other column to prove "
                       f"identity, and the {lk.table} lookup is unavailable (no DB)")
        # Resolve BOTH entity columns into name-space, then compare the full rows.
        g_named = self._to_name_space(gold, gcol, gcol_key, lk, mp)
        a_named = self._to_name_space(agent, acol, acol_key, lk, mp)
        if g_named is None or a_named is None:
            return SemanticVerdict(
                decidable=False,
                reason=f"an id in `{gcol}`/`{acol}` is absent from {lk.table}")
        from eval.deterministic import result_metrics
        full = result_metrics.compare_results(
            g_named, a_named, order_sensitive=False, numeric_tolerance=tol)
        return SemanticVerdict(
            decidable=True, equivalent=full.exact_match,
            confidence=0.95 if full.exact_match else 0.9, method="lookup",
            reason=(f"resolved {pair} through {lk.table.split('.')[-1]}: the entities "
                    + ("match" if full.exact_match else "differ")),
            rewrites=self._rewrites(agent, acol, acol_key, lk, mp))

    # ------------------------------------------------------------------ lookups
    def _get_db(self):
        if self._db is not None:
            return self._db
        try:
            from sql_agent.db import db
            self._db = db
        except Exception:  # noqa: BLE001 — no DB is a valid state (offline re-run)
            self._db = False
        return self._db

    def _map(self, lk: Lookup) -> dict | None:
        """{id_value(lower) -> name_value} for one lookup, cached. None if unreadable."""
        key = f"{lk.table}:{lk.id_col}->{lk.name_col}"
        if key in self._cache:
            return self._cache[key]
        db = self._get_db()
        if not db:
            self._cache[key] = None
            return None
        try:
            rows = db.execute(f"SELECT {lk.id_col}, {lk.name_col} FROM {lk.table}").rows
            mp = {str(r[lk.id_col]).strip().lower(): str(r[lk.name_col])
                  for r in rows if r.get(lk.id_col) is not None}
            self._cache[key] = mp or None
        except Exception:  # noqa: BLE001 — an unreadable lookup is "cannot decide"
            self._cache[key] = None
        return self._cache[key]

    def _to_name_space(self, rows, col, col_key, lk: Lookup, mp: dict):
        """Rewrite `rows` so the entity column holds the canonical NAME under a common key.
        Returns None if any id is missing from the lookup."""
        rev = {str(n).strip().lower(): str(n) for n in mp.values()}
        out = []
        for r in rows:
            raw = r[col]
            if col_key == lk.id_col:
                name = mp.get(str(raw).strip().lower())
            else:
                name = rev.get(str(raw).strip().lower(), str(raw))
            if name is None:
                return None
            r2 = {k: v for k, v in r.items() if k != col}
            r2["__entity__"] = name
            out.append(r2)
        return out

    def _rewrites(self, rows, col, col_key, lk: Lookup, mp: dict | None) -> list:
        """Human-readable `PROD002 -> Term Loan` list for the report, when a map exists and
        the agent side carried ids. Purely cosmetic — the verdict never depends on it."""
        if mp is None or col_key != lk.id_col:
            return []
        seen, out = set(), []
        for r in rows:
            raw = str(r[col])
            name = mp.get(raw.strip().lower())
            if name and raw not in seen:
                seen.add(raw)
                out.append(f"{raw} -> {name}")
        return out[:12]

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _orig(rows, col_key: str) -> str:
        """The original-cased column name for a lowercased key."""
        for c in rows[0]:
            if c.lower() == col_key:
                return c
        return col_key

    @staticmethod
    def _norm(v) -> str:
        return str(v).strip().lower()

    @staticmethod
    def _rows_distinct(rows: list[dict]) -> bool:
        """Are the row dicts pairwise distinct (by value)? Guards the structural proof: if
        two rows share the same non-entity signature, matching it no longer pins WHICH
        entity is which, so structural equivalence would be ambiguous."""
        from eval.deterministic.result_metrics import _cell
        seen = set()
        for r in rows:
            sig = tuple(sorted((k.lower(), _cell(v)) for k, v in r.items()))
            if sig in seen:
                return False
            seen.add(sig)
        return True
