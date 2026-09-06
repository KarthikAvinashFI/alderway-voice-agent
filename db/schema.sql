-- Alderway Insurance Services outbound intake agent, world schema.
--
-- The data flows the other way round from a servicing agent: almost nothing is read out to the
-- caller, and almost everything is written down. So the guarantee this schema exists to support is
-- that a dropped call leaves behind exactly what was collected, in the caller's own words, with the
-- revisions intact. Nothing is held only in the model's context.
--
-- PRIMARY KEY POLICY: every table uses a SINGLE-COLUMN surrogate key. Natural compound identities
-- are expressed with UNIQUE constraints instead. A two-column PRIMARY KEY breaks the harness world
-- seeder, which emits each key column as its own inline PRIMARY KEY and is then rejected by Postgres
-- with "multiple primary keys for table X are not allowed".

DROP TABLE IF EXISTS audit_log, callback_requests, transfers, eligibility_decisions,
    consent_events, answer_revisions, answers, intake_sessions, call_attempts,
    do_not_call, leads, campaigns, lender_partners CASCADE;

CREATE TABLE lender_partners (
    partner_id    TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    contact_phone TEXT,
    -- Named to the caller when they ask where their details came from, so it has to be a real
    -- relationship rather than a label.
    referral_label TEXT NOT NULL DEFAULT 'your mortgage lender'
);

