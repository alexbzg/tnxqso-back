ALTER TABLE IF EXISTS public.users
    ADD COLUMN gallery_quotas smallint NOT NULL DEFAULT 1;
