ALTER TABLE IF EXISTS public.users
    ADD COLUMN last_blog_entry_delete timestamp without time zone;

CREATE OR REPLACE FUNCTION public.blog_entry_delete_trf()
    RETURNS trigger
    LANGUAGE 'plpgsql'
    COST 100
    VOLATILE NOT LEAKPROOF
AS $BODY$
begin
  update users set last_blog_entry_delete = now()
  	where callsign = old.user;
  return old;
end;
$BODY$;

ALTER FUNCTION public.blog_entry_delete_trf()
    OWNER TO postgres;

GRANT EXECUTE ON FUNCTION public.blog_entry_delete_trf() TO PUBLIC;

GRANT EXECUTE ON FUNCTION public.blog_entry_delete_trf() TO postgres;

GRANT EXECUTE ON FUNCTION public.blog_entry_delete_trf() TO www;

CREATE TRIGGER blog_entries_delete_tr
    BEFORE DELETE
    ON public.blog_entries
    FOR EACH ROW
    EXECUTE FUNCTION public.blog_entry_delete_trf();