CREATE TABLE campaigns (
    campaign_id           TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    questionnaire         TEXT NOT NULL DEFAULT 'home_quote_intake',
    -- The version that was asked, which is not always the version deployed today.
    questionnaire_version TEXT NOT NULL DEFAULT '',
    -- Pinned per campaign so an age based rule gives the same answer on a replay as it did live.
    reference_year        INT  NOT NULL DEFAULT 2026,
    active                BOOLEAN NOT NULL DEFAULT TRUE,
    max_attempts          INT  NOT NULL DEFAULT 4,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE leads (
    lead_id      TEXT PRIMARY KEY,
    campaign_id  TEXT NOT NULL REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    partner_id   TEXT REFERENCES lender_partners(partner_id) ON DELETE SET NULL,
    full_name    TEXT NOT NULL,
    phone        TEXT UNIQUE NOT NULL,          -- E.164, matched when dialling and when suppressing
    -- What the lender sent. Unverified, and confirmed with the caller rather than read back as fact.
    property_address_hint TEXT NOT NULL DEFAULT '',
    property_state        TEXT NOT NULL DEFAULT '',
    partner_reference     TEXT NOT NULL DEFAULT '',
    -- The calling window is the property's local time, never ours.
    time_zone    TEXT NOT NULL DEFAULT 'America/New_York',
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','retry','completed','transferred','suppressed','exhausted')),
    attempts     INT  NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE do_not_call (
    dnc_id      TEXT PRIMARY KEY,
    phone       TEXT UNIQUE NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'caller_request'
                CHECK (source IN ('caller_request','wrong_number','partner_request','regulatory')),
    -- Kept because "in what words did they ask" is the question that actually gets asked later.
    verbatim    TEXT NOT NULL DEFAULT '',
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE intake_sessions (
    session_id            TEXT PRIMARY KEY,
    lead_id               TEXT NOT NULL REFERENCES leads(lead_id) ON DELETE CASCADE,
    questionnaire         TEXT NOT NULL,
    questionnaire_version TEXT NOT NULL,
    state                 TEXT NOT NULL DEFAULT 'in_progress'
                          CHECK (state IN ('in_progress','complete','ended_fatal_refusal','ended_by_caller')),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at          TIMESTAMPTZ,
    -- How many times each field has been served as the next question. Without it a caller who keeps
    -- answering something else is asked the same thing for the rest of the call.
    ask_attempts          JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE call_attempts (
    call_id          TEXT PRIMARY KEY,
    lead_id          TEXT NOT NULL REFERENCES leads(lead_id) ON DELETE CASCADE,
    session_id       TEXT REFERENCES intake_sessions(session_id) ON DELETE SET NULL,
    room_name        TEXT NOT NULL DEFAULT '',
    attempt_number   INT  NOT NULL DEFAULT 1,
    status           TEXT NOT NULL DEFAULT 'in_progress'
                     CHECK (status IN ('in_progress','completed','failed')),
    -- What a campaign report is actually built from.
    disposition      TEXT NOT NULL DEFAULT ''
                     CHECK (disposition IN ('','completed','dropped','no_answer','voicemail','removed',
                                            'wrong_number','transferred','callback','declined',
                                            'declined_consent','disqualified')),
    ended_reason     TEXT NOT NULL DEFAULT '',
    duration_seconds INT  NOT NULL DEFAULT 0,
    recording_url    TEXT NOT NULL DEFAULT '',
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at         TIMESTAMPTZ
);

CREATE TABLE answers (
    answer_id   TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES intake_sessions(session_id) ON DELETE CASCADE,
    field_id    TEXT NOT NULL,
    -- The parsed value, as text plus the type needed to read it back. A repeating group addresses
    -- its items as field_id#1, field_id#2, which is why this is not a fixed column per question.
    value_text  TEXT,
    value_type  TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'answered'
                CHECK (status IN ('answered','refused','unknown','not_applicable')),
    -- What they actually said. The parsed value is an interpretation; this is the evidence.
    verbatim    TEXT NOT NULL DEFAULT '',
    -- low where they hedged, so a licensed agent knows not to quote off it.
    confidence  TEXT NOT NULL DEFAULT 'high' CHECK (confidence IN ('high','medium','low')),
    sequence    INT  NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, field_id)
);

-- A correction is a new row here, not an overwrite there. An underwriter asked six months later needs
-- what was first said as much as what it became.
CREATE TABLE answer_revisions (
    revision_id TEXT PRIMARY KEY,
    answer_id   TEXT NOT NULL REFERENCES answers(answer_id) ON DELETE CASCADE,
    value_text  TEXT,
    value_type  TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'answered',
    verbatim    TEXT NOT NULL DEFAULT '',
    confidence  TEXT NOT NULL DEFAULT 'high',
    sequence    INT  NOT NULL DEFAULT 0,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- An event log rather than a flag on the lead, because the question is always "did they agree, when,
-- and in what words".
CREATE TABLE consent_events (
    consent_id  TEXT PRIMARY KEY,
    lead_id     TEXT NOT NULL REFERENCES leads(lead_id) ON DELETE CASCADE,
    call_id     TEXT,
    kind        TEXT NOT NULL CHECK (kind IN ('recording','continue','email','transfer')),
    granted     BOOLEAN NOT NULL,
    verbatim    TEXT NOT NULL DEFAULT '',
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE eligibility_decisions (
    decision_id          TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL REFERENCES intake_sessions(session_id) ON DELETE CASCADE,
    decision             TEXT NOT NULL CHECK (decision IN ('eligible','needs_review','disqualified')),
    hard_codes           JSONB NOT NULL DEFAULT '[]',
    soft_codes           JSONB NOT NULL DEFAULT '[]',
    indeterminate_codes  JSONB NOT NULL DEFAULT '[]',
    notes                JSONB NOT NULL DEFAULT '[]',
    -- The one sentence the agent is permitted to say. Stored so the transcript can be checked against it.
    spoken_reason        TEXT NOT NULL DEFAULT '',
    -- A decision made under one rule set must stay explainable after the rules change.
    rules_reference_year INT  NOT NULL DEFAULT 2026,
    decided_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE transfers (
    transfer_id       TEXT PRIMARY KEY,
    call_id           TEXT NOT NULL REFERENCES call_attempts(call_id) ON DELETE CASCADE,
    to_number         TEXT NOT NULL DEFAULT '',
    reason            TEXT NOT NULL DEFAULT '',
    answers_collected INT  NOT NULL DEFAULT 0,
    succeeded         BOOLEAN NOT NULL DEFAULT FALSE,
    requested_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE callback_requests (
    callback_id  TEXT PRIMARY KEY,
    lead_id      TEXT NOT NULL REFERENCES leads(lead_id) ON DELETE CASCADE,
    call_id      TEXT,
    window_label TEXT NOT NULL DEFAULT 'no_preference',
    notes        TEXT NOT NULL DEFAULT '',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
    audit_id    TEXT PRIMARY KEY,
    entity      TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    action      TEXT NOT NULL,
    detail      JSONB NOT NULL DEFAULT '{}',
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX answers_session_idx      ON answers (session_id);
CREATE INDEX call_attempts_lead_idx   ON call_attempts (lead_id);
CREATE INDEX intake_sessions_lead_idx ON intake_sessions (lead_id, state);
CREATE INDEX audit_entity_idx         ON audit_log (entity, entity_id);
