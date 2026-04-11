-- =============================================================================
-- Tantra AI — Postgres init: Create dedicated LiteLLM database
-- =============================================================================
-- This script runs once on first postgres container startup (when pgdata is empty).
-- It creates a separate 'litellm' database for LiteLLM's Prisma schema so that
-- LiteLLM's db push / migrations never touch Tantra's tables (weekly_plans, etc.).
-- =============================================================================
CREATE DATABASE litellm;
GRANT ALL PRIVILEGES ON DATABASE litellm TO tantra;
