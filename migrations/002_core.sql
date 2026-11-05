-- Основной слой: только проверенные данные, все ограничения целостности.

CREATE TABLE core.store (
    store_id   smallint PRIMARY KEY,
    code       varchar(8)   NOT NULL UNIQUE,
    name       varchar(100) NOT NULL,
    region     varchar(100) NOT NULL,
    city       varchar(100) NOT NULL,
    is_active  boolean      NOT NULL DEFAULT true,
    opened_on  date         NOT NULL
);

CREATE TABLE core.category (
    category_id smallint PRIMARY KEY,
    name        varchar(100) NOT NULL UNIQUE,
    parent_id   smallint REFERENCES core.category (category_id)
);

CREATE TABLE core.product (
    product_id  integer PRIMARY KEY,
    sku         varchar(16)  NOT NULL UNIQUE,
    name        varchar(150) NOT NULL,
    category_id smallint     NOT NULL REFERENCES core.category (category_id),
    sale_unit   varchar(16)  NOT NULL CHECK (sale_unit IN ('шт', 'порция', 'набор', 'бутылка')),
    is_fresh    boolean      NOT NULL,           -- срок годности 1 день (роллы, суши, наборы)
    is_active   boolean      NOT NULL DEFAULT true
);
CREATE INDEX ix_product_category ON core.product (category_id);

-- Версии себестоимости единицы. Себестоимость единая по сети (допущение A-01).
CREATE TABLE core.product_cost (
    cost_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id   integer       NOT NULL REFERENCES core.product (product_id),
    valid_from   date          NOT NULL,
    unit_cost    numeric(12,4) NOT NULL CHECK (unit_cost > 0),
    source       varchar(50)   NOT NULL,
    calc_version integer       NOT NULL CHECK (calc_version > 0),
    UNIQUE (product_id, valid_from)
);

-- Цены в точках: действуют с valid_from до следующей записи.
CREATE TABLE core.store_price (
    price_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    store_id   smallint      NOT NULL REFERENCES core.store (store_id),
    product_id integer       NOT NULL REFERENCES core.product (product_id),
    valid_from date          NOT NULL,
    price      numeric(12,2) NOT NULL CHECK (price > 0),
    UNIQUE (store_id, product_id, valid_from)
);

CREATE TABLE core.promo (
    promo_id     integer PRIMARY KEY,
    name         varchar(150) NOT NULL,
    date_from    date         NOT NULL,
    date_to      date         NOT NULL,
    discount_pct numeric(5,4) NOT NULL CHECK (discount_pct > 0 AND discount_pct < 1),
    CHECK (date_to >= date_from)
);

-- store_id IS NULL — акция действует во всей сети.
CREATE TABLE core.promo_item (
    promo_item_id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    promo_id      integer  NOT NULL REFERENCES core.promo (promo_id),
    product_id    integer  NOT NULL REFERENCES core.product (product_id),
    store_id      smallint REFERENCES core.store (store_id),
    UNIQUE NULLS NOT DISTINCT (promo_id, product_id, store_id)
);

-- Календарь работы точек (санитарные дни и т. п.).
CREATE TABLE core.store_calendar (
    store_id      smallint NOT NULL REFERENCES core.store (store_id),
    calendar_date date     NOT NULL,
    is_open       boolean  NOT NULL,
    note          varchar(100),
    PRIMARY KEY (store_id, calendar_date)
);

