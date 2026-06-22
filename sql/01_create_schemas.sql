-- =============================================================
-- 01_create_schemas.sql
-- Creates the two schemas used by the FAB Structured Data POC.
-- Run this once before any other SQL or Python scripts.
-- =============================================================

CREATE SCHEMA IF NOT EXISTS fab_curated
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

CREATE SCHEMA IF NOT EXISTS fab_semantic
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;
