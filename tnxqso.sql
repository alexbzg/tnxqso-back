--
-- PostgreSQL database dump
--

-- Dumped from database version 13.5 (Debian 13.5-0+deb11u1)
-- Dumped by pg_dump version 13.5 (Debian 13.5-0+deb11u1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: is_callsign(character varying); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.is_callsign(callsign character varying) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    AS $_$
begin
  return callsign  ~ '^[\d]*[a-zA-Z]+\d+[a-zA-Z]+(/[\da-zA-Z])*$';
end
$_$;


ALTER FUNCTION public.is_callsign(callsign character varying) OWNER TO postgres;

--
-- Name: tf_users_bi(); Type: FUNCTION; Schema: public; Owner: postgres
--

CREATE FUNCTION public.tf_users_bi() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
  if (new.chat_callsign is null or new_callsign = '') and is_callsign(new.callsign) then
    new.chat_callsign = upper(new.callsign);
  end if;
  return new;
end;
$$;


ALTER FUNCTION public.tf_users_bi() OWNER TO postgres;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: blog_comments; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.blog_comments (
    id bigint NOT NULL,
    "user" character varying(32) NOT NULL,
    entry_id bigint NOT NULL,
    txt text NOT NULL,
    timestamp_created timestamp without time zone DEFAULT timezone('utc'::text, now()) NOT NULL
);


ALTER TABLE public.blog_comments OWNER TO postgres;

--
-- Name: blog_comments_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.blog_comments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.blog_comments_id_seq OWNER TO postgres;

--
-- Name: blog_comments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.blog_comments_id_seq OWNED BY public.blog_comments.id;


--
-- Name: blog_comments_read; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.blog_comments_read (
    entry_id integer NOT NULL,
    "user" character varying(32) NOT NULL,
    last_read_comment_id integer NOT NULL
);


ALTER TABLE public.blog_comments_read OWNER TO postgres;

--
-- Name: blog_entries; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.blog_entries (
    id bigint NOT NULL,
    "user" character varying(32) NOT NULL,
    file text,
    file_type text,
    txt text,
    timestamp_created timestamp without time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    file_thumb text,
    media_width smallint
);


ALTER TABLE public.blog_entries OWNER TO postgres;

--
-- Name: blog_entries_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.blog_entries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.blog_entries_id_seq OWNER TO postgres;

--
-- Name: blog_entries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.blog_entries_id_seq OWNED BY public.blog_entries.id;


--
-- Name: blog_reactions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.blog_reactions (
    entry_id bigint NOT NULL,
    "user" character varying(32) NOT NULL,
    type smallint NOT NULL
);


ALTER TABLE public.blog_reactions OWNER TO postgres;

--
-- Name: dxpeditions; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.dxpeditions (
    id integer NOT NULL,
    callsign character varying NOT NULL
);


ALTER TABLE public.dxpeditions OWNER TO postgres;

--
-- Name: dxpeditions_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.dxpeditions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.dxpeditions_id_seq OWNER TO postgres;

--
-- Name: dxpeditions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: postgres
--

ALTER SEQUENCE public.dxpeditions_id_seq OWNED BY public.dxpeditions.id;


--
-- Name: log_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.log_id_seq
    START WITH 15
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.log_id_seq OWNER TO postgres;

--
-- Name: log; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.log (
    id integer DEFAULT nextval('public.log_id_seq'::regclass) NOT NULL,
    qso jsonb,
    callsign character varying(32)
);


ALTER TABLE public.log OWNER TO postgres;

--
-- Name: private_messages_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.private_messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    MAXVALUE 2147483647
    CACHE 1;


ALTER TABLE public.private_messages_id_seq OWNER TO postgres;

--
-- Name: private_messages; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.private_messages (
    id integer DEFAULT nextval('public.private_messages_id_seq'::regclass) NOT NULL,
    callsign_from character varying(32) NOT NULL,
    callsign_to character varying(32) NOT NULL,
    tstamp timestamp without time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    txt character varying(300) NOT NULL,
    unread boolean DEFAULT true NOT NULL
);


ALTER TABLE public.private_messages OWNER TO postgres;

--
-- Name: qth_now_locations_id_seq; Type: SEQUENCE; Schema: public; Owner: postgres
--

CREATE SEQUENCE public.qth_now_locations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER TABLE public.qth_now_locations_id_seq OWNER TO postgres;

--
-- Name: qth_now_locations; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.qth_now_locations (
    id bigint DEFAULT nextval('public.qth_now_locations_id_seq'::regclass) NOT NULL,
    lat numeric(7,4) NOT NULL,
    lng numeric(7,4) NOT NULL,
    rda character varying(16),
    tstamp timestamp without time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.qth_now_locations OWNER TO postgres;

--
-- Name: users; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.users (
    callsign character varying(32) NOT NULL,
    password character varying(1024) NOT NULL,
    settings jsonb,
    email character varying(32),
    name character varying(64),
    email_confirmed boolean DEFAULT false NOT NULL,
    chat_callsign character varying(32),
    pm_enabled boolean DEFAULT true NOT NULL,
    verified boolean DEFAULT false NOT NULL
);


ALTER TABLE public.users OWNER TO postgres;

--
-- Name: blog_comments id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blog_comments ALTER COLUMN id SET DEFAULT nextval('public.blog_comments_id_seq'::regclass);


--
-- Name: blog_entries id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blog_entries ALTER COLUMN id SET DEFAULT nextval('public.blog_entries_id_seq'::regclass);


--
-- Name: dxpeditions id; Type: DEFAULT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.dxpeditions ALTER COLUMN id SET DEFAULT nextval('public.dxpeditions_id_seq'::regclass);


--
-- Name: blog_comments blog_comments_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blog_comments
    ADD CONSTRAINT blog_comments_pkey PRIMARY KEY (id);


--
-- Name: blog_comments_read blog_comments_read_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blog_comments_read
    ADD CONSTRAINT blog_comments_read_pkey PRIMARY KEY (entry_id, "user");


--
-- Name: blog_entries blog_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blog_entries
    ADD CONSTRAINT blog_entries_pkey PRIMARY KEY (id);


--
-- Name: blog_reactions blog_reactions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blog_reactions
    ADD CONSTRAINT blog_reactions_pkey PRIMARY KEY (entry_id, "user");


--
-- Name: dxpeditions dxpeditions_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.dxpeditions
    ADD CONSTRAINT dxpeditions_pkey PRIMARY KEY (id);


--
-- Name: log log_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.log
    ADD CONSTRAINT log_pkey PRIMARY KEY (id);


--
-- Name: private_messages private_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.private_messages
    ADD CONSTRAINT private_messages_pkey PRIMARY KEY (id);


--
-- Name: qth_now_locations qth_now_locations_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.qth_now_locations
    ADD CONSTRAINT qth_now_locations_pkey PRIMARY KEY (id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (callsign);


--
-- Name: callsign_qso_ts_log_idx; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX callsign_qso_ts_log_idx ON public.log USING btree (callsign, (((qso ->> 'ts'::text))::double precision));


--
-- Name: callsign_qso_uq; Type: INDEX; Schema: public; Owner: postgres
--

CREATE UNIQUE INDEX callsign_qso_uq ON public.log USING btree (callsign, ((qso ->> 'cs'::text)), ((qso ->> 'qso_ts'::text)), ((qso ->> 'band'::text)));


--
-- Name: users tr_users_bi; Type: TRIGGER; Schema: public; Owner: postgres
--

CREATE TRIGGER tr_users_bi BEFORE INSERT ON public.users FOR EACH ROW EXECUTE FUNCTION public.tf_users_bi();


--
-- Name: blog_comments blog_comments_entry_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blog_comments
    ADD CONSTRAINT blog_comments_entry_id_fkey FOREIGN KEY (entry_id) REFERENCES public.blog_entries(id) ON UPDATE CASCADE ON DELETE CASCADE NOT VALID;


--
-- Name: blog_comments_read blog_comments_read_entry_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blog_comments_read
    ADD CONSTRAINT blog_comments_read_entry_id_fkey FOREIGN KEY (entry_id) REFERENCES public.blog_entries(id) ON UPDATE CASCADE ON DELETE CASCADE NOT VALID;


--
-- Name: blog_comments_read blog_comments_read_user_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blog_comments_read
    ADD CONSTRAINT blog_comments_read_user_fkey FOREIGN KEY ("user") REFERENCES public.users(callsign);


--
-- Name: blog_comments blog_comments_user_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blog_comments
    ADD CONSTRAINT blog_comments_user_fkey FOREIGN KEY ("user") REFERENCES public.users(callsign);


--
-- Name: blog_entries blog_entries_user_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blog_entries
    ADD CONSTRAINT blog_entries_user_fkey FOREIGN KEY ("user") REFERENCES public.users(callsign);


--
-- Name: blog_reactions blog_reactions_entry_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blog_reactions
    ADD CONSTRAINT blog_reactions_entry_id_fkey FOREIGN KEY (entry_id) REFERENCES public.blog_entries(id) ON UPDATE CASCADE ON DELETE CASCADE NOT VALID;


--
-- Name: blog_reactions blog_reactions_user_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.blog_reactions
    ADD CONSTRAINT blog_reactions_user_fkey FOREIGN KEY ("user") REFERENCES public.users(callsign);


--
-- Name: dxpeditions dxpeditions_callsign_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.dxpeditions
    ADD CONSTRAINT dxpeditions_callsign_fkey FOREIGN KEY (callsign) REFERENCES public.users(callsign);


--
-- Name: log log_callsign_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.log
    ADD CONSTRAINT log_callsign_fkey FOREIGN KEY (callsign) REFERENCES public.users(callsign);


--
-- Name: private_messages private_messages_callsign_from_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.private_messages
    ADD CONSTRAINT private_messages_callsign_from_fkey FOREIGN KEY (callsign_from) REFERENCES public.users(callsign);


--
-- Name: private_messages private_messages_callsign_to_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.private_messages
    ADD CONSTRAINT private_messages_callsign_to_fkey FOREIGN KEY (callsign_to) REFERENCES public.users(callsign);


--
-- Name: TABLE blog_comments; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,UPDATE ON TABLE public.blog_comments TO "www-group";


--
-- Name: SEQUENCE blog_comments_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,USAGE ON SEQUENCE public.blog_comments_id_seq TO www;


--
-- Name: TABLE blog_comments_read; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT,REFERENCES,DELETE,UPDATE ON TABLE public.blog_comments_read TO "www-group";


--
-- Name: TABLE blog_entries; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,UPDATE ON TABLE public.blog_entries TO "www-group";


--
-- Name: SEQUENCE blog_entries_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,USAGE ON SEQUENCE public.blog_entries_id_seq TO www;


--
-- Name: TABLE blog_reactions; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,UPDATE ON TABLE public.blog_reactions TO "www-group";


--
-- Name: TABLE dxpeditions; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.dxpeditions TO "www-group";


--
-- Name: SEQUENCE dxpeditions_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,UPDATE ON SEQUENCE public.dxpeditions_id_seq TO "www-group";
GRANT SELECT,USAGE ON SEQUENCE public.dxpeditions_id_seq TO www;


--
-- Name: SEQUENCE log_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,USAGE ON SEQUENCE public.log_id_seq TO www;


--
-- Name: TABLE log; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.log TO "www-group";


--
-- Name: SEQUENCE private_messages_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,USAGE ON SEQUENCE public.private_messages_id_seq TO www;


--
-- Name: TABLE private_messages; Type: ACL; Schema: public; Owner: postgres
--

GRANT ALL ON TABLE public.private_messages TO www;


--
-- Name: SEQUENCE qth_now_locations_id_seq; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,USAGE ON SEQUENCE public.qth_now_locations_id_seq TO www;


--
-- Name: TABLE qth_now_locations; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT,REFERENCES,DELETE,TRIGGER,UPDATE ON TABLE public.qth_now_locations TO "www-group";


--
-- Name: TABLE users; Type: ACL; Schema: public; Owner: postgres
--

GRANT SELECT,INSERT,REFERENCES,UPDATE ON TABLE public.users TO "www-group";


--
-- Name: DEFAULT PRIVILEGES FOR SEQUENCES; Type: DEFAULT ACL; Schema: public; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT,USAGE ON SEQUENCES  TO www;


--
-- PostgreSQL database dump complete
--

