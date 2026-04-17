-- =============================================================================
-- schema.sql
-- Создание таблиц базы данных bookstore_ods
-- Запускается один раз при первоначальном развёртывании.
-- Используется в load_history.py
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Справочник дат
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_date (
    date            DATE        PRIMARY KEY,
    year            SMALLINT    NOT NULL,
    month           SMALLINT    NOT NULL CHECK (month BETWEEN 1 AND 12),
    quarter         SMALLINT    NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    is_weekend      SMALLINT    NOT NULL CHECK (is_weekend IN (0, 1)),
    is_holiday_ru   SMALLINT    NOT NULL CHECK (is_holiday_ru IN (0, 1))
);

-- -----------------------------------------------------------------------------
-- Справочник продуктов
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dim_product (
    product_id      INTEGER     PRIMARY KEY,
    isbn            VARCHAR(20),
    title           TEXT        NOT NULL,
    author          VARCHAR(255),
    publisher       VARCHAR(255),
    published_date  DATE,
    language        VARCHAR(50),
    page_count      SMALLINT,
    genre           VARCHAR(50) NOT NULL,
    format          VARCHAR(20) NOT NULL,
    price_rub       NUMERIC(10, 2) NOT NULL CHECK (price_rub > 0),
    cost_rub        NUMERIC(10, 2) NOT NULL CHECK (cost_rub > 0),
    avg_rating      NUMERIC(3, 1)  CHECK (avg_rating BETWEEN 0 AND 5),
    review_count    INTEGER        CHECK (review_count >= 0),
    stock_initial   INTEGER,
    is_physical     SMALLINT    NOT NULL CHECK (is_physical IN (0, 1))
);

-- -----------------------------------------------------------------------------
-- Планы (genre × format × year × month)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_plan (
    plan_id             INTEGER     PRIMARY KEY,
    genre               VARCHAR(50) NOT NULL,
    format              VARCHAR(20) NOT NULL,
    year                SMALLINT    NOT NULL,
    month               SMALLINT    NOT NULL CHECK (month BETWEEN 1 AND 12),
    plan_qty            INTEGER     NOT NULL CHECK (plan_qty >= 0),
    plan_amount         NUMERIC(14, 2) NOT NULL CHECK (plan_amount >= 0),
    plan_margin_target  NUMERIC(6, 4)  CHECK (plan_margin_target BETWEEN 0 AND 1),
    UNIQUE (genre, format, year, month)
);

-- -----------------------------------------------------------------------------
-- Продажи
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_sales (
    sales_id            INTEGER     PRIMARY KEY,
    order_id            INTEGER,
    product_id          INTEGER     NOT NULL REFERENCES dim_product(product_id),
    date                DATE        NOT NULL REFERENCES dim_date(date),
    sales_qty           INTEGER     NOT NULL CHECK (sales_qty > 0),
    return_qty          INTEGER     NOT NULL DEFAULT 0 CHECK (return_qty >= 0),
    unit_price          NUMERIC(10, 2) NOT NULL CHECK (unit_price > 0),
    sales_amount        NUMERIC(14, 2) NOT NULL,
    return_amount       NUMERIC(14, 2) NOT NULL DEFAULT 0,
    discount_percent    SMALLINT    NOT NULL DEFAULT 0
                            CHECK (discount_percent BETWEEN 0 AND 100),
    is_promo            SMALLINT    NOT NULL DEFAULT 0 CHECK (is_promo IN (0, 1)),
    promo_code          VARCHAR(50),
    channel             VARCHAR(20) NOT NULL,
    lost_sales_qty      INTEGER     NOT NULL DEFAULT 0 CHECK (lost_sales_qty >= 0)
);

CREATE INDEX IF NOT EXISTS idx_fact_sales_date       ON fact_sales(date);
CREATE INDEX IF NOT EXISTS idx_fact_sales_product_id ON fact_sales(product_id);
CREATE INDEX IF NOT EXISTS idx_fact_sales_order_id   ON fact_sales(order_id);

-- -----------------------------------------------------------------------------
-- Остатки
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fact_inventory (
    inventory_id        INTEGER     PRIMARY KEY,
    product_id          INTEGER     NOT NULL REFERENCES dim_product(product_id),
    date                DATE        NOT NULL REFERENCES dim_date(date),
    opening_stock       INTEGER     NOT NULL CHECK (opening_stock >= 0),
    sold_qty            INTEGER     NOT NULL CHECK (sold_qty >= 0),
    replenishment_qty   INTEGER     NOT NULL DEFAULT 0 CHECK (replenishment_qty >= 0),
    closing_stock       INTEGER     NOT NULL CHECK (closing_stock >= 0),
    is_low_stock        SMALLINT    NOT NULL CHECK (is_low_stock IN (0, 1)),
    UNIQUE (product_id, date)
);

CREATE INDEX IF NOT EXISTS idx_fact_inventory_date       ON fact_inventory(date);
CREATE INDEX IF NOT EXISTS idx_fact_inventory_product_id ON fact_inventory(product_id);