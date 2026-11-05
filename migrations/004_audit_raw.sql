-- Журналы и «сырой» слой.

CREATE TABLE audit.event_log (
    event_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_at   timestamptz NOT NULL DEFAULT now(),
    event_type varchar(40) NOT NULL,
    severity   varchar(4)  NOT NULL DEFAULT 'INFO' CHECK (severity IN ('INFO', 'WARN', 'CRIT')),
    actor_role varchar(50) NOT NULL,
    run_id     uuid,
    message    text        NOT NULL,
    payload    jsonb       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX ix_event_at ON audit.event_log (event_at DESC);

-- Журнал только дополняется.
CREATE FUNCTION audit.forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Журнал событий только дополняется' USING ERRCODE = 'check_violation';
END $$;
CREATE TRIGGER trg_event_log_append_only BEFORE UPDATE OR DELETE ON audit.event_log
FOR EACH ROW EXECUTE FUNCTION audit.forbid_change();

CREATE TABLE audit.dq_issue (
    issue_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    checked_at   timestamptz NOT NULL DEFAULT now(),
    batch_id     uuid        NOT NULL,
    source_table varchar(50) NOT NULL,
    source_key   text        NOT NULL,
    check_code   varchar(30) NOT NULL,
    severity     varchar(4)  NOT NULL CHECK (severity IN ('CRIT', 'WARN')),
    store_id     smallint,
    product_id   integer,
    details      jsonb       NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX ix_dq_check ON audit.dq_issue (check_code);

-- Пакеты загрузки.
CREATE TABLE audit.load_batch (
    batch_id    uuid PRIMARY KEY,
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    seed        integer,
    rows_raw    jsonb NOT NULL,
    rows_core   jsonb NOT NULL,
    issues      integer NOT NULL
);

-- Сырой слой: типы мягкие, ограничений нет — сюда попадают и ошибочные строки.
CREATE TABLE raw.sale_line (
    batch_id        uuid NOT NULL,
    sale_line_id    bigint,
    order_id        bigint,
    sold_at         timestamptz,
    sale_date       date,
    store_id        integer,
    product_id      integer,
    quantity        numeric,
    unit_price      numeric,
    discount_amount numeric,
    operation_type  text,
    is_cancelled    boolean,
    promo_id        integer
);

CREATE TABLE raw.product_cost (
    batch_id     uuid NOT NULL,
    product_id   integer,
    valid_from   date,
    unit_cost    numeric,
    source       text,
    calc_version integer
);

CREATE TABLE raw.store_price (
    batch_id   uuid NOT NULL,
    store_id   integer,
    product_id integer,
    valid_from date,
    price      numeric
);
