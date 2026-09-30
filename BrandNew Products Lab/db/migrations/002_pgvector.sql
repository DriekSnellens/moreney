-- Optional. Skip this file when the vector extension is not installed.
-- Embeddings are for later similarity search across products, niches, and creatives.
-- They are not required for the profit loop.

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE products ADD COLUMN IF NOT EXISTS embedding vector(1536);
ALTER TABLE niches ADD COLUMN IF NOT EXISTS embedding vector(1536);
ALTER TABLE creative_concepts ADD COLUMN IF NOT EXISTS embedding vector(1536);
