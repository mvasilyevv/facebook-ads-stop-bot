--
-- PostgreSQL database dump
--


-- Dumped from database version 16.14 (Debian 16.14-1.pgdg13+1)
-- Dumped by pg_dump version 16.14 (Debian 16.14-1.pgdg13+1)

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
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: bump_fb_operator_revision(text, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.bump_fb_operator_revision(event_scope text, event_id text) RETURNS bigint
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        DECLARE
            next_revision bigint;
        BEGIN
            INSERT INTO public.operator_revision_events (scope, event_id)
            VALUES (
                LEFT(COALESCE(NULLIF(event_scope, ''), 'snapshot'), 64),
                LEFT(event_id, 256)
            )
            RETURNING revision INTO next_revision;

            PERFORM pg_notify(
                'fb_operator_events',
                json_build_object(
                    'scope', event_scope,
                    'id', event_id,
                    'revision', next_revision
                )::text
            );
            RETURN next_revision;
        END;
        $$;


--
-- Name: enforce_adset_duplicate_preview_consume_once(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enforce_adset_duplicate_preview_consume_once() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        BEGIN
            IF OLD.task_id IS NOT NULL OR OLD.consumed_at IS NOT NULL THEN
                RAISE EXCEPTION
                    'adset duplicate preview receipt is immutable after consumption'
                    USING ERRCODE = 'check_violation';
            END IF;

            IF NEW.task_id IS NULL THEN
                RAISE EXCEPTION
                    'adset duplicate preview update must consume into a task'
                    USING ERRCODE = 'check_violation';
            END IF;

            IF NEW.token_digest IS DISTINCT FROM OLD.token_digest
                OR NEW.principal IS DISTINCT FROM OLD.principal
                OR NEW.preview IS DISTINCT FROM OLD.preview
                OR NEW.task_payload IS DISTINCT FROM OLD.task_payload
                OR NEW.plan_digest IS DISTINCT FROM OLD.plan_digest
                OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
                OR NEW.created_at IS DISTINCT FROM OLD.created_at
                OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
            THEN
                RAISE EXCEPTION
                    'adset duplicate preview authority fields are immutable'
                    USING ERRCODE = 'check_violation';
            END IF;

            NEW.consumed_at := clock_timestamp();
            RETURN NEW;
        END;
        $$;


--
-- Name: invalidate_browser_readiness_on_maintenance(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.invalidate_browser_readiness_on_maintenance() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        BEGIN
            IF COALESCE(
                (NEW.value->>'expires_at')::timestamptz,
                '-infinity'::timestamptz
            ) > clock_timestamp() THEN
                UPDATE public.browser_channel_readiness
                SET state = 'maintenance',
                    reason_code = 'browser_maintenance_active',
                    observed_at = clock_timestamp(),
                    readiness_expires_at = NULL,
                    generation = generation + 1,
                    updated_at = clock_timestamp()
                WHERE channel = 'meta_api';
            END IF;
            RETURN NEW;
        END;
        $$;


--
-- Name: notify_fb_operator_event(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.notify_fb_operator_event() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        DECLARE
            event_id text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                event_id := to_jsonb(OLD) ->> TG_ARGV[1];
            ELSE
                event_id := to_jsonb(NEW) ->> TG_ARGV[1];
            END IF;
            PERFORM public.bump_fb_operator_revision(TG_ARGV[0], event_id);
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$;


--
-- Name: notify_fb_operator_statement(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.notify_fb_operator_statement() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public'
    AS $$
        BEGIN
            PERFORM public.bump_fb_operator_revision(TG_ARGV[0], NULL);
            RETURN NULL;
        END;
        $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: ad_alert_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ad_alert_state (
    ad_id uuid NOT NULL,
    alert_state character varying(16) DEFAULT 'normal'::character varying NOT NULL,
    current_stage character varying(16),
    open_state_token uuid,
    warning_rule_codes jsonb DEFAULT '[]'::jsonb NOT NULL,
    stop_rule_codes jsonb DEFAULT '[]'::jsonb NOT NULL,
    snoozed_until timestamp with time zone,
    enable_grace_until timestamp with time zone,
    enable_grace_spend_cap numeric(20,6),
    enable_grace_baseline_spend numeric(20,6),
    enable_grace_cabinet_day_start timestamp with time zone,
    enable_grace_currency character varying(3),
    enable_grace_currency_exponent smallint,
    last_scan_id bigint,
    last_transition_at timestamp with time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_ad_alert_state_enable_grace_coherent CHECK (
        (
            enable_grace_until IS NULL
            AND enable_grace_spend_cap IS NULL
            AND enable_grace_baseline_spend IS NULL
            AND enable_grace_cabinet_day_start IS NULL
            AND enable_grace_currency IS NULL
            AND enable_grace_currency_exponent IS NULL
        )
        OR (
            enable_grace_until IS NOT NULL
            AND enable_grace_spend_cap IS NOT NULL
            AND enable_grace_baseline_spend IS NOT NULL
            AND enable_grace_cabinet_day_start IS NOT NULL
            AND enable_grace_currency IS NOT NULL
            AND enable_grace_currency_exponent IS NOT NULL
            AND enable_grace_spend_cap > 0
            AND enable_grace_baseline_spend >= 0
            AND enable_grace_baseline_spend < enable_grace_spend_cap
            AND enable_grace_until > enable_grace_cabinet_day_start
        )
    ),
    CONSTRAINT ck_ad_alert_state_enable_grace_currency CHECK (((enable_grace_currency IS NULL) OR ((enable_grace_currency)::text ~ '^[A-Z]{3}$'::text))),
    CONSTRAINT ck_ad_alert_state_enable_grace_currency_exponent CHECK (((enable_grace_currency_exponent IS NULL) OR (enable_grace_currency_exponent = ANY (ARRAY[0, 2, 3]))))
);


--
-- Name: ad_auto_enable_disabled; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ad_auto_enable_disabled (
    ad_id uuid NOT NULL,
    cabinet_day_started_at timestamp with time zone NOT NULL,
    reason character varying(64),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ad_metrics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ad_metrics (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ad_id uuid NOT NULL,
    cycle_ts timestamp with time zone NOT NULL,
    scan_id bigint,
    currency character varying(3),
    spend numeric(18,3),
    reach integer,
    impressions integer,
    clicks integer,
    cpc numeric(20,6),
    ctr numeric(7,4),
    cost_per_result numeric(20,6),
    cpm numeric(20,6),
    frequency numeric(7,4),
    leads integer,
    cost_per_lead numeric(20,6),
    registrations integer,
    cost_per_registration numeric(20,6),
    deposits integer,
    outbound_clicks integer,
    outbound_ctr numeric(7,4),
    landing_page_views integer,
    cost_per_landing_page_view numeric(20,6),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_ad_metrics_currency CHECK (((currency IS NULL) OR ((currency)::text ~ '^[A-Z]{3}$'::text)))
)
PARTITION BY RANGE (cycle_ts);


--
-- Name: ad_metrics_default; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ad_metrics_default (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ad_id uuid NOT NULL,
    cycle_ts timestamp with time zone NOT NULL,
    scan_id bigint,
    currency character varying(3),
    spend numeric(18,3),
    reach integer,
    impressions integer,
    clicks integer,
    cpc numeric(20,6),
    ctr numeric(7,4),
    cost_per_result numeric(20,6),
    cpm numeric(20,6),
    frequency numeric(7,4),
    leads integer,
    cost_per_lead numeric(20,6),
    registrations integer,
    cost_per_registration numeric(20,6),
    deposits integer,
    outbound_clicks integer,
    outbound_ctr numeric(7,4),
    landing_page_views integer,
    cost_per_landing_page_view numeric(20,6),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_ad_metrics_currency CHECK (((currency IS NULL) OR ((currency)::text ~ '^[A-Z]{3}$'::text)))
);


--
-- Name: adset_duplicate_previews; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.adset_duplicate_previews (
    token_digest bytea NOT NULL,
    principal character varying(64) NOT NULL,
    preview jsonb NOT NULL,
    task_payload jsonb NOT NULL,
    plan_digest bytea NOT NULL,
    idempotency_key character varying(128) NOT NULL,
    task_id bigint,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone,
    CONSTRAINT ck_adset_duplicate_previews_consumption_coherent CHECK ((((task_id IS NULL) AND (consumed_at IS NULL)) OR ((task_id IS NOT NULL) AND (consumed_at IS NOT NULL)))),
    CONSTRAINT ck_adset_duplicate_previews_idempotency_key_format CHECK (((idempotency_key)::text ~ '^meta:duplicate-adset:[0-9a-f]{64}$'::text)),
    CONSTRAINT ck_adset_duplicate_previews_plan_digest_sha256 CHECK ((octet_length(plan_digest) = 32)),
    CONSTRAINT ck_adset_duplicate_previews_preview_object CHECK ((jsonb_typeof(preview) = 'object'::text)),
    CONSTRAINT ck_adset_duplicate_previews_principal_length CHECK (((char_length((principal)::text) >= 1) AND (char_length((principal)::text) <= 64))),
    CONSTRAINT ck_adset_duplicate_previews_task_payload_object CHECK ((jsonb_typeof(task_payload) = 'object'::text)),
    CONSTRAINT ck_adset_duplicate_previews_token_digest_sha256 CHECK ((octet_length(token_digest) = 32)),
    CONSTRAINT ck_adset_duplicate_previews_valid_consumed_at CHECK (((consumed_at IS NULL) OR (consumed_at >= created_at))),
    CONSTRAINT ck_adset_duplicate_previews_valid_expiry CHECK ((expires_at > created_at))
);


--
-- Name: adsetpro_credentials; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.adsetpro_credentials (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    singleton_key character varying(16) DEFAULT 'default'::character varying NOT NULL,
    api_key_encrypted bytea,
    postback_secret_encrypted bytea,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: adsetpro_postback_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.adsetpro_postback_events (
    id bigint NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    click_id character varying(128) NOT NULL,
    fb_ad_id character varying(64),
    fb_ad_fk uuid,
    event_type character varying(32) NOT NULL,
    revenue numeric(12,4),
    currency character varying(3),
    raw_json jsonb NOT NULL,
    signature_valid boolean,
    is_duplicate boolean DEFAULT false NOT NULL,
    processed_at timestamp with time zone,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    source character varying(32) DEFAULT 'adsetpro'::character varying NOT NULL,
    provider_event_id character varying(128),
    attribution_status character varying(32) DEFAULT 'unmatched'::character varying NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    last_error character varying(500),
    next_retry_at timestamp with time zone,
    CONSTRAINT ck_adsetpro_postback_events_adsetpro_currency CHECK (((currency IS NULL) OR ((currency)::text ~ '^[A-Z]{3}$'::text))),
    CONSTRAINT ck_adsetpro_postback_events_adsetpro_event_type CHECK (((event_type)::text = ANY (ARRAY['registration'::text, 'ftd'::text, 'redeposit'::text])))
)
PARTITION BY RANGE (received_at);


--
-- Name: adsetpro_postback_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.adsetpro_postback_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: adsetpro_postback_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.adsetpro_postback_events_id_seq OWNED BY public.adsetpro_postback_events.id;


--
-- Name: adsetpro_postback_events_default; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.adsetpro_postback_events_default (
    id bigint DEFAULT nextval('public.adsetpro_postback_events_id_seq'::regclass) NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    click_id character varying(128) NOT NULL,
    fb_ad_id character varying(64),
    fb_ad_fk uuid,
    event_type character varying(32) NOT NULL,
    revenue numeric(12,4),
    currency character varying(3),
    raw_json jsonb NOT NULL,
    signature_valid boolean,
    is_duplicate boolean DEFAULT false NOT NULL,
    processed_at timestamp with time zone,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    source character varying(32) DEFAULT 'adsetpro'::character varying NOT NULL,
    provider_event_id character varying(128),
    attribution_status character varying(32) DEFAULT 'unmatched'::character varying NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    last_error character varying(500),
    next_retry_at timestamp with time zone,
    CONSTRAINT ck_adsetpro_postback_events_adsetpro_currency CHECK (((currency IS NULL) OR ((currency)::text ~ '^[A-Z]{3}$'::text))),
    CONSTRAINT ck_adsetpro_postback_events_adsetpro_event_type CHECK (((event_type)::text = ANY (ARRAY['registration'::text, 'ftd'::text, 'redeposit'::text])))
);


--
-- Name: alert_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ad_id uuid NOT NULL,
    stage character varying(16) NOT NULL,
    state character varying(16) NOT NULL,
    matched_rule_codes jsonb NOT NULL,
    metrics_json jsonb NOT NULL,
    open_state_token uuid,
    scan_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL
)
PARTITION BY RANGE (created_at);


--
-- Name: alert_events_default; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alert_events_default (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ad_id uuid NOT NULL,
    stage character varying(16) NOT NULL,
    state character varying(16) NOT NULL,
    matched_rule_codes jsonb NOT NULL,
    metrics_json jsonb NOT NULL,
    open_state_token uuid,
    scan_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: browser_channel_readiness; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.browser_channel_readiness (
    channel character varying(32) NOT NULL,
    vision_config_id uuid NOT NULL,
    vision_config_updated_at timestamp with time zone NOT NULL,
    expected_profile_id character varying(64) NOT NULL,
    observed_profile_id character varying(128),
    observed_session_id character varying(128),
    observed_contract_version integer,
    state character varying(24) NOT NULL,
    reason_code character varying(64) NOT NULL,
    observed_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    readiness_expires_at timestamp with time zone,
    writer_instance uuid NOT NULL,
    generation bigint DEFAULT 1 NOT NULL,
    last_ready_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: browser_operation_leases; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.browser_operation_leases (
    operation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    owner uuid NOT NULL,
    operation_kind character varying(64) NOT NULL,
    target character varying(128),
    lease_expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: browser_operation_capability_uses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.browser_operation_capability_uses (
    nonce_sha256 bytea NOT NULL,
    capability_digest bytea NOT NULL,
    operation_digest bytea NOT NULL,
    browser_contract_version integer NOT NULL,
    caller character varying(32) NOT NULL,
    rpc character varying(32) NOT NULL,
    task_id bigint NOT NULL,
    lease_owner uuid NOT NULL,
    lease_token bigint NOT NULL,
    session_id character varying(128) NOT NULL,
    vision_profile_id character varying(128) NOT NULL,
    ad_account_id character varying(32) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cabinet_runtime; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.cabinet_runtime (
    ad_account_id character varying(32) NOT NULL,
    owner_instance uuid,
    lease_token bigint DEFAULT 0 NOT NULL,
    lease_expires_at timestamp with time zone,
    next_scan_at timestamp with time zone,
    last_progress_at timestamp with time zone,
    last_snapshot_at timestamp with time zone,
    stage character varying(32),
    last_error_code character varying(64)
);


--
-- Name: campaign_creative; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.campaign_creative (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    offer_code character varying(64) NOT NULL,
    code character varying(64) NOT NULL,
    kind character varying(16) NOT NULL,
    meta_creative_id character varying(64) NOT NULL,
    run_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: campaign_preset; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.campaign_preset (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(255) NOT NULL,
    act_id character varying(64) NOT NULL,
    page_id character varying(64) NOT NULL,
    pixel_id character varying(64) NOT NULL,
    offer_code character varying(64),
    byer_tag character varying(64),
    objective character varying(64) DEFAULT 'OUTCOME_SALES'::character varying NOT NULL,
    optimization_goal character varying(64) DEFAULT 'OFFSITE_CONVERSIONS'::character varying NOT NULL,
    custom_event_type character varying(64) DEFAULT 'PURCHASE'::character varying NOT NULL,
    special_ad_categories jsonb DEFAULT '["NONE"]'::jsonb NOT NULL,
    cta character varying(64) DEFAULT 'PLAY_GAME'::character varying NOT NULL,
    text_optimizations character varying(32) DEFAULT 'OPT_OUT'::character varying NOT NULL,
    click_through_days integer DEFAULT 1 NOT NULL,
    view_through_days integer DEFAULT 1 NOT NULL,
    url_tags_template character varying(1024),
    naming_template character varying(512),
    extra jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by_chat_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: campaign_run; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.campaign_run (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    preset_id uuid,
    config jsonb NOT NULL,
    status character varying(16) DEFAULT 'queued'::character varying NOT NULL,
    progress jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_meta_ids jsonb DEFAULT '{}'::jsonb NOT NULL,
    error text,
    idempotency_key character varying(128),
    created_by_chat_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_campaign_run_status CHECK (((status)::text = ANY (ARRAY[('queued'::character varying)::text, ('uniquifying'::character varying)::text, ('uploading'::character varying)::text, ('creating'::character varying)::text, ('succeeded'::character varying)::text, ('failed'::character varying)::text, ('cancelled'::character varying)::text])))
);


--
-- Name: command_idempotency_receipts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.command_idempotency_receipts (
    idempotency_key character varying(128) NOT NULL,
    task_id bigint NOT NULL,
    action_kind character varying(32) NOT NULL,
    target_id character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_command_idem_receipt_action CHECK (((action_kind)::text = ANY (ARRAY[('pause_ad'::character varying)::text, ('activate_ad'::character varying)::text, ('abort_campaign_run'::character varying)::text, ('resume_campaign_run'::character varying)::text])))
);


--
-- Name: enable_recommendations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.enable_recommendations (
    ad_id uuid NOT NULL,
    snapshot_metrics jsonb NOT NULL,
    recommendation_level character varying(16) NOT NULL,
    live_batch_started_at timestamp with time zone NOT NULL,
    promoted_to_task_id bigint,
    idempotency_key character varying(128) NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: fb_ads; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fb_ads (
    adset_id uuid NOT NULL,
    fb_ad_id character varying(32) NOT NULL,
    ad_name character varying(255) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    delivery_status character varying(64),
    creative_thumb_url character varying(1024),
    creative_image_url character varying(1024)
);


--
-- Name: fb_adsets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fb_adsets (
    campaign_id uuid NOT NULL,
    fb_adset_id character varying(32),
    adset_name character varying(255) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    pixel_id character varying(64),
    daily_budget character varying(32),
    lifetime_budget character varying(32),
    budget_remaining character varying(32),
    learning_stage character varying(32)
);


--
-- Name: fb_campaigns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fb_campaigns (
    fb_campaign_id character varying(32),
    campaign_name character varying(255) NOT NULL,
    offer_id uuid,
    is_active boolean DEFAULT true NOT NULL,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    ad_account_id character varying(32) NOT NULL,
    CONSTRAINT ck_fb_campaigns_ad_account_identity CHECK (((ad_account_id)::text ~ '^[0-9]+$'::text))
);


--
-- Name: incidents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.incidents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    incident_key character varying(160) NOT NULL,
    generation integer DEFAULT 1 NOT NULL,
    resource_type character varying(32) NOT NULL,
    resource_id character varying(160) NOT NULL,
    ad_account_id character varying(64),
    severity character varying(16) NOT NULL,
    status character varying(16) DEFAULT 'open'::character varying NOT NULL,
    title character varying(200) NOT NULL,
    summary character varying(700),
    facts jsonb DEFAULT '{}'::jsonb NOT NULL,
    correlation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    opened_at timestamp with time zone DEFAULT now() NOT NULL,
    acknowledged_at timestamp with time zone,
    acknowledged_by character varying(128),
    resolved_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_incidents_incident_generation_positive CHECK ((generation > 0)),
    CONSTRAINT ck_incidents_incident_severity CHECK (((severity)::text = ANY (ARRAY[('ok'::character varying)::text, ('warning'::character varying)::text, ('critical'::character varying)::text, ('unknown'::character varying)::text]))),
    CONSTRAINT ck_incidents_incident_status CHECK (((status)::text = ANY (ARRAY[('open'::character varying)::text, ('acknowledged'::character varying)::text, ('executing'::character varying)::text, ('resolved'::character varying)::text, ('failed'::character varying)::text])))
);


--
-- Name: meta_account_snapshot; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meta_account_snapshot (
    account_id character varying(32) NOT NULL,
    timezone_name character varying(128),
    currency character varying(3),
    currency_observed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_meta_account_snapshot_currency CHECK (((currency IS NULL) OR ((currency)::text ~ '^[A-Z]{3}$'::text))),
    CONSTRAINT ck_meta_account_snapshot_currency_observation CHECK (((currency IS NULL) = (currency_observed_at IS NULL)))
);


--
-- Name: meta_api_audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meta_api_audit_log (
    id bigint NOT NULL,
    endpoint character varying(128) NOT NULL,
    http_method character varying(8) NOT NULL,
    http_status integer NOT NULL,
    ad_account_id character varying(32),
    initiated_by character varying(64) NOT NULL,
    request_payload jsonb,
    response_payload jsonb,
    duration_ms integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
)
PARTITION BY RANGE (created_at);


--
-- Name: meta_api_audit_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.meta_api_audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: meta_api_audit_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.meta_api_audit_log_id_seq OWNED BY public.meta_api_audit_log.id;


--
-- Name: meta_api_audit_log_default; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meta_api_audit_log_default (
    id bigint DEFAULT nextval('public.meta_api_audit_log_id_seq'::regclass) NOT NULL,
    endpoint character varying(128) NOT NULL,
    http_method character varying(8) NOT NULL,
    http_status integer NOT NULL,
    ad_account_id character varying(32),
    initiated_by character varying(64) NOT NULL,
    request_payload jsonb,
    response_payload jsonb,
    duration_ms integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: meta_shadow_spend_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.meta_shadow_spend_state (
    account_id character varying(32) NOT NULL,
    currency character varying(3) NOT NULL,
    samples jsonb DEFAULT '[]'::jsonb NOT NULL,
    cabinet_day_start timestamp with time zone NOT NULL,
    incident_baseline_at timestamp with time zone,
    incident_baseline_billing_minor bigint,
    incident_baseline_reported_minor bigint,
    recovery_candidate_at timestamp with time zone,
    last_observed_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_meta_shadow_spend_state_meta_shadow_baseline_complete CHECK ((((incident_baseline_at IS NULL) AND (incident_baseline_billing_minor IS NULL) AND (incident_baseline_reported_minor IS NULL)) OR ((incident_baseline_at IS NOT NULL) AND (incident_baseline_billing_minor IS NOT NULL) AND (incident_baseline_reported_minor IS NOT NULL)))),
    CONSTRAINT ck_meta_shadow_spend_state_candidate_requires_baseline CHECK (((recovery_candidate_at IS NULL) OR (incident_baseline_at IS NOT NULL)))
);


--
-- Name: notification_deliveries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_deliveries (
    id bigint NOT NULL,
    event_id uuid NOT NULL,
    recipient_id uuid NOT NULL,
    bot_generation integer NOT NULL,
    channel character varying(16) DEFAULT 'telegram'::character varying NOT NULL,
    state character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 8 NOT NULL,
    scheduled_at timestamp with time zone DEFAULT now() NOT NULL,
    lease_owner character varying(96),
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    external_started_at timestamp with time zone,
    telegram_chat_id bigint,
    telegram_message_id bigint,
    sent_at timestamp with time zone,
    completed_at timestamp with time zone,
    last_error_code character varying(64),
    last_error_detail character varying(500),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    external_operation_kind character varying(8),
    CONSTRAINT ck_notification_deliveries_external_operation_kind CHECK (((external_operation_kind IS NULL) OR ((external_operation_kind)::text = ANY (ARRAY[('send'::character varying)::text, ('edit'::character varying)::text])))),
    CONSTRAINT ck_notification_deliveries_notification_attempt_nonnegative CHECK ((attempt_count >= 0)),
    CONSTRAINT ck_notification_deliveries_notification_bot_generation CHECK ((bot_generation > 0)),
    CONSTRAINT ck_notification_deliveries_notification_delivery_state CHECK (((state)::text = ANY (ARRAY[('pending'::character varying)::text, ('leased'::character varying)::text, ('retry'::character varying)::text, ('sent'::character varying)::text, ('dead'::character varying)::text, ('superseded'::character varying)::text, ('unknown'::character varying)::text]))),
    CONSTRAINT ck_notification_deliveries_notification_max_attempts_positive CHECK ((max_attempts > 0)),
    CONSTRAINT ck_notification_deliveries_notification_message_id_positive CHECK (((telegram_message_id IS NULL) OR (telegram_message_id > 0)))
);


--
-- Name: notification_deliveries_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.notification_deliveries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: notification_deliveries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.notification_deliveries_id_seq OWNED BY public.notification_deliveries.id;


--
-- Name: notification_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.notification_events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    incident_id uuid,
    event_type character varying(64) NOT NULL,
    severity character varying(16) NOT NULL,
    audience character varying(32) NOT NULL,
    template_version integer DEFAULT 1 NOT NULL,
    facts jsonb NOT NULL,
    actions jsonb DEFAULT '[]'::jsonb NOT NULL,
    dedupe_key character varying(200) NOT NULL,
    correlation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_notification_events_notification_event_severity CHECK (((severity)::text = ANY (ARRAY[('ok'::character varying)::text, ('warning'::character varying)::text, ('critical'::character varying)::text, ('unknown'::character varying)::text]))),
    CONSTRAINT ck_notification_events_notification_template_version_positive CHECK ((template_version > 0))
);


--
-- Name: observer_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.observer_config (
    interval_seconds integer DEFAULT 30 NOT NULL,
    is_scanning_enabled boolean DEFAULT false NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    singleton_key character varying(16) DEFAULT 'default'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    auto_enable_recommendations boolean DEFAULT false NOT NULL,
    owner_campaign_tag character varying(255),
    campaign_ids text[] DEFAULT '{}'::text[] NOT NULL
);


--
-- Name: offer_creative_seq; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.offer_creative_seq (
    offer_code character varying(64) NOT NULL,
    next_seq integer DEFAULT 0 NOT NULL
);


--
-- Name: offer_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.offer_rules (
    offer_id uuid NOT NULL,
    cpa_threshold numeric(20,6),
    currency character varying(3),
    frequency_threshold numeric(5,2),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    stop_percent_of_rule numeric(5,2) DEFAULT '80'::numeric NOT NULL,
    warning_percent_of_stop numeric(5,2) DEFAULT '80'::numeric NOT NULL
);


--
-- Name: offers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.offers (
    code character varying(32) NOT NULL,
    name character varying(128) NOT NULL,
    vertical character varying(32),
    is_active boolean DEFAULT true NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    ad_account_ids character varying[] DEFAULT '{}'::character varying[] NOT NULL,
    pixel_id character varying(64),
    countries character varying[] DEFAULT '{}'::character varying[] NOT NULL
);


--
-- Name: operator_revision_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.operator_revision_events (
    revision bigint NOT NULL,
    scope character varying(64) NOT NULL,
    event_id character varying(256),
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: operator_revision_events_revision_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.operator_revision_events ALTER COLUMN revision ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.operator_revision_events_revision_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: operator_revision_state; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.operator_revision_state AS
 SELECT 'operator'::character varying(16) AS singleton_key,
    COALESCE(max(revision), (0)::bigint) AS revision,
    COALESCE(max(created_at), clock_timestamp()) AS updated_at
   FROM public.operator_revision_events;


--
-- Name: panel_login_tickets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.panel_login_tickets (
    ticket_digest bytea NOT NULL,
    telegram_user_id bigint NOT NULL,
    source character varying(32) NOT NULL,
    return_to character varying(2048) NOT NULL,
    issued_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_panel_login_tickets_positive_telegram_user_id CHECK ((telegram_user_id > 0)),
    CONSTRAINT ck_panel_login_tickets_ticket_digest_sha256 CHECK ((octet_length(ticket_digest) = 32)),
    CONSTRAINT ck_panel_login_tickets_valid_expiry CHECK ((expires_at > issued_at))
);


--
-- Name: panel_oidc_attempts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.panel_oidc_attempts (
    state_digest bytea NOT NULL,
    nonce character varying(128) NOT NULL,
    code_verifier character varying(128) NOT NULL,
    return_to character varying(2048) NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_panel_oidc_attempts_state_digest_sha256 CHECK ((octet_length(state_digest) = 32)),
    CONSTRAINT ck_panel_oidc_attempts_valid_expiry CHECK ((expires_at > created_at))
);


--
-- Name: panel_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.panel_sessions (
    token_digest bytea NOT NULL,
    telegram_user_id bigint NOT NULL,
    role character varying(16) NOT NULL,
    source character varying(32) NOT NULL,
    issued_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    CONSTRAINT ck_panel_sessions_owner_role CHECK (((role)::text = 'owner'::text)),
    CONSTRAINT ck_panel_sessions_positive_telegram_user_id CHECK ((telegram_user_id > 0)),
    CONSTRAINT ck_panel_sessions_token_digest_sha256 CHECK ((octet_length(token_digest) = 32)),
    CONSTRAINT ck_panel_sessions_valid_expiry CHECK ((expires_at > issued_at))
);


--
-- Name: scan_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scan_runs (
    id bigint NOT NULL,
    scan_id bigint NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    outcome character varying(32),
    rows_total integer,
    alerts_warning integer,
    alerts_stop integer,
    error_message text,
    duration_ms integer,
    ad_account_id character varying(32) NOT NULL,
    CONSTRAINT ck_scan_runs_ad_account_identity CHECK (((ad_account_id)::text ~ '^[0-9]+$'::text))
)
PARTITION BY RANGE (started_at);


--
-- Name: scan_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.scan_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: scan_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.scan_runs_id_seq OWNED BY public.scan_runs.id;


--
-- Name: scan_runs_default; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.scan_runs_default (
    id bigint DEFAULT nextval('public.scan_runs_id_seq'::regclass) NOT NULL,
    scan_id bigint NOT NULL,
    started_at timestamp with time zone NOT NULL,
    finished_at timestamp with time zone,
    outcome character varying(32),
    rows_total integer,
    alerts_warning integer,
    alerts_stop integer,
    error_message text,
    duration_ms integer,
    ad_account_id character varying(32) NOT NULL,
    CONSTRAINT ck_scan_runs_ad_account_identity CHECK (((ad_account_id)::text ~ '^[0-9]+$'::text))
);


--
-- Name: system_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.system_config (
    key character varying(64) NOT NULL,
    value jsonb NOT NULL,
    description character varying,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: task_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.task_queue (
    task_type character varying(32) NOT NULL,
    status character varying(16) NOT NULL,
    idempotency_key character varying(128) NOT NULL,
    payload jsonb NOT NULL,
    result jsonb,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 5 NOT NULL,
    last_error text,
    requested_by character varying(64) NOT NULL,
    completed_at timestamp with time zone,
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by_chat_id bigint,
    external_started_at timestamp with time zone,
    lane character varying(16) NOT NULL,
    priority integer DEFAULT 0 NOT NULL,
    available_at timestamp with time zone DEFAULT now() NOT NULL,
    deadline_at timestamp with time zone,
    lease_owner uuid,
    lease_token bigint DEFAULT 0 NOT NULL,
    lease_expires_at timestamp with time zone,
    cancel_requested_at timestamp with time zone,
    cancel_reason text,
    correlation_id uuid DEFAULT gen_random_uuid() NOT NULL,
    CONSTRAINT ck_task_queue_ck_task_queue_status CHECK (((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('running'::character varying)::text, ('succeeded'::character varying)::text, ('failed'::character varying)::text, ('retrying'::character varying)::text, ('cancelled'::character varying)::text]))),
    CONSTRAINT ck_task_queue_lane CHECK (((lane)::text = ANY (ARRAY[('money'::character varying)::text, ('interactive'::character varying)::text, ('bulk'::character varying)::text, ('background'::character varying)::text]))),
    CONSTRAINT ck_task_queue_meta_account_identity CHECK ((((task_type)::text <> 'meta_api_mutation'::text) OR COALESCE(((jsonb_typeof((payload -> 'ad_account_id'::text)) = 'string'::text) AND ((payload ->> 'ad_account_id'::text) ~ '^[0-9]+$'::text)), false))),
    CONSTRAINT ck_task_queue_task_type CHECK (((task_type)::text = ANY (ARRAY[('meta_api_mutation'::character varying)::text, ('observer_scan'::character varying)::text, ('campaign_create'::character varying)::text, ('tracker_event_process'::character varying)::text])))
);


--
-- Name: task_queue_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.task_queue_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: task_queue_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.task_queue_id_seq OWNED BY public.task_queue.id;


--
-- Name: telegram_action_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.telegram_action_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    token_digest bytea NOT NULL,
    delivery_id bigint,
    event_id uuid,
    incident_id uuid,
    recipient_id uuid NOT NULL,
    action_key character varying(64) NOT NULL,
    action_kind character varying(32) NOT NULL,
    target_type character varying(32) NOT NULL,
    target_id character varying(160) NOT NULL,
    target_payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    required_role character varying(16) DEFAULT 'owner'::character varying NOT NULL,
    incident_generation integer,
    expires_at timestamp with time zone NOT NULL,
    claimed_at timestamp with time zone,
    claim_key character varying(128),
    command_idempotency_key character varying(128),
    consumed_at timestamp with time zone,
    revoked_at timestamp with time zone,
    task_id bigint,
    failure_code character varying(64),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_telegram_action_tokens_telegram_action_generation CHECK (((incident_generation IS NULL) OR (incident_generation > 0))),
    CONSTRAINT ck_telegram_action_tokens_telegram_action_role CHECK (((required_role)::text = ANY (ARRAY[('owner'::character varying)::text, ('recipient'::character varying)::text])))
);


--
-- Name: telegram_command_replies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.telegram_command_replies (
    id bigint NOT NULL,
    bot_generation integer NOT NULL,
    update_id bigint NOT NULL,
    ordinal integer NOT NULL,
    chat_id bigint NOT NULL,
    text text NOT NULL,
    parse_mode character varying(8),
    reply_to_message_id bigint,
    reply_markup jsonb,
    state character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 8 NOT NULL,
    scheduled_at timestamp with time zone DEFAULT now() NOT NULL,
    lease_owner character varying(96),
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    external_started_at timestamp with time zone,
    telegram_message_id bigint,
    completed_at timestamp with time zone,
    last_error_code character varying(64),
    last_error_detail character varying(500),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_telegram_command_replies_telegram_command_reply_attempts CHECK ((attempt_count >= 0)),
    CONSTRAINT ck_telegram_command_replies_telegram_command_reply_bot__421d CHECK ((bot_generation > 0)),
    CONSTRAINT ck_telegram_command_replies_telegram_command_reply_chat CHECK ((chat_id <> 0)),
    CONSTRAINT ck_telegram_command_replies_telegram_command_reply_max_attempts CHECK ((max_attempts > 0)),
    CONSTRAINT ck_telegram_command_replies_telegram_command_reply_message CHECK (((telegram_message_id IS NULL) OR (telegram_message_id > 0))),
    CONSTRAINT ck_telegram_command_replies_telegram_command_reply_ordinal CHECK ((ordinal >= 0)),
    CONSTRAINT ck_telegram_command_replies_telegram_command_reply_parse_mode CHECK (((parse_mode IS NULL) OR ((parse_mode)::text = 'HTML'::text))),
    CONSTRAINT ck_telegram_command_replies_telegram_command_reply_state CHECK (((state)::text = ANY (ARRAY[('pending'::character varying)::text, ('leased'::character varying)::text, ('retry'::character varying)::text, ('sent'::character varying)::text, ('dead'::character varying)::text, ('unknown'::character varying)::text]))),
    CONSTRAINT ck_telegram_command_replies_telegram_command_reply_target CHECK (((reply_to_message_id IS NULL) OR (reply_to_message_id > 0))),
    CONSTRAINT ck_telegram_command_replies_telegram_command_reply_text_length CHECK (((char_length(text) >= 1) AND (char_length(text) <= 4096)))
);


--
-- Name: telegram_command_replies_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.telegram_command_replies_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: telegram_command_replies_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.telegram_command_replies_id_seq OWNED BY public.telegram_command_replies.id;


--
-- Name: telegram_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.telegram_config (
    bot_token_encrypted character varying NOT NULL,
    bot_token_fingerprint bytea,
    is_enabled boolean DEFAULT true NOT NULL,
    webhook_generation integer DEFAULT 0 NOT NULL,
    webhook_applied_generation integer,
    webhook_operation character varying(16),
    webhook_desired_url character varying(2048),
    webhook_secret_digest bytea,
    webhook_state character varying(16) DEFAULT 'unconfigured'::character varying NOT NULL,
    webhook_scheduled_at timestamp with time zone,
    webhook_attempt_count integer DEFAULT 0 NOT NULL,
    webhook_lease_owner character varying(96),
    webhook_lease_token uuid,
    webhook_lease_expires_at timestamp with time zone,
    webhook_remote_url character varying(2048),
    webhook_remote_pending_update_count integer,
    webhook_remote_last_error_at timestamp with time zone,
    webhook_remote_last_error_message character varying(500),
    webhook_remote_max_connections integer,
    webhook_remote_allowed_updates jsonb,
    webhook_checked_at timestamp with time zone,
    webhook_configured_at timestamp with time zone,
    webhook_last_error_code character varying(64),
    webhook_last_error_detail character varying(500),
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    singleton_key character varying(16) DEFAULT 'default'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_telegram_config_bot_token_fingerprint_sha256 CHECK (((bot_token_fingerprint IS NULL) OR (octet_length(bot_token_fingerprint) = 32))),
    CONSTRAINT ck_telegram_config_webhook_attempt_count CHECK ((webhook_attempt_count >= 0)),
    CONSTRAINT ck_telegram_config_webhook_desired_url_https CHECK (((webhook_desired_url IS NULL) OR ((webhook_desired_url)::text ~~ 'https://%'::text))),
    CONSTRAINT ck_telegram_config_webhook_generation CHECK (((webhook_generation >= 0) AND ((webhook_applied_generation IS NULL) OR ((webhook_applied_generation >= 0) AND (webhook_applied_generation <= webhook_generation))))),
    CONSTRAINT ck_telegram_config_webhook_operation CHECK (((webhook_operation IS NULL) OR ((webhook_operation)::text = ANY ((ARRAY['configure'::character varying, 'delete'::character varying])::text[])))),
    CONSTRAINT ck_telegram_config_webhook_remote_pending_nonnegative CHECK (((webhook_remote_pending_update_count IS NULL) OR (webhook_remote_pending_update_count >= 0))),
    CONSTRAINT ck_telegram_config_webhook_secret_digest_sha256 CHECK (((webhook_secret_digest IS NULL) OR (octet_length(webhook_secret_digest) = 32))),
    CONSTRAINT ck_telegram_config_webhook_state CHECK (((webhook_state)::text = ANY ((ARRAY['unconfigured'::character varying, 'pending'::character varying, 'applying'::character varying, 'retry'::character varying, 'configured'::character varying, 'failed'::character varying])::text[])))
);


--
-- Name: telegram_invites; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.telegram_invites (
    code character varying(32) NOT NULL,
    created_by character varying(64) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    used_by character varying(64),
    revoked_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    role character varying(16) DEFAULT 'recipient'::character varying NOT NULL,
    CONSTRAINT ck_telegram_invites_telegram_invite_role CHECK (((role)::text = ANY (ARRAY[('owner'::character varying)::text, ('recipient'::character varying)::text])))
);


--
-- Name: telegram_message_slots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.telegram_message_slots (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    incident_id uuid NOT NULL,
    recipient_id uuid NOT NULL,
    last_event_id uuid NOT NULL,
    chat_id bigint NOT NULL,
    message_id bigint NOT NULL,
    incident_generation integer NOT NULL,
    state character varying(16) NOT NULL,
    render_hash bytea NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_telegram_message_slots_telegram_message_slot_generation CHECK ((incident_generation > 0)),
    CONSTRAINT ck_telegram_message_slots_message_positive CHECK ((message_id > 0))
);


--
-- Name: telegram_navigation_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.telegram_navigation_tokens (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    token_digest bytea NOT NULL,
    recipient_id uuid NOT NULL,
    delivery_id bigint,
    event_id uuid,
    target_kind character varying(16) NOT NULL,
    target_id character varying(160) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    consumed_at timestamp with time zone,
    revoked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_telegram_navigation_tokens_telegram_navigation_target_kind CHECK (((target_kind)::text = ANY (ARRAY[('ad'::character varying)::text, ('action'::character varying)::text, ('incident'::character varying)::text])))
);


--
-- Name: telegram_recipient_preferences; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.telegram_recipient_preferences (
    recipient_id uuid NOT NULL,
    timezone character varying(64) DEFAULT 'Europe/Kaliningrad'::character varying NOT NULL,
    min_severity character varying(16) DEFAULT 'warning'::character varying NOT NULL,
    quiet_hours_start time without time zone,
    quiet_hours_end time without time zone,
    digest_local_time time without time zone,
    categories jsonb DEFAULT '{}'::jsonb NOT NULL,
    is_enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_telegram_recipient_preferences_telegram_preference_severity CHECK (((min_severity)::text = ANY (ARRAY[('ok'::character varying)::text, ('warning'::character varying)::text, ('critical'::character varying)::text, ('unknown'::character varying)::text])))
);


--
-- Name: telegram_recipients; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.telegram_recipients (
    chat_id bigint NOT NULL,
    telegram_user_id bigint NOT NULL,
    username character varying(64),
    display_name character varying(128),
    role character varying(16) NOT NULL,
    invite_id uuid,
    revoked_at timestamp with time zone,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_telegram_recipients_telegram_recipient_role CHECK (((role)::text = ANY (ARRAY[('owner'::character varying)::text, ('recipient'::character varying)::text])))
);


--
-- Name: telegram_updates_inbox; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.telegram_updates_inbox (
    bot_generation integer NOT NULL,
    update_id bigint NOT NULL,
    payload jsonb NOT NULL,
    state character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    scheduled_at timestamp with time zone DEFAULT now() NOT NULL,
    lease_owner character varying(96),
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    processed_at timestamp with time zone,
    last_error_code character varying(64),
    last_error_detail character varying(500),
    CONSTRAINT ck_telegram_updates_inbox_telegram_update_attempt_nonnegative CHECK ((attempt_count >= 0)),
    CONSTRAINT ck_telegram_updates_inbox_telegram_update_bot_generation CHECK ((bot_generation > 0)),
    CONSTRAINT ck_telegram_updates_inbox_telegram_update_inbox_state CHECK (((state)::text = ANY (ARRAY[('pending'::character varying)::text, ('leased'::character varying)::text, ('retry'::character varying)::text, ('processed'::character varying)::text, ('dead'::character varying)::text])))
);


--
-- Name: tracker_click_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.tracker_click_state (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source character varying(32) NOT NULL,
    click_id character varying(128) NOT NULL,
    ad_id uuid,
    fb_ad_id character varying(64),
    country character varying(2),
    attribution_status character varying(32) DEFAULT 'unmatched'::character varying NOT NULL,
    registration boolean DEFAULT false NOT NULL,
    ftd boolean DEFAULT false NOT NULL,
    confirmed_deposit boolean DEFAULT false NOT NULL,
    registration_at timestamp with time zone,
    ftd_at timestamp with time zone,
    confirmed_deposit_at timestamp with time zone,
    redeposits integer DEFAULT 0 NOT NULL,
    last_event_at timestamp with time zone NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: vision_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vision_config (
    x_token_encrypted character varying NOT NULL,
    profile_id character varying(64) NOT NULL,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    singleton_key character varying(16) DEFAULT 'default'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ad_metrics_default; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ad_metrics ATTACH PARTITION public.ad_metrics_default DEFAULT;


--
-- Name: adsetpro_postback_events_default; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adsetpro_postback_events ATTACH PARTITION public.adsetpro_postback_events_default DEFAULT;


--
-- Name: alert_events_default; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_events ATTACH PARTITION public.alert_events_default DEFAULT;


--
-- Name: meta_api_audit_log_default; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meta_api_audit_log ATTACH PARTITION public.meta_api_audit_log_default DEFAULT;


--
-- Name: scan_runs_default; Type: TABLE ATTACH; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scan_runs ATTACH PARTITION public.scan_runs_default DEFAULT;


--
-- Name: adsetpro_postback_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adsetpro_postback_events ALTER COLUMN id SET DEFAULT nextval('public.adsetpro_postback_events_id_seq'::regclass);


--
-- Name: meta_api_audit_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meta_api_audit_log ALTER COLUMN id SET DEFAULT nextval('public.meta_api_audit_log_id_seq'::regclass);


--
-- Name: notification_deliveries id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_deliveries ALTER COLUMN id SET DEFAULT nextval('public.notification_deliveries_id_seq'::regclass);


--
-- Name: scan_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scan_runs ALTER COLUMN id SET DEFAULT nextval('public.scan_runs_id_seq'::regclass);


--
-- Name: task_queue id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_queue ALTER COLUMN id SET DEFAULT nextval('public.task_queue_id_seq'::regclass);


--
-- Name: telegram_command_replies id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_command_replies ALTER COLUMN id SET DEFAULT nextval('public.telegram_command_replies_id_seq'::regclass);


--
-- Name: ad_metrics uq_ad_metrics_ad_cycle; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ad_metrics
    ADD CONSTRAINT uq_ad_metrics_ad_cycle UNIQUE (ad_id, cycle_ts);


--
-- Name: ad_metrics_default ad_metrics_default_ad_id_cycle_ts_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ad_metrics_default
    ADD CONSTRAINT ad_metrics_default_ad_id_cycle_ts_key UNIQUE (ad_id, cycle_ts);


--
-- Name: ad_metrics pk_ad_metrics; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ad_metrics
    ADD CONSTRAINT pk_ad_metrics PRIMARY KEY (id, cycle_ts);


--
-- Name: ad_metrics_default ad_metrics_default_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ad_metrics_default
    ADD CONSTRAINT ad_metrics_default_pkey PRIMARY KEY (id, cycle_ts);


--
-- Name: adset_duplicate_previews pk_adset_duplicate_previews; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adset_duplicate_previews
    ADD CONSTRAINT pk_adset_duplicate_previews PRIMARY KEY (token_digest);


--
-- Name: adsetpro_credentials adsetpro_credentials_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adsetpro_credentials
    ADD CONSTRAINT adsetpro_credentials_pkey PRIMARY KEY (id);


--
-- Name: adsetpro_postback_events uq_adsetpro_postback_dedup; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adsetpro_postback_events
    ADD CONSTRAINT uq_adsetpro_postback_dedup UNIQUE (click_id, event_type, received_at);


--
-- Name: adsetpro_postback_events_default adsetpro_postback_events_defa_click_id_event_type_received__key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adsetpro_postback_events_default
    ADD CONSTRAINT adsetpro_postback_events_defa_click_id_event_type_received__key UNIQUE (click_id, event_type, received_at);


--
-- Name: adsetpro_postback_events adsetpro_postback_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adsetpro_postback_events
    ADD CONSTRAINT adsetpro_postback_events_pkey PRIMARY KEY (id, received_at);


--
-- Name: adsetpro_postback_events_default adsetpro_postback_events_default_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adsetpro_postback_events_default
    ADD CONSTRAINT adsetpro_postback_events_default_pkey PRIMARY KEY (id, received_at);


--
-- Name: alert_events pk_alert_events; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_events
    ADD CONSTRAINT pk_alert_events PRIMARY KEY (id, created_at);


--
-- Name: alert_events_default alert_events_default_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alert_events_default
    ADD CONSTRAINT alert_events_default_pkey PRIMARY KEY (id, created_at);


--
-- Name: meta_api_audit_log pk_meta_api_audit_log; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meta_api_audit_log
    ADD CONSTRAINT pk_meta_api_audit_log PRIMARY KEY (id, created_at);


--
-- Name: meta_api_audit_log_default meta_api_audit_log_default_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meta_api_audit_log_default
    ADD CONSTRAINT meta_api_audit_log_default_pkey PRIMARY KEY (id, created_at);


--
-- Name: ad_alert_state pk_ad_alert_state; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ad_alert_state
    ADD CONSTRAINT pk_ad_alert_state PRIMARY KEY (id);


--
-- Name: ad_auto_enable_disabled pk_ad_auto_enable_disabled; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ad_auto_enable_disabled
    ADD CONSTRAINT pk_ad_auto_enable_disabled PRIMARY KEY (id);


--
-- Name: browser_channel_readiness ck_browser_channel_readiness_channel; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.browser_channel_readiness
    ADD CONSTRAINT ck_browser_channel_readiness_channel CHECK (((channel)::text = 'meta_api'::text));


--
-- Name: browser_channel_readiness ck_browser_channel_readiness_evidence; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.browser_channel_readiness
    ADD CONSTRAINT ck_browser_channel_readiness_evidence CHECK (((((state)::text = 'ready'::text) AND (observed_contract_version = 5) AND ((observed_profile_id)::text = (expected_profile_id)::text) AND (observed_session_id IS NOT NULL) AND (length((observed_session_id)::text) > 0) AND (readiness_expires_at IS NOT NULL) AND (readiness_expires_at > observed_at)) OR (((state)::text <> 'ready'::text) AND (readiness_expires_at IS NULL))));


--
-- Name: browser_channel_readiness ck_browser_channel_readiness_state; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.browser_channel_readiness
    ADD CONSTRAINT ck_browser_channel_readiness_state CHECK (((state)::text = ANY ((ARRAY['ready'::character varying, 'unavailable'::character varying, 'incompatible'::character varying, 'profile_mismatch'::character varying, 'maintenance'::character varying])::text[])));


--
-- Name: browser_channel_readiness pk_browser_channel_readiness; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.browser_channel_readiness
    ADD CONSTRAINT pk_browser_channel_readiness PRIMARY KEY (channel);


--
-- Name: browser_operation_leases pk_browser_operation_leases; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.browser_operation_leases
    ADD CONSTRAINT pk_browser_operation_leases PRIMARY KEY (operation_id);


--
-- Name: browser_operation_capability_uses ck_browser_operation_capability_caller; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.browser_operation_capability_uses
    ADD CONSTRAINT ck_browser_operation_capability_caller CHECK (((caller)::text = ANY ((ARRAY['autopause'::character varying, 'meta_api'::character varying, 'campaign_creator'::character varying])::text[])));


--
-- Name: browser_operation_capability_uses ck_browser_operation_capability_contract_version; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.browser_operation_capability_uses
    ADD CONSTRAINT ck_browser_operation_capability_contract_version CHECK ((browser_contract_version = 5));


--
-- Name: browser_operation_capability_uses ck_browser_operation_capability_rpc; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.browser_operation_capability_uses
    ADD CONSTRAINT ck_browser_operation_capability_rpc CHECK (((rpc)::text = ANY ((ARRAY['execute_graph_call'::character varying, 'upload_image'::character varying, 'upload_video'::character varying])::text[])));


--
-- Name: browser_operation_capability_uses pk_browser_operation_capability_uses; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.browser_operation_capability_uses
    ADD CONSTRAINT pk_browser_operation_capability_uses PRIMARY KEY (nonce_sha256);


--
-- Name: browser_operation_capability_uses uq_browser_operation_capability_uses_capability_digest; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.browser_operation_capability_uses
    ADD CONSTRAINT uq_browser_operation_capability_uses_capability_digest UNIQUE (capability_digest);


--
-- Name: cabinet_runtime pk_cabinet_runtime; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.cabinet_runtime
    ADD CONSTRAINT pk_cabinet_runtime PRIMARY KEY (ad_account_id);


--
-- Name: campaign_creative pk_campaign_creative; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_creative
    ADD CONSTRAINT pk_campaign_creative PRIMARY KEY (id);


--
-- Name: campaign_preset pk_campaign_preset; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_preset
    ADD CONSTRAINT pk_campaign_preset PRIMARY KEY (id);


--
-- Name: campaign_run pk_campaign_run; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_run
    ADD CONSTRAINT pk_campaign_run PRIMARY KEY (id);


--
-- Name: command_idempotency_receipts pk_command_idempotency_receipts; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.command_idempotency_receipts
    ADD CONSTRAINT pk_command_idempotency_receipts PRIMARY KEY (idempotency_key);


--
-- Name: enable_recommendations pk_enable_recommendations; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.enable_recommendations
    ADD CONSTRAINT pk_enable_recommendations PRIMARY KEY (id);


--
-- Name: fb_ads pk_fb_ads; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fb_ads
    ADD CONSTRAINT pk_fb_ads PRIMARY KEY (id);


--
-- Name: fb_adsets pk_fb_adsets; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fb_adsets
    ADD CONSTRAINT pk_fb_adsets PRIMARY KEY (id);


--
-- Name: fb_campaigns pk_fb_campaigns; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fb_campaigns
    ADD CONSTRAINT pk_fb_campaigns PRIMARY KEY (id);


--
-- Name: incidents pk_incidents; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.incidents
    ADD CONSTRAINT pk_incidents PRIMARY KEY (id);


--
-- Name: meta_account_snapshot pk_meta_account_snapshot; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meta_account_snapshot
    ADD CONSTRAINT pk_meta_account_snapshot PRIMARY KEY (account_id);


--
-- Name: meta_shadow_spend_state pk_meta_shadow_spend_state; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.meta_shadow_spend_state
    ADD CONSTRAINT pk_meta_shadow_spend_state PRIMARY KEY (account_id);


--
-- Name: notification_deliveries pk_notification_deliveries; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_deliveries
    ADD CONSTRAINT pk_notification_deliveries PRIMARY KEY (id);


--
-- Name: notification_events pk_notification_events; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_events
    ADD CONSTRAINT pk_notification_events PRIMARY KEY (id);


--
-- Name: observer_config pk_observer_config; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.observer_config
    ADD CONSTRAINT pk_observer_config PRIMARY KEY (id);


--
-- Name: offer_creative_seq pk_offer_creative_seq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.offer_creative_seq
    ADD CONSTRAINT pk_offer_creative_seq PRIMARY KEY (offer_code);


--
-- Name: offer_rules pk_offer_rules; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.offer_rules
    ADD CONSTRAINT pk_offer_rules PRIMARY KEY (id);


--
-- Name: offers pk_offers; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.offers
    ADD CONSTRAINT pk_offers PRIMARY KEY (id);


--
-- Name: operator_revision_events pk_operator_revision_events; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.operator_revision_events
    ADD CONSTRAINT pk_operator_revision_events PRIMARY KEY (revision);


--
-- Name: panel_login_tickets pk_panel_login_tickets; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.panel_login_tickets
    ADD CONSTRAINT pk_panel_login_tickets PRIMARY KEY (ticket_digest);


--
-- Name: panel_oidc_attempts pk_panel_oidc_attempts; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.panel_oidc_attempts
    ADD CONSTRAINT pk_panel_oidc_attempts PRIMARY KEY (state_digest);


--
-- Name: panel_sessions pk_panel_sessions; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.panel_sessions
    ADD CONSTRAINT pk_panel_sessions PRIMARY KEY (token_digest);


--
-- Name: scan_runs pk_scan_runs; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scan_runs
    ADD CONSTRAINT pk_scan_runs PRIMARY KEY (id, started_at);


--
-- Name: system_config pk_system_config; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_config
    ADD CONSTRAINT pk_system_config PRIMARY KEY (id);


--
-- Name: task_queue pk_task_queue; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_queue
    ADD CONSTRAINT pk_task_queue PRIMARY KEY (id);


--
-- Name: telegram_action_tokens pk_telegram_action_tokens; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_action_tokens
    ADD CONSTRAINT pk_telegram_action_tokens PRIMARY KEY (id);


--
-- Name: telegram_command_replies pk_telegram_command_replies; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_command_replies
    ADD CONSTRAINT pk_telegram_command_replies PRIMARY KEY (id);


--
-- Name: telegram_config pk_telegram_config; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_config
    ADD CONSTRAINT pk_telegram_config PRIMARY KEY (id);


--
-- Name: telegram_invites pk_telegram_invites; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_invites
    ADD CONSTRAINT pk_telegram_invites PRIMARY KEY (id);


--
-- Name: telegram_message_slots pk_telegram_message_slots; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_message_slots
    ADD CONSTRAINT pk_telegram_message_slots PRIMARY KEY (id);


--
-- Name: telegram_navigation_tokens pk_telegram_navigation_tokens; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_navigation_tokens
    ADD CONSTRAINT pk_telegram_navigation_tokens PRIMARY KEY (id);


--
-- Name: telegram_recipient_preferences pk_telegram_recipient_preferences; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_recipient_preferences
    ADD CONSTRAINT pk_telegram_recipient_preferences PRIMARY KEY (recipient_id);


--
-- Name: telegram_recipients pk_telegram_recipients; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_recipients
    ADD CONSTRAINT pk_telegram_recipients PRIMARY KEY (id);


--
-- Name: telegram_updates_inbox pk_telegram_updates_inbox; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_updates_inbox
    ADD CONSTRAINT pk_telegram_updates_inbox PRIMARY KEY (bot_generation, update_id);


--
-- Name: tracker_click_state pk_tracker_click_state; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tracker_click_state
    ADD CONSTRAINT pk_tracker_click_state PRIMARY KEY (id);


--
-- Name: vision_config pk_vision_config; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vision_config
    ADD CONSTRAINT pk_vision_config PRIMARY KEY (id);


--
-- Name: scan_runs_default scan_runs_default_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scan_runs_default
    ADD CONSTRAINT scan_runs_default_pkey PRIMARY KEY (id, started_at);


--
-- Name: scan_runs uq_scan_runs_scan_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scan_runs
    ADD CONSTRAINT uq_scan_runs_scan_id UNIQUE (scan_id, started_at);


--
-- Name: scan_runs_default scan_runs_default_scan_id_started_at_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.scan_runs_default
    ADD CONSTRAINT scan_runs_default_scan_id_started_at_key UNIQUE (scan_id, started_at);


--
-- Name: ad_alert_state uq_ad_alert_state_ad; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ad_alert_state
    ADD CONSTRAINT uq_ad_alert_state_ad UNIQUE (ad_id);


--
-- Name: ad_auto_enable_disabled uq_ad_auto_enable_disabled_ad; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ad_auto_enable_disabled
    ADD CONSTRAINT uq_ad_auto_enable_disabled_ad UNIQUE (ad_id);


--
-- Name: adsetpro_credentials uq_adsetpro_credentials_singleton_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adsetpro_credentials
    ADD CONSTRAINT uq_adsetpro_credentials_singleton_key UNIQUE (singleton_key);


--
-- Name: campaign_creative uq_campaign_creative_offer_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_creative
    ADD CONSTRAINT uq_campaign_creative_offer_code UNIQUE (offer_code, code);


--
-- Name: campaign_preset uq_campaign_preset_name; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_preset
    ADD CONSTRAINT uq_campaign_preset_name UNIQUE (name);


--
-- Name: campaign_run uq_campaign_run_idempotency_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_run
    ADD CONSTRAINT uq_campaign_run_idempotency_key UNIQUE (idempotency_key);


--
-- Name: enable_recommendations uq_enable_recommendations_idempotency; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.enable_recommendations
    ADD CONSTRAINT uq_enable_recommendations_idempotency UNIQUE (idempotency_key);


--
-- Name: fb_ads uq_fb_ads_fb_ad_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fb_ads
    ADD CONSTRAINT uq_fb_ads_fb_ad_id UNIQUE (fb_ad_id);


--
-- Name: fb_adsets uq_fb_adsets_campaign_adset; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fb_adsets
    ADD CONSTRAINT uq_fb_adsets_campaign_adset UNIQUE (campaign_id, adset_name);


--
-- Name: notification_deliveries uq_notification_delivery_event_recipient_channel; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_deliveries
    ADD CONSTRAINT uq_notification_delivery_event_recipient_channel UNIQUE (event_id, recipient_id, channel);


--
-- Name: notification_events uq_notification_events_dedupe_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_events
    ADD CONSTRAINT uq_notification_events_dedupe_key UNIQUE (dedupe_key);


--
-- Name: observer_config uq_observer_config_singleton_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.observer_config
    ADD CONSTRAINT uq_observer_config_singleton_key UNIQUE (singleton_key);


--
-- Name: offer_rules ck_offer_rules_cpa_threshold_positive_finite; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.offer_rules
    ADD CONSTRAINT ck_offer_rules_cpa_threshold_positive_finite CHECK (((cpa_threshold IS NULL) OR ((cpa_threshold > (0)::numeric) AND (cpa_threshold < 'Infinity'::numeric))));


--
-- Name: offer_rules ck_offer_rules_currency; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.offer_rules
    ADD CONSTRAINT ck_offer_rules_currency CHECK (((currency IS NULL) OR ((currency)::text ~ '^[A-Z]{3}$'::text)));


--
-- Name: offer_rules ck_offer_rules_cpa_currency_required; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.offer_rules
    ADD CONSTRAINT ck_offer_rules_cpa_currency_required CHECK (((cpa_threshold IS NULL) OR (currency IS NOT NULL)));


--
-- Name: offer_rules ck_offer_rules_frequency_threshold_positive_finite; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.offer_rules
    ADD CONSTRAINT ck_offer_rules_frequency_threshold_positive_finite CHECK (((frequency_threshold IS NULL) OR ((frequency_threshold > (0)::numeric) AND (frequency_threshold < 'Infinity'::numeric))));


--
-- Name: offer_rules ck_offer_rules_stop_percent_range; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.offer_rules
    ADD CONSTRAINT ck_offer_rules_stop_percent_range CHECK (((stop_percent_of_rule >= (1)::numeric) AND (stop_percent_of_rule <= (100)::numeric)));


--
-- Name: offer_rules ck_offer_rules_warning_percent_range; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.offer_rules
    ADD CONSTRAINT ck_offer_rules_warning_percent_range CHECK (((warning_percent_of_stop >= (1)::numeric) AND (warning_percent_of_stop <= (100)::numeric)));


--
-- Name: offer_rules uq_offer_rules_offer_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.offer_rules
    ADD CONSTRAINT uq_offer_rules_offer_id UNIQUE (offer_id);


--
-- Name: offers uq_offers_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.offers
    ADD CONSTRAINT uq_offers_code UNIQUE (code);


--
-- Name: system_config uq_system_config_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.system_config
    ADD CONSTRAINT uq_system_config_key UNIQUE (key);


--
-- Name: task_queue uq_task_queue_idempotency_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.task_queue
    ADD CONSTRAINT uq_task_queue_idempotency_key UNIQUE (idempotency_key);


--
-- Name: telegram_action_tokens uq_telegram_action_token_digest; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_action_tokens
    ADD CONSTRAINT uq_telegram_action_token_digest UNIQUE (token_digest);


--
-- Name: telegram_command_replies uq_telegram_command_reply_update_ordinal; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_command_replies
    ADD CONSTRAINT uq_telegram_command_reply_update_ordinal UNIQUE (bot_generation, update_id, ordinal);


--
-- Name: telegram_config uq_telegram_config_singleton_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_config
    ADD CONSTRAINT uq_telegram_config_singleton_key UNIQUE (singleton_key);


--
-- Name: telegram_invites uq_telegram_invites_code; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_invites
    ADD CONSTRAINT uq_telegram_invites_code UNIQUE (code);


--
-- Name: telegram_message_slots uq_telegram_message_slot_incident_recipient; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_message_slots
    ADD CONSTRAINT uq_telegram_message_slot_incident_recipient UNIQUE (incident_id, recipient_id);


--
-- Name: telegram_navigation_tokens uq_telegram_navigation_token_digest; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_navigation_tokens
    ADD CONSTRAINT uq_telegram_navigation_token_digest UNIQUE (token_digest);


--
-- Name: telegram_recipients uq_telegram_recipients_chat_user; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_recipients
    ADD CONSTRAINT uq_telegram_recipients_chat_user UNIQUE (chat_id, telegram_user_id);


--
-- Name: tracker_click_state uq_tracker_click_state_source_click; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tracker_click_state
    ADD CONSTRAINT uq_tracker_click_state_source_click UNIQUE (source, click_id);


--
-- Name: vision_config uq_vision_config_singleton_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vision_config
    ADD CONSTRAINT uq_vision_config_singleton_key UNIQUE (singleton_key);


--
-- Name: ix_ad_metrics_ad_cycle; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ad_metrics_ad_cycle ON ONLY public.ad_metrics USING btree (ad_id, cycle_ts);


--
-- Name: ad_metrics_default_ad_id_cycle_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ad_metrics_default_ad_id_cycle_ts_idx ON public.ad_metrics_default USING btree (ad_id, cycle_ts);


--
-- Name: ix_ad_metrics_scan; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ad_metrics_scan ON ONLY public.ad_metrics USING btree (scan_id) WHERE (scan_id IS NOT NULL);


--
-- Name: ad_metrics_default_scan_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ad_metrics_default_scan_id_idx ON public.ad_metrics_default USING btree (scan_id) WHERE (scan_id IS NOT NULL);


--
-- Name: ix_adset_duplicate_previews_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adset_duplicate_previews_expires_at ON public.adset_duplicate_previews USING btree (expires_at) WHERE (task_id IS NULL);


--
-- Name: ix_adset_duplicate_previews_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adset_duplicate_previews_task_id ON public.adset_duplicate_previews USING btree (task_id) WHERE (task_id IS NOT NULL);


--
-- Name: ix_adsetpro_postback_processing; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adsetpro_postback_processing ON ONLY public.adsetpro_postback_events USING btree (attribution_status, next_retry_at) WHERE (processed_at IS NULL);


--
-- Name: adsetpro_postback_events_defa_attribution_status_next_retry_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX adsetpro_postback_events_defa_attribution_status_next_retry_idx ON public.adsetpro_postback_events_default USING btree (attribution_status, next_retry_at) WHERE (processed_at IS NULL);


--
-- Name: ix_adsetpro_postback_click; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adsetpro_postback_click ON ONLY public.adsetpro_postback_events USING btree (click_id, event_type);


--
-- Name: adsetpro_postback_events_default_click_id_event_type_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX adsetpro_postback_events_default_click_id_event_type_idx ON public.adsetpro_postback_events_default USING btree (click_id, event_type);


--
-- Name: ix_adsetpro_postback_fb_ad; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adsetpro_postback_fb_ad ON ONLY public.adsetpro_postback_events USING btree (fb_ad_fk, received_at) WHERE (fb_ad_fk IS NOT NULL);


--
-- Name: adsetpro_postback_events_default_fb_ad_fk_received_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX adsetpro_postback_events_default_fb_ad_fk_received_at_idx ON public.adsetpro_postback_events_default USING btree (fb_ad_fk, received_at) WHERE (fb_ad_fk IS NOT NULL);


--
-- Name: ix_adsetpro_postback_fb_ad_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adsetpro_postback_fb_ad_id ON ONLY public.adsetpro_postback_events USING btree (fb_ad_id, received_at) WHERE (fb_ad_id IS NOT NULL);


--
-- Name: adsetpro_postback_events_default_fb_ad_id_received_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX adsetpro_postback_events_default_fb_ad_id_received_at_idx ON public.adsetpro_postback_events_default USING btree (fb_ad_id, received_at) WHERE (fb_ad_id IS NOT NULL);


--
-- Name: ix_adsetpro_postback_received; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adsetpro_postback_received ON ONLY public.adsetpro_postback_events USING btree (received_at);


--
-- Name: adsetpro_postback_events_default_received_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX adsetpro_postback_events_default_received_at_idx ON public.adsetpro_postback_events_default USING btree (received_at);


--
-- Name: ix_adsetpro_postback_source_click; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adsetpro_postback_source_click ON ONLY public.adsetpro_postback_events USING btree (source, click_id, event_type);


--
-- Name: adsetpro_postback_events_default_source_click_id_event_type_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX adsetpro_postback_events_default_source_click_id_event_type_idx ON public.adsetpro_postback_events_default USING btree (source, click_id, event_type);


--
-- Name: ix_adsetpro_postback_source_provider; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_adsetpro_postback_source_provider ON ONLY public.adsetpro_postback_events USING btree (source, provider_event_id) WHERE (provider_event_id IS NOT NULL);


--
-- Name: adsetpro_postback_events_default_source_provider_event_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX adsetpro_postback_events_default_source_provider_event_id_idx ON public.adsetpro_postback_events_default USING btree (source, provider_event_id) WHERE (provider_event_id IS NOT NULL);


--
-- Name: ix_alert_events_ad_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_events_ad_created ON ONLY public.alert_events USING btree (ad_id, created_at);


--
-- Name: alert_events_default_ad_id_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX alert_events_default_ad_id_created_at_idx ON public.alert_events_default USING btree (ad_id, created_at);


--
-- Name: ix_alert_events_token; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_events_token ON ONLY public.alert_events USING btree (open_state_token) WHERE (open_state_token IS NOT NULL);


--
-- Name: alert_events_default_open_state_token_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX alert_events_default_open_state_token_idx ON public.alert_events_default USING btree (open_state_token) WHERE (open_state_token IS NOT NULL);


--
-- Name: ix_alert_events_scan_id_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_events_scan_id_created ON ONLY public.alert_events USING btree (scan_id, created_at);


--
-- Name: alert_events_default_scan_id_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX alert_events_default_scan_id_created_at_idx ON public.alert_events_default USING btree (scan_id, created_at);


--
-- Name: ix_alert_events_stage; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_events_stage ON ONLY public.alert_events USING btree (stage);


--
-- Name: alert_events_default_stage_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX alert_events_default_stage_idx ON public.alert_events_default USING btree (stage);


--
-- Name: ix_alert_events_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_alert_events_state ON ONLY public.alert_events USING btree (state);


--
-- Name: alert_events_default_state_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX alert_events_default_state_idx ON public.alert_events_default USING btree (state);


--
-- Name: ix_ad_alert_state_last_scan; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ad_alert_state_last_scan ON public.ad_alert_state USING btree (last_scan_id);


--
-- Name: ix_ad_alert_state_open; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ad_alert_state_open ON public.ad_alert_state USING btree (ad_id, open_state_token) WHERE (open_state_token IS NOT NULL);


--
-- Name: ix_ad_alert_state_snoozed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ad_alert_state_snoozed ON public.ad_alert_state USING btree (ad_id) WHERE (snoozed_until IS NOT NULL);


--
-- Name: ix_ad_alert_state_state; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ad_alert_state_state ON public.ad_alert_state USING btree (alert_state);


--
-- Name: ix_ad_auto_disable_day; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ad_auto_disable_day ON public.ad_auto_enable_disabled USING btree (cabinet_day_started_at);


--
-- Name: ix_browser_operation_leases_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_browser_operation_leases_active ON public.browser_operation_leases USING btree (lease_expires_at);


--
-- Name: ix_browser_operation_leases_owner; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_browser_operation_leases_owner ON public.browser_operation_leases USING btree (owner);


--
-- Name: ix_browser_operation_capability_expiry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_browser_operation_capability_expiry ON public.browser_operation_capability_uses USING btree (expires_at);


--
-- Name: ix_browser_operation_capability_task; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_browser_operation_capability_task ON public.browser_operation_capability_uses USING btree (task_id, created_at);


--
-- Name: ix_browser_operation_capability_unconsumed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_browser_operation_capability_unconsumed ON public.browser_operation_capability_uses USING btree (expires_at) WHERE (consumed_at IS NULL);


--
-- Name: ix_cabinet_runtime_lease_expiry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cabinet_runtime_lease_expiry ON public.cabinet_runtime USING btree (lease_expires_at) WHERE (owner_instance IS NOT NULL);


--
-- Name: ix_cabinet_runtime_next_scan; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_cabinet_runtime_next_scan ON public.cabinet_runtime USING btree (next_scan_at);


--
-- Name: ix_campaign_creative_offer_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_campaign_creative_offer_code ON public.campaign_creative USING btree (offer_code);


--
-- Name: ix_campaign_run_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_campaign_run_created_at ON public.campaign_run USING btree (created_at);


--
-- Name: ix_campaign_run_preset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_campaign_run_preset ON public.campaign_run USING btree (preset_id);


--
-- Name: ix_campaign_run_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_campaign_run_status ON public.campaign_run USING btree (status);


--
-- Name: ix_command_idempotency_receipts_task_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_command_idempotency_receipts_task_id ON public.command_idempotency_receipts USING btree (task_id);


--
-- Name: ix_enable_recs_ad; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_enable_recs_ad ON public.enable_recommendations USING btree (ad_id);


--
-- Name: ix_enable_recs_batch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_enable_recs_batch ON public.enable_recommendations USING btree (live_batch_started_at);


--
-- Name: ix_enable_recs_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_enable_recs_created ON public.enable_recommendations USING btree (created_at);


--
-- Name: ix_enable_recs_level; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_enable_recs_level ON public.enable_recommendations USING btree (recommendation_level);


--
-- Name: ix_enable_recs_promoted; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_enable_recs_promoted ON public.enable_recommendations USING btree (promoted_to_task_id) WHERE (promoted_to_task_id IS NOT NULL);


--
-- Name: ix_fb_ads_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fb_ads_active ON public.fb_ads USING btree (id) WHERE (is_active = true);


--
-- Name: ix_fb_ads_adset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fb_ads_adset ON public.fb_ads USING btree (adset_id);


--
-- Name: ix_fb_ads_last_seen; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fb_ads_last_seen ON public.fb_ads USING btree (last_seen_at);


--
-- Name: ix_fb_adsets_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fb_adsets_active ON public.fb_adsets USING btree (id) WHERE (is_active = true);


--
-- Name: ix_fb_adsets_campaign; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fb_adsets_campaign ON public.fb_adsets USING btree (campaign_id);


--
-- Name: ix_fb_adsets_fb_id_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_fb_adsets_fb_id_unique ON public.fb_adsets USING btree (fb_adset_id) WHERE (fb_adset_id IS NOT NULL);


--
-- Name: ix_fb_campaigns_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fb_campaigns_active ON public.fb_campaigns USING btree (id) WHERE (is_active = true);


--
-- Name: ix_fb_campaigns_ad_account; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fb_campaigns_ad_account ON public.fb_campaigns USING btree (ad_account_id);


--
-- Name: ix_fb_campaigns_campaign_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fb_campaigns_campaign_name ON public.fb_campaigns USING btree (campaign_name);


--
-- Name: ix_fb_campaigns_fb_id_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_fb_campaigns_fb_id_unique ON public.fb_campaigns USING btree (fb_campaign_id) WHERE (fb_campaign_id IS NOT NULL);


--
-- Name: ix_fb_campaigns_offer; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_fb_campaigns_offer ON public.fb_campaigns USING btree (offer_id) WHERE (offer_id IS NOT NULL);


--
-- Name: ix_incidents_active_severity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_incidents_active_severity ON public.incidents USING btree (severity, opened_at) WHERE ((status)::text = ANY (ARRAY[('open'::character varying)::text, ('acknowledged'::character varying)::text, ('executing'::character varying)::text]));


--
-- Name: ix_incidents_correlation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_incidents_correlation ON public.incidents USING btree (correlation_id);


--
-- Name: ix_incidents_resource; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_incidents_resource ON public.incidents USING btree (resource_type, resource_id);


--
-- Name: ix_incidents_terminal_retention; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_incidents_terminal_retention ON public.incidents USING btree (resolved_at, updated_at) WHERE ((status)::text = ANY (ARRAY[('resolved'::character varying)::text, ('failed'::character varying)::text]));


--
-- Name: ix_invites_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invites_active ON public.telegram_invites USING btree (id) WHERE ((used_at IS NULL) AND (revoked_at IS NULL));


--
-- Name: ix_invites_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invites_expires ON public.telegram_invites USING btree (expires_at);


--
-- Name: ix_meta_audit_account; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_meta_audit_account ON ONLY public.meta_api_audit_log USING btree (ad_account_id, created_at) WHERE (ad_account_id IS NOT NULL);


--
-- Name: ix_meta_audit_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_meta_audit_created ON ONLY public.meta_api_audit_log USING btree (created_at);


--
-- Name: ix_meta_audit_errors; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_meta_audit_errors ON ONLY public.meta_api_audit_log USING btree (created_at) WHERE (http_status >= 400);


--
-- Name: ix_meta_audit_initiated; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_meta_audit_initiated ON ONLY public.meta_api_audit_log USING btree (initiated_by, created_at);


--
-- Name: ix_notification_delivery_claim; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_delivery_claim ON public.notification_deliveries USING btree (scheduled_at, id) WHERE ((state)::text = ANY (ARRAY[('pending'::character varying)::text, ('retry'::character varying)::text]));


--
-- Name: ix_notification_delivery_event; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_delivery_event ON public.notification_deliveries USING btree (event_id);


--
-- Name: ix_notification_delivery_expired_lease; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_delivery_expired_lease ON public.notification_deliveries USING btree (lease_expires_at) WHERE ((state)::text = 'leased'::text);


--
-- Name: ix_notification_delivery_recipient_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_delivery_recipient_active ON public.notification_deliveries USING btree (recipient_id, state, id) WHERE ((state)::text = ANY (ARRAY[('pending'::character varying)::text, ('retry'::character varying)::text, ('leased'::character varying)::text]));


--
-- Name: ix_notification_delivery_terminal_window; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_delivery_terminal_window ON public.notification_deliveries USING btree (completed_at) WHERE ((state)::text = ANY (ARRAY[('sent'::character varying)::text, ('dead'::character varying)::text, ('unknown'::character varying)::text, ('superseded'::character varying)::text]));


--
-- Name: ix_notification_events_correlation; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_events_correlation ON public.notification_events USING btree (correlation_id);


--
-- Name: ix_notification_events_incident; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_events_incident ON public.notification_events USING btree (incident_id, created_at);


--
-- Name: ix_notification_events_retention; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_notification_events_retention ON public.notification_events USING btree (created_at);


--
-- Name: ix_offers_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_offers_active ON public.offers USING btree (id) WHERE (is_active = true);


--
-- Name: ix_operator_revision_events_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_operator_revision_events_created_at ON public.operator_revision_events USING btree (created_at);


--
-- Name: ix_panel_login_tickets_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_panel_login_tickets_expires_at ON public.panel_login_tickets USING btree (expires_at);


--
-- Name: ix_panel_oidc_attempts_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_panel_oidc_attempts_expires_at ON public.panel_oidc_attempts USING btree (expires_at);


--
-- Name: ix_panel_sessions_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_panel_sessions_expires_at ON public.panel_sessions USING btree (expires_at);


--
-- Name: ix_recipients_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recipients_active ON public.telegram_recipients USING btree (chat_id) WHERE (revoked_at IS NULL);


--
-- Name: ix_recipients_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_recipients_role ON public.telegram_recipients USING btree (role);


--
-- Name: ix_scan_runs_scan_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_scan_runs_scan_id ON ONLY public.scan_runs USING btree (scan_id);


--
-- Name: ix_scan_runs_started; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_scan_runs_started ON ONLY public.scan_runs USING btree (started_at);


--
-- Name: ix_system_config_value_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_system_config_value_gin ON public.system_config USING gin (value);


--
-- Name: ix_task_queue_completed; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_queue_completed ON public.task_queue USING btree (completed_at) WHERE (completed_at IS NOT NULL);


--
-- Name: ix_task_queue_created_by_chat; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_queue_created_by_chat ON public.task_queue USING btree (created_by_chat_id, status) WHERE (created_by_chat_id IS NOT NULL);


--
-- Name: ix_task_queue_lease_expiry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_queue_lease_expiry ON public.task_queue USING btree (lease_expires_at) WHERE ((status)::text = 'running'::text);


--
-- Name: ix_task_queue_money_runnable; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_queue_money_runnable ON public.task_queue USING btree (priority DESC, available_at, created_at, id) WHERE (((lane)::text = 'money'::text) AND ((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('retrying'::character varying)::text])));


--
-- Name: ix_task_queue_payload; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_queue_payload ON public.task_queue USING gin (payload);


--
-- Name: ix_task_queue_requested_by; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_queue_requested_by ON public.task_queue USING btree (requested_by, created_at);


--
-- Name: ix_task_queue_runnable; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_queue_runnable ON public.task_queue USING btree (lane, priority DESC, available_at, created_at, id) WHERE ((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('retrying'::character varying)::text]));


--
-- Name: ix_task_queue_running; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_task_queue_running ON public.task_queue USING btree (updated_at) WHERE ((status)::text = 'running'::text);


--
-- Name: ix_telegram_action_active_digest; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_telegram_action_active_digest ON public.telegram_action_tokens USING btree (token_digest) WHERE ((consumed_at IS NULL) AND (revoked_at IS NULL));


--
-- Name: ix_telegram_action_delivery; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_telegram_action_delivery ON public.telegram_action_tokens USING btree (delivery_id, action_key);


--
-- Name: ix_telegram_action_expiry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_telegram_action_expiry ON public.telegram_action_tokens USING btree (expires_at);


--
-- Name: ix_telegram_command_reply_claim; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_telegram_command_reply_claim ON public.telegram_command_replies USING btree (scheduled_at, id) WHERE ((state)::text = ANY (ARRAY[('pending'::character varying)::text, ('retry'::character varying)::text]));


--
-- Name: ix_telegram_command_reply_expired_lease; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_telegram_command_reply_expired_lease ON public.telegram_command_replies USING btree (lease_expires_at) WHERE ((state)::text = 'leased'::text);


--
-- Name: ix_telegram_command_reply_retention; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_telegram_command_reply_retention ON public.telegram_command_replies USING btree (completed_at) WHERE ((state)::text = ANY (ARRAY[('sent'::character varying)::text, ('dead'::character varying)::text, ('unknown'::character varying)::text]));


--
-- Name: ix_telegram_message_slot_recipient; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_telegram_message_slot_recipient ON public.telegram_message_slots USING btree (recipient_id);


--
-- Name: ix_telegram_navigation_active_digest; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_telegram_navigation_active_digest ON public.telegram_navigation_tokens USING btree (token_digest) WHERE ((consumed_at IS NULL) AND (revoked_at IS NULL));


--
-- Name: ix_telegram_navigation_expiry; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_telegram_navigation_expiry ON public.telegram_navigation_tokens USING btree (expires_at);


--
-- Name: ix_telegram_update_inbox_claim; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_telegram_update_inbox_claim ON public.telegram_updates_inbox USING btree (scheduled_at, bot_generation, update_id) WHERE ((state)::text = ANY (ARRAY[('pending'::character varying)::text, ('retry'::character varying)::text]));


--
-- Name: ix_telegram_update_inbox_expired_lease; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_telegram_update_inbox_expired_lease ON public.telegram_updates_inbox USING btree (lease_expires_at) WHERE ((state)::text = 'leased'::text);


--
-- Name: ix_telegram_update_terminal_retention; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_telegram_update_terminal_retention ON public.telegram_updates_inbox USING btree (processed_at) WHERE ((state)::text = ANY (ARRAY[('processed'::character varying)::text, ('dead'::character varying)::text]));


--
-- Name: ix_tracker_click_state_ad; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tracker_click_state_ad ON public.tracker_click_state USING btree (ad_id, last_event_at);


--
-- Name: ix_tracker_click_state_last_event; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tracker_click_state_last_event ON public.tracker_click_state USING btree (last_event_at);


--
-- Name: ix_tracker_click_state_unmatched; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_tracker_click_state_unmatched ON public.tracker_click_state USING btree (last_event_at) WHERE (ad_id IS NULL);


--
-- Name: meta_api_audit_log_default_ad_account_id_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX meta_api_audit_log_default_ad_account_id_created_at_idx ON public.meta_api_audit_log_default USING btree (ad_account_id, created_at) WHERE (ad_account_id IS NOT NULL);


--
-- Name: meta_api_audit_log_default_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX meta_api_audit_log_default_created_at_idx ON public.meta_api_audit_log_default USING btree (created_at);


--
-- Name: meta_api_audit_log_default_created_at_idx1; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX meta_api_audit_log_default_created_at_idx1 ON public.meta_api_audit_log_default USING btree (created_at) WHERE (http_status >= 400);


--
-- Name: meta_api_audit_log_default_initiated_by_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX meta_api_audit_log_default_initiated_by_created_at_idx ON public.meta_api_audit_log_default USING btree (initiated_by, created_at);


--
-- Name: scan_runs_default_scan_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX scan_runs_default_scan_id_idx ON public.scan_runs_default USING btree (scan_id);


--
-- Name: scan_runs_default_started_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX scan_runs_default_started_at_idx ON public.scan_runs_default USING btree (started_at);


--
-- Name: uq_incidents_active_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_incidents_active_key ON public.incidents USING btree (incident_key) WHERE ((status)::text = ANY (ARRAY[('open'::character varying)::text, ('acknowledged'::character varying)::text, ('executing'::character varying)::text]));


--
-- Name: ad_metrics_default_ad_id_cycle_ts_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_ad_metrics_ad_cycle ATTACH PARTITION public.ad_metrics_default_ad_id_cycle_ts_idx;


--
-- Name: ad_metrics_default_ad_id_cycle_ts_key; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.uq_ad_metrics_ad_cycle ATTACH PARTITION public.ad_metrics_default_ad_id_cycle_ts_key;


--
-- Name: ad_metrics_default_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.pk_ad_metrics ATTACH PARTITION public.ad_metrics_default_pkey;


--
-- Name: ad_metrics_default_scan_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_ad_metrics_scan ATTACH PARTITION public.ad_metrics_default_scan_id_idx;


--
-- Name: adsetpro_postback_events_defa_attribution_status_next_retry_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_adsetpro_postback_processing ATTACH PARTITION public.adsetpro_postback_events_defa_attribution_status_next_retry_idx;


--
-- Name: adsetpro_postback_events_defa_click_id_event_type_received__key; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.uq_adsetpro_postback_dedup ATTACH PARTITION public.adsetpro_postback_events_defa_click_id_event_type_received__key;


--
-- Name: adsetpro_postback_events_default_click_id_event_type_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_adsetpro_postback_click ATTACH PARTITION public.adsetpro_postback_events_default_click_id_event_type_idx;


--
-- Name: adsetpro_postback_events_default_fb_ad_fk_received_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_adsetpro_postback_fb_ad ATTACH PARTITION public.adsetpro_postback_events_default_fb_ad_fk_received_at_idx;


--
-- Name: adsetpro_postback_events_default_fb_ad_id_received_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_adsetpro_postback_fb_ad_id ATTACH PARTITION public.adsetpro_postback_events_default_fb_ad_id_received_at_idx;


--
-- Name: adsetpro_postback_events_default_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.adsetpro_postback_events_pkey ATTACH PARTITION public.adsetpro_postback_events_default_pkey;


--
-- Name: adsetpro_postback_events_default_received_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_adsetpro_postback_received ATTACH PARTITION public.adsetpro_postback_events_default_received_at_idx;


--
-- Name: adsetpro_postback_events_default_source_click_id_event_type_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_adsetpro_postback_source_click ATTACH PARTITION public.adsetpro_postback_events_default_source_click_id_event_type_idx;


--
-- Name: adsetpro_postback_events_default_source_provider_event_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_adsetpro_postback_source_provider ATTACH PARTITION public.adsetpro_postback_events_default_source_provider_event_id_idx;


--
-- Name: alert_events_default_ad_id_created_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_alert_events_ad_created ATTACH PARTITION public.alert_events_default_ad_id_created_at_idx;


--
-- Name: alert_events_default_open_state_token_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_alert_events_token ATTACH PARTITION public.alert_events_default_open_state_token_idx;


--
-- Name: alert_events_default_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.pk_alert_events ATTACH PARTITION public.alert_events_default_pkey;


--
-- Name: alert_events_default_scan_id_created_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_alert_events_scan_id_created ATTACH PARTITION public.alert_events_default_scan_id_created_at_idx;


--
-- Name: alert_events_default_stage_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_alert_events_stage ATTACH PARTITION public.alert_events_default_stage_idx;


--
-- Name: alert_events_default_state_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_alert_events_state ATTACH PARTITION public.alert_events_default_state_idx;


--
-- Name: meta_api_audit_log_default_ad_account_id_created_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_meta_audit_account ATTACH PARTITION public.meta_api_audit_log_default_ad_account_id_created_at_idx;


--
-- Name: meta_api_audit_log_default_created_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_meta_audit_created ATTACH PARTITION public.meta_api_audit_log_default_created_at_idx;


--
-- Name: meta_api_audit_log_default_created_at_idx1; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_meta_audit_errors ATTACH PARTITION public.meta_api_audit_log_default_created_at_idx1;


--
-- Name: meta_api_audit_log_default_initiated_by_created_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_meta_audit_initiated ATTACH PARTITION public.meta_api_audit_log_default_initiated_by_created_at_idx;


--
-- Name: meta_api_audit_log_default_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.pk_meta_api_audit_log ATTACH PARTITION public.meta_api_audit_log_default_pkey;


--
-- Name: scan_runs_default_pkey; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.pk_scan_runs ATTACH PARTITION public.scan_runs_default_pkey;


--
-- Name: scan_runs_default_scan_id_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_scan_runs_scan_id ATTACH PARTITION public.scan_runs_default_scan_id_idx;


--
-- Name: scan_runs_default_scan_id_started_at_key; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.uq_scan_runs_scan_id ATTACH PARTITION public.scan_runs_default_scan_id_started_at_key;


--
-- Name: scan_runs_default_started_at_idx; Type: INDEX ATTACH; Schema: public; Owner: -
--

ALTER INDEX public.ix_scan_runs_started ATTACH PARTITION public.scan_runs_default_started_at_idx;


--
-- Name: adset_duplicate_previews trg_adset_duplicate_previews_consume_once; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_adset_duplicate_previews_consume_once BEFORE UPDATE ON public.adset_duplicate_previews FOR EACH ROW EXECUTE FUNCTION public.enforce_adset_duplicate_preview_consume_once();


--
-- Name: ad_metrics trg_ad_metrics_operator_revision; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_ad_metrics_operator_revision AFTER INSERT OR DELETE OR UPDATE ON public.ad_metrics FOR EACH STATEMENT EXECUTE FUNCTION public.notify_fb_operator_statement('metrics');


--
-- Name: cabinet_runtime trg_cabinet_runtime_operator_notify; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_cabinet_runtime_operator_notify AFTER INSERT OR DELETE OR UPDATE ON public.cabinet_runtime FOR EACH ROW EXECUTE FUNCTION public.notify_fb_operator_event('cabinet', 'ad_account_id');


--
-- Name: campaign_run trg_campaign_run_operator_notify; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_campaign_run_operator_notify AFTER INSERT OR DELETE OR UPDATE ON public.campaign_run FOR EACH ROW EXECUTE FUNCTION public.notify_fb_operator_event('campaign_run', 'id');


--
-- Name: fb_ads trg_fb_ads_operator_revision; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_fb_ads_operator_revision AFTER INSERT OR DELETE OR UPDATE ON public.fb_ads FOR EACH STATEMENT EXECUTE FUNCTION public.notify_fb_operator_statement('ads');


--
-- Name: incidents trg_incidents_operator_notify; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_incidents_operator_notify AFTER INSERT OR DELETE OR UPDATE ON public.incidents FOR EACH ROW EXECUTE FUNCTION public.notify_fb_operator_event('incident', 'id');


--
-- Name: notification_deliveries trg_notification_deliveries_operator_notify; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_notification_deliveries_operator_notify AFTER INSERT OR DELETE OR UPDATE ON public.notification_deliveries FOR EACH ROW EXECUTE FUNCTION public.notify_fb_operator_event('notification_delivery', 'id');


--
-- Name: observer_config trg_observer_config_operator_revision; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_observer_config_operator_revision AFTER INSERT OR DELETE OR UPDATE ON public.observer_config FOR EACH STATEMENT EXECUTE FUNCTION public.notify_fb_operator_statement('observer_config');


--
-- Name: scan_runs trg_scan_runs_operator_revision; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_scan_runs_operator_revision AFTER INSERT OR DELETE OR UPDATE ON public.scan_runs FOR EACH STATEMENT EXECUTE FUNCTION public.notify_fb_operator_statement('scan');


--
-- Name: system_config trg_system_config_browser_maintenance_readiness; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_system_config_browser_maintenance_readiness AFTER INSERT OR UPDATE OF value ON public.system_config FOR EACH ROW WHEN (((new.key)::text = 'browser_maintenance'::text)) EXECUTE FUNCTION public.invalidate_browser_readiness_on_maintenance();


--
-- Name: task_queue trg_task_queue_operator_notify; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_task_queue_operator_notify AFTER INSERT OR DELETE OR UPDATE ON public.task_queue FOR EACH ROW EXECUTE FUNCTION public.notify_fb_operator_event('task', 'id');


--
-- Name: tracker_click_state trg_tracker_click_state_operator_revision; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_tracker_click_state_operator_revision AFTER INSERT OR DELETE OR UPDATE ON public.tracker_click_state FOR EACH STATEMENT EXECUTE FUNCTION public.notify_fb_operator_statement('tracker');


--
-- Name: adset_duplicate_previews fk_adset_duplicate_previews_task_id_task_queue; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.adset_duplicate_previews
    ADD CONSTRAINT fk_adset_duplicate_previews_task_id_task_queue FOREIGN KEY (task_id) REFERENCES public.task_queue(id) ON DELETE CASCADE;


--
-- Name: adsetpro_postback_events adsetpro_postback_events_fb_ad_fk_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.adsetpro_postback_events
    ADD CONSTRAINT adsetpro_postback_events_fb_ad_fk_fkey FOREIGN KEY (fb_ad_fk) REFERENCES public.fb_ads(id) ON DELETE SET NULL;


--
-- Name: ad_alert_state fk_ad_alert_state_ad_id_fb_ads; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ad_alert_state
    ADD CONSTRAINT fk_ad_alert_state_ad_id_fb_ads FOREIGN KEY (ad_id) REFERENCES public.fb_ads(id) ON DELETE CASCADE;


--
-- Name: ad_auto_enable_disabled fk_ad_auto_enable_disabled_ad_id_fb_ads; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ad_auto_enable_disabled
    ADD CONSTRAINT fk_ad_auto_enable_disabled_ad_id_fb_ads FOREIGN KEY (ad_id) REFERENCES public.fb_ads(id) ON DELETE CASCADE;


--
-- Name: ad_metrics fk_ad_metrics_ad_id_fb_ads; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.ad_metrics
    ADD CONSTRAINT fk_ad_metrics_ad_id_fb_ads FOREIGN KEY (ad_id) REFERENCES public.fb_ads(id) ON DELETE CASCADE;


--
-- Name: alert_events fk_alert_events_ad_id_fb_ads; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.alert_events
    ADD CONSTRAINT fk_alert_events_ad_id_fb_ads FOREIGN KEY (ad_id) REFERENCES public.fb_ads(id) ON DELETE CASCADE;


--
-- Name: browser_operation_capability_uses fk_browser_operation_capability_uses_task_id_task_queue; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.browser_operation_capability_uses
    ADD CONSTRAINT fk_browser_operation_capability_uses_task_id_task_queue FOREIGN KEY (task_id) REFERENCES public.task_queue(id) ON DELETE CASCADE;


--
-- Name: browser_channel_readiness fk_browser_channel_readiness_vision_config_id_vision_config; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.browser_channel_readiness
    ADD CONSTRAINT fk_browser_channel_readiness_vision_config_id_vision_config FOREIGN KEY (vision_config_id) REFERENCES public.vision_config(id) ON DELETE CASCADE;


--
-- Name: campaign_run fk_campaign_run_preset_id_campaign_preset; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.campaign_run
    ADD CONSTRAINT fk_campaign_run_preset_id_campaign_preset FOREIGN KEY (preset_id) REFERENCES public.campaign_preset(id) ON DELETE SET NULL;


--
-- Name: command_idempotency_receipts fk_command_idempotency_receipts_task_id_task_queue; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.command_idempotency_receipts
    ADD CONSTRAINT fk_command_idempotency_receipts_task_id_task_queue FOREIGN KEY (task_id) REFERENCES public.task_queue(id) ON DELETE CASCADE;


--
-- Name: enable_recommendations fk_enable_recommendations_ad_id_fb_ads; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.enable_recommendations
    ADD CONSTRAINT fk_enable_recommendations_ad_id_fb_ads FOREIGN KEY (ad_id) REFERENCES public.fb_ads(id) ON DELETE CASCADE;


--
-- Name: enable_recommendations fk_enable_recommendations_promoted_to_task_id_task_queue; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.enable_recommendations
    ADD CONSTRAINT fk_enable_recommendations_promoted_to_task_id_task_queue FOREIGN KEY (promoted_to_task_id) REFERENCES public.task_queue(id) ON DELETE SET NULL;


--
-- Name: fb_ads fk_fb_ads_adset_id_fb_adsets; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fb_ads
    ADD CONSTRAINT fk_fb_ads_adset_id_fb_adsets FOREIGN KEY (adset_id) REFERENCES public.fb_adsets(id) ON DELETE CASCADE;


--
-- Name: fb_adsets fk_fb_adsets_campaign_id_fb_campaigns; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fb_adsets
    ADD CONSTRAINT fk_fb_adsets_campaign_id_fb_campaigns FOREIGN KEY (campaign_id) REFERENCES public.fb_campaigns(id) ON DELETE CASCADE;


--
-- Name: fb_campaigns fk_fb_campaigns_offer_id_offers; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fb_campaigns
    ADD CONSTRAINT fk_fb_campaigns_offer_id_offers FOREIGN KEY (offer_id) REFERENCES public.offers(id) ON DELETE SET NULL;


--
-- Name: notification_deliveries fk_notification_deliveries_event_id_notification_events; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_deliveries
    ADD CONSTRAINT fk_notification_deliveries_event_id_notification_events FOREIGN KEY (event_id) REFERENCES public.notification_events(id) ON DELETE CASCADE;


--
-- Name: notification_deliveries fk_notification_deliveries_recipient_id_telegram_recipients; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_deliveries
    ADD CONSTRAINT fk_notification_deliveries_recipient_id_telegram_recipients FOREIGN KEY (recipient_id) REFERENCES public.telegram_recipients(id) ON DELETE RESTRICT;


--
-- Name: notification_events fk_notification_events_incident_id_incidents; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.notification_events
    ADD CONSTRAINT fk_notification_events_incident_id_incidents FOREIGN KEY (incident_id) REFERENCES public.incidents(id) ON DELETE SET NULL;


--
-- Name: offer_rules fk_offer_rules_offer_id_offers; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.offer_rules
    ADD CONSTRAINT fk_offer_rules_offer_id_offers FOREIGN KEY (offer_id) REFERENCES public.offers(id) ON DELETE CASCADE;


--
-- Name: telegram_action_tokens fk_telegram_action_tokens_delivery_id_notification_deliveries; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_action_tokens
    ADD CONSTRAINT fk_telegram_action_tokens_delivery_id_notification_deliveries FOREIGN KEY (delivery_id) REFERENCES public.notification_deliveries(id) ON DELETE CASCADE;


--
-- Name: telegram_action_tokens fk_telegram_action_tokens_event_id_notification_events; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_action_tokens
    ADD CONSTRAINT fk_telegram_action_tokens_event_id_notification_events FOREIGN KEY (event_id) REFERENCES public.notification_events(id) ON DELETE CASCADE;


--
-- Name: telegram_action_tokens fk_telegram_action_tokens_incident_id_incidents; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_action_tokens
    ADD CONSTRAINT fk_telegram_action_tokens_incident_id_incidents FOREIGN KEY (incident_id) REFERENCES public.incidents(id) ON DELETE CASCADE;


--
-- Name: telegram_action_tokens fk_telegram_action_tokens_recipient_id_telegram_recipients; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_action_tokens
    ADD CONSTRAINT fk_telegram_action_tokens_recipient_id_telegram_recipients FOREIGN KEY (recipient_id) REFERENCES public.telegram_recipients(id) ON DELETE CASCADE;


--
-- Name: telegram_action_tokens fk_telegram_action_tokens_task_id_task_queue; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_action_tokens
    ADD CONSTRAINT fk_telegram_action_tokens_task_id_task_queue FOREIGN KEY (task_id) REFERENCES public.task_queue(id) ON DELETE SET NULL;


--
-- Name: telegram_command_replies fk_telegram_command_reply_update_generation; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_command_replies
    ADD CONSTRAINT fk_telegram_command_reply_update_generation FOREIGN KEY (bot_generation, update_id) REFERENCES public.telegram_updates_inbox(bot_generation, update_id) ON DELETE CASCADE;


--
-- Name: telegram_message_slots fk_telegram_message_slots_incident_id_incidents; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_message_slots
    ADD CONSTRAINT fk_telegram_message_slots_incident_id_incidents FOREIGN KEY (incident_id) REFERENCES public.incidents(id) ON DELETE CASCADE;


--
-- Name: telegram_message_slots fk_telegram_message_slots_last_event_id_notification_events; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_message_slots
    ADD CONSTRAINT fk_telegram_message_slots_last_event_id_notification_events FOREIGN KEY (last_event_id) REFERENCES public.notification_events(id) ON DELETE RESTRICT;


--
-- Name: telegram_message_slots fk_telegram_message_slots_recipient_id_telegram_recipients; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_message_slots
    ADD CONSTRAINT fk_telegram_message_slots_recipient_id_telegram_recipients FOREIGN KEY (recipient_id) REFERENCES public.telegram_recipients(id) ON DELETE CASCADE;


--
-- Name: telegram_navigation_tokens fk_telegram_navigation_tokens_delivery_id_notification__0d77; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_navigation_tokens
    ADD CONSTRAINT fk_telegram_navigation_tokens_delivery_id_notification__0d77 FOREIGN KEY (delivery_id) REFERENCES public.notification_deliveries(id) ON DELETE CASCADE;


--
-- Name: telegram_navigation_tokens fk_telegram_navigation_tokens_event_id_notification_events; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_navigation_tokens
    ADD CONSTRAINT fk_telegram_navigation_tokens_event_id_notification_events FOREIGN KEY (event_id) REFERENCES public.notification_events(id) ON DELETE CASCADE;


--
-- Name: telegram_navigation_tokens fk_telegram_navigation_tokens_recipient_id_telegram_recipients; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_navigation_tokens
    ADD CONSTRAINT fk_telegram_navigation_tokens_recipient_id_telegram_recipients FOREIGN KEY (recipient_id) REFERENCES public.telegram_recipients(id) ON DELETE CASCADE;


--
-- Name: telegram_recipient_preferences fk_telegram_recipient_preferences_recipient_id_telegram_e7ee; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_recipient_preferences
    ADD CONSTRAINT fk_telegram_recipient_preferences_recipient_id_telegram_e7ee FOREIGN KEY (recipient_id) REFERENCES public.telegram_recipients(id) ON DELETE CASCADE;


--
-- Name: telegram_recipients fk_telegram_recipients_invite_id_telegram_invites; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_recipients
    ADD CONSTRAINT fk_telegram_recipients_invite_id_telegram_invites FOREIGN KEY (invite_id) REFERENCES public.telegram_invites(id) ON DELETE SET NULL;


--
-- Name: tracker_click_state fk_tracker_click_state_ad_id_fb_ads; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.tracker_click_state
    ADD CONSTRAINT fk_tracker_click_state_ad_id_fb_ads FOREIGN KEY (ad_id) REFERENCES public.fb_ads(id) ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--
