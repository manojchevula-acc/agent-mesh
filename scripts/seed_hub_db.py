"""
scripts/seed_hub_db.py
-----------------------
Create the mcp_servers table in fab_semantic and seed it from
hub_service/mcp-hub.json.

Run once after MySQL is started, and any time you want to reset or
update the registry:

    python scripts/seed_hub_db.py

Safe to re-run — uses INSERT ... ON DUPLICATE KEY UPDATE so existing
rows are updated in place rather than duplicated. New columns are added
automatically via INFORMATION_SCHEMA check + ALTER TABLE.
"""

import json
import sys
from pathlib import Path

# Allow this script to import from both hub_service/ and datalayer-as-service/
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hub_service"))
sys.path.insert(0, str(ROOT / "datalayer-as-service"))

from sqlalchemy import text          # noqa: E402  (import after path setup)
from db import get_engine            # hub_service/db.py  # noqa: E402

HUB_JSON = ROOT / "hub_service" / "mcp-hub.json"

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS mcp_servers (
    id              VARCHAR(100)  NOT NULL,
    name            VARCHAR(255)  NOT NULL,
    endpoint        VARCHAR(500)  NOT NULL,
    transport       VARCHAR(50)   NOT NULL DEFAULT 'sse',
    capability      TEXT,
    skills          JSON,
    description     TEXT,
    examples        JSON,
    start_cmd       TEXT,
    api_key         VARCHAR(1000) DEFAULT NULL COMMENT 'Per-server Bearer token for agent auth. Overrides MCP_API_KEY env var when set.',
    api_key_expires TIMESTAMP     DEFAULT NULL COMMENT 'When the api_key expires (NULL = never)',
    is_active       TINYINT(1)    NOT NULL DEFAULT 1,
    created_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""

UPSERT = """
INSERT INTO mcp_servers (id, name, endpoint, transport, capability, skills, description, examples, start_cmd)
VALUES (:id, :name, :endpoint, :transport, :capability, :skills, :description, :examples, :start_cmd)
ON DUPLICATE KEY UPDATE
    name        = VALUES(name),
    endpoint    = VALUES(endpoint),
    transport   = VALUES(transport),
    capability  = VALUES(capability),
    skills      = VALUES(skills),
    description = VALUES(description),
    examples    = VALUES(examples),
    start_cmd   = VALUES(start_cmd),
    is_active   = 1,         -- NOTE: re-activates any server that was manually disabled via
                             -- the Admin UI. If you need to permanently remove a server,
                             -- delete it from mcp-hub.json before re-running this script,
                             -- or set is_active=0 manually after the seed.
    updated_at  = CURRENT_TIMESTAMP;
"""

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if not HUB_JSON.exists():
        print(f"ERROR: hub registry not found at {HUB_JSON}")
        sys.exit(1)

    hub     = json.loads(HUB_JSON.read_text(encoding="utf-8"))
    servers = hub.get("servers", [])
    print(f"Source  : {HUB_JSON}")
    print(f"Servers : {len(servers)}")

    engine = get_engine()
    with engine.begin() as conn:
        print("\nCreating mcp_servers table (if not exists)...")
        conn.execute(text(CREATE_TABLE))

        # MySQL has no ADD COLUMN IF NOT EXISTS, so we query INFORMATION_SCHEMA
        # to find which columns already exist, then only ALTER for missing ones.
        # This loop is append-only — it can add new columns but cannot rename or
        # drop existing ones. For column renames or type changes, write a separate
        # migration script rather than modifying this loop.
        existing = {
            row[0]
            for row in conn.execute(text(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'mcp_servers'"
            ))
        }
        new_cols = {
            "capability":      "ALTER TABLE mcp_servers ADD COLUMN capability TEXT DEFAULT NULL AFTER transport",
            "skills":          "ALTER TABLE mcp_servers ADD COLUMN skills     JSON DEFAULT NULL AFTER capability",
            "api_key":         "ALTER TABLE mcp_servers ADD COLUMN api_key         VARCHAR(1000) DEFAULT NULL AFTER start_cmd",
            "api_key_expires": "ALTER TABLE mcp_servers ADD COLUMN api_key_expires TIMESTAMP     DEFAULT NULL AFTER api_key",
        }
        for col, ddl in new_cols.items():
            if col not in existing:
                print(f"  adding column: {col}")
                conn.execute(text(ddl))
            else:
                print(f"  column exists: {col}")

        print("Seeding rows...")
        for s in servers:
            conn.execute(text(UPSERT), {
                "id":          s["id"],
                "name":        s["name"],
                "endpoint":    s["endpoint"],
                "transport":   s.get("transport", "sse"),
                "capability":  s.get("capability", ""),
                "skills":      json.dumps(s.get("skills", [])),
                "description": s.get("description", ""),
                "examples":    json.dumps(s.get("examples", [])),
                "start_cmd":   s.get("start_cmd", ""),
            })
            print(f"  ok  {s['id']:30s}  {s.get('capability', '')}")

    print(f"\nDone — {len(servers)} servers registered in fab_semantic.mcp_servers")
    print("The hub server will read from this table on startup.")


if __name__ == "__main__":
    main()
