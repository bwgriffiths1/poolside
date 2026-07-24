-- Migration 017: document_images bytes move to object storage.
--
-- The base64 PNGs in image_b64 were ~82% of the database (269 of 328 MB at
-- the 2026-07-23 disk-full incident) and the weekly prune only slowed the
-- ratchet. Bytes now live in an S3-compatible bucket (Railway Storage
-- Bucket in prod) behind pipeline/storage.py; the row keeps metadata only.
--
-- storage_key: object key (docimg/{document_id}/{page}_{idx}.png) when the
--   PNG lives in the bucket; NULL for legacy rows still carrying image_b64.
--   After api/tools/backfill_image_storage.py runs, exactly one of
--   (image_b64, storage_key) is populated per row. image_b64 is retained
--   (not dropped) until the migration has soaked — every reader goes
--   through pipeline/storage.get_image_bytes, which serves either era.
-- size_bytes: raw PNG size, so image_stats / the admin Image-storage panel
--   can report usage without fetching objects (same convention as
--   document_files.size_bytes, migration 008).
ALTER TABLE document_images ADD COLUMN IF NOT EXISTS storage_key TEXT;
ALTER TABLE document_images ADD COLUMN IF NOT EXISTS size_bytes  INT;
