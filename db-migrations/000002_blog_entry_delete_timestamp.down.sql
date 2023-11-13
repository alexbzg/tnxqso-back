DROP TRIGGER IF EXISTS blog_entries_delete_tr ON public.blog_entries;
DROP FUNCTION IF EXISTS public.blog_entry_delete_trf();
ALTER TABLE IF EXISTS public.users DROP COLUMN IF EXISTS last_blog_entry_delete;
