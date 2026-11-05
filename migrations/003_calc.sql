-- Правила ценообразования, запуски расчёта, рекомендации, предупреждения.

CREATE TABLE calc.rule_set (
    rule_set_id    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    version        integer      NOT NULL UNIQUE CHECK (version > 0),
    effective_from date         NOT NULL,
    is_locked      boolean      NOT NULL DEFAULT false,   -- true после первого использования (BR-09)
    created_at     timestamptz  NOT NULL DEFAULT now(),
    created_by     varchar(50)  NOT NULL,
    comment        text
);

CREATE TABLE calc.pricing_rule (
    rule_id        integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule_set_id    integer      NOT NULL REFERENCES calc.rule_set (rule_set_id),
    scope_type     varchar(10)  NOT NULL CHECK (scope_type IN ('global', 'store', 'category', 'product')),
    scope_id       integer,
    target_margin  numeric(6,4) NOT NULL CHECK (target_margin >= 0 AND target_margin < 1),
    min_margin     numeric(6,4) NOT NULL CHECK (min_margin >= 0),
    max_change     numeric(6,4) NOT NULL CHECK (max_change > 0 AND max_change <= 1),
    rounding_step  numeric(10,2) NOT NULL CHECK (rounding_step > 0),
    rounding_mode  varchar(10)  NOT NULL CHECK (rounding_mode IN ('up', 'nearest', 'down')),
    allow_decrease boolean      NOT NULL DEFAULT false,
    CHECK (min_margin <= target_margin),
    CHECK ((scope_type = 'global') = (scope_id IS NULL)),
    UNIQUE NULLS NOT DISTINCT (rule_set_id, scope_type, scope_id)
);

-- Использованный набор правил менять нельзя: изменение = новая версия.
CREATE FUNCTION calc.forbid_locked_rule_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- В INSERT нет OLD, в DELETE нет NEW: проверяем оба набора, если они есть.
    IF EXISTS (SELECT 1 FROM calc.rule_set
               WHERE is_locked
                 AND rule_set_id IN (
                     CASE WHEN TG_OP <> 'DELETE' THEN NEW.rule_set_id END,
                     CASE WHEN TG_OP <> 'INSERT' THEN OLD.rule_set_id END)) THEN
        RAISE EXCEPTION 'Набор правил уже использован в расчёте и не может изменяться. Создайте новую версию.'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN COALESCE(NEW, OLD);
END $$;

CREATE TRIGGER trg_pricing_rule_locked
BEFORE INSERT OR UPDATE OR DELETE ON calc.pricing_rule
FOR EACH ROW EXECUTE FUNCTION calc.forbid_locked_rule_change();

CREATE TABLE calc.pricing_run (
    run_id          uuid PRIMARY KEY,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    as_of_date      date        NOT NULL,
    rule_set_id     integer     NOT NULL REFERENCES calc.rule_set (rule_set_id),
    idempotency_key char(64)    NOT NULL,
    status          varchar(10) NOT NULL CHECK (status IN ('running', 'done', 'failed')),
    triggered_by    varchar(50) NOT NULL,
    is_forced       boolean     NOT NULL DEFAULT false,
    offers_total    integer,
    error_text      text
);
-- Завершённый запуск с тем же ключом может быть только один, кроме принудительных.
CREATE UNIQUE INDEX ux_run_idempotency ON calc.pricing_run (idempotency_key)
    WHERE status = 'done' AND NOT is_forced;
CREATE INDEX ix_run_started ON calc.pricing_run (started_at DESC);

CREATE TABLE calc.price_recommendation (
    recommendation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id            uuid          NOT NULL REFERENCES calc.pricing_run (run_id),
    store_id          smallint      NOT NULL REFERENCES core.store (store_id),
    product_id        integer       NOT NULL REFERENCES core.product (product_id),
    rule_id           integer REFERENCES calc.pricing_rule (rule_id),
    unit_cost         numeric(12,4),
    current_price     numeric(12,2),
    base_price        numeric(12,4),
    recommended_price numeric(12,2),
    change_abs        numeric(12,2),
    change_pct        numeric(8,4),
    status            varchar(12)   NOT NULL CHECK (status IN (
        'PROPOSED', 'NO_CHANGE', 'CONFLICT', 'BLOCKED', 'ON_HOLD', 'APPROVED', 'REJECTED', 'SUPERSEDED')),
    reason_code       varchar(40)   NOT NULL,
    reason_text       text          NOT NULL,
    details           jsonb         NOT NULL DEFAULT '{}'::jsonb,
    decided_by        varchar(50),
    decided_at        timestamptz,
    decision_comment  text,
    UNIQUE (run_id, store_id, product_id),
    CHECK (status NOT IN ('PROPOSED', 'APPROVED') OR recommended_price IS NOT NULL)
);
CREATE INDEX ix_rec_status ON calc.price_recommendation (status);
CREATE INDEX ix_rec_offer  ON calc.price_recommendation (store_id, product_id);

-- Предупреждения CRIT/WARN. INFO-события живут в audit.event_log (ADR-08).
CREATE TABLE calc.alert (
    alert_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    alert_code    varchar(30)  NOT NULL,
    severity      varchar(4)   NOT NULL CHECK (severity IN ('CRIT', 'WARN')),
    store_id      smallint REFERENCES core.store (store_id),
    product_id    integer REFERENCES core.product (product_id),
    run_id        uuid REFERENCES calc.pricing_run (run_id),
    description   text         NOT NULL,
    dedup_key     char(64)     NOT NULL,
    first_seen_at timestamptz  NOT NULL DEFAULT now(),
    last_seen_at  timestamptz  NOT NULL DEFAULT now(),
    occurrences   integer      NOT NULL DEFAULT 1 CHECK (occurrences > 0),
    status        varchar(12)  NOT NULL DEFAULT 'NEW'
                  CHECK (status IN ('NEW', 'IN_PROGRESS', 'RESOLVED', 'DISMISSED')),
    status_changed_by varchar(50),
    status_changed_at timestamptz
);
-- Открытое предупреждение с тем же ключом существует в одном экземпляре (BR-13).
CREATE UNIQUE INDEX ux_alert_open ON calc.alert (dedup_key) WHERE status IN ('NEW', 'IN_PROGRESS');
CREATE INDEX ix_alert_severity ON calc.alert (severity, status, first_seen_at DESC);