-- Строки чеков. Знак операции зашит в вычисляемое поле net_revenue.
CREATE TABLE core.sale_line (
    sale_line_id    bigint PRIMARY KEY,
    order_id        bigint        NOT NULL,
    sold_at         timestamptz   NOT NULL,
    sale_date       date          NOT NULL,       -- дата по местному времени точки (Asia/Omsk)
    store_id        smallint      NOT NULL REFERENCES core.store (store_id),
    product_id      integer       NOT NULL REFERENCES core.product (product_id),
    quantity        numeric(10,3) NOT NULL CHECK (quantity > 0),
    unit_price      numeric(12,2) NOT NULL CHECK (unit_price >= 0),
    discount_amount numeric(12,2) NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    operation_type  varchar(10)   NOT NULL CHECK (operation_type IN ('sale', 'return')),
    is_cancelled    boolean       NOT NULL DEFAULT false,
    promo_id        integer REFERENCES core.promo (promo_id),
    source_row_hash char(64)      NOT NULL UNIQUE,  -- защита от повторной загрузки той же строки
    net_quantity    numeric(10,3) GENERATED ALWAYS AS (
        CASE WHEN is_cancelled THEN 0
             WHEN operation_type = 'return' THEN -quantity
             ELSE quantity END) STORED,
    net_revenue     numeric(12,2) GENERATED ALWAYS AS (
        CASE WHEN is_cancelled THEN 0
             WHEN operation_type = 'return' THEN -(quantity * unit_price - discount_amount)
             ELSE quantity * unit_price - discount_amount END) STORED,
    CHECK (discount_amount <= quantity * unit_price)
);
CREATE INDEX ix_sale_store_date   ON core.sale_line (store_id, sale_date);
CREATE INDEX ix_sale_product_date ON core.sale_line (product_id, sale_date);
CREATE INDEX ix_sale_order        ON core.sale_line (order_id);

-- Ежедневные остатки (единицы). closing = opening + produced − sold − written_off.
CREATE TABLE core.stock_daily (
    store_id        smallint      NOT NULL REFERENCES core.store (store_id),
    product_id      integer       NOT NULL REFERENCES core.product (product_id),
    stock_date      date          NOT NULL,
    opening_qty     numeric(10,3) NOT NULL CHECK (opening_qty >= 0),
    produced_qty    numeric(10,3) NOT NULL CHECK (produced_qty >= 0),
    sold_qty        numeric(10,3) NOT NULL CHECK (sold_qty >= 0),
    written_off_qty numeric(10,3) NOT NULL CHECK (written_off_qty >= 0),
    closing_qty     numeric(10,3) NOT NULL CHECK (closing_qty >= 0),
    is_available    boolean       NOT NULL,      -- товар был в продаже в этот день
    PRIMARY KEY (store_id, product_id, stock_date),
    CHECK (closing_qty = opening_qty + produced_qty - sold_qty - written_off_qty)
);

CREATE TABLE core.write_off (
    write_off_id   bigint PRIMARY KEY,
    store_id       smallint      NOT NULL REFERENCES core.store (store_id),
    product_id     integer       NOT NULL REFERENCES core.product (product_id),
    write_off_date date          NOT NULL,
    quantity       numeric(10,3) NOT NULL CHECK (quantity > 0),
    reason         varchar(20)   NOT NULL CHECK (reason IN ('expired', 'damaged', 'return', 'other'))
);
CREATE INDEX ix_write_off_store_date   ON core.write_off (store_id, write_off_date);
CREATE INDEX ix_write_off_product_date ON core.write_off (product_id, write_off_date);

-- Интервалы действия версий себестоимости и цен (valid_to включительно).
CREATE VIEW core.v_product_cost_range AS
SELECT product_id,
       valid_from,
       COALESCE(LEAD(valid_from) OVER w - 1, DATE 'infinity') AS valid_to,
       unit_cost,
       calc_version
FROM core.product_cost
WINDOW w AS (PARTITION BY product_id ORDER BY valid_from);

CREATE VIEW core.v_store_price_range AS
SELECT store_id,
       product_id,
       valid_from,
       COALESCE(LEAD(valid_from) OVER w - 1, DATE 'infinity') AS valid_to,
       price
FROM core.store_price
WINDOW w AS (PARTITION BY store_id, product_id ORDER BY valid_from);

-- Факт продаж с себестоимостью на дату продажи (правило BR-10).
-- unit_cost IS NULL — версии себестоимости на дату нет; строка исключается из валовой прибыли.
CREATE VIEW core.v_sale_fact AS
SELECT s.sale_line_id,
       s.order_id,
       s.sale_date,
       s.store_id,
       s.product_id,
       p.category_id,
       s.operation_type,
       s.is_cancelled,
       s.promo_id,
       s.discount_amount,
       s.net_quantity,
       s.net_revenue,
       c.unit_cost,
       s.net_quantity * c.unit_cost AS net_cogs
FROM core.sale_line s
JOIN core.product p ON p.product_id = s.product_id
LEFT JOIN core.v_product_cost_range c
       ON c.product_id = s.product_id
      AND s.sale_date BETWEEN c.valid_from AND c.valid_to;
