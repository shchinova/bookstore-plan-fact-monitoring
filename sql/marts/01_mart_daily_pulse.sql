-- =============================================================================
-- 01_mart_daily_pulse.sql
-- Витрина для оперативного отчёта daily_pulse.xlsx
--
-- Содержит два слоя по каждому KPI:
--   today     = последняя дата в fact_sales (MAX(date))
--   yesterday = последняя дата - 1 день
--
-- Строк в результате: 2 (по одной на каждый day_type)
-- Используется: Excel -> Power Query -> daily_pulse.xlsx
--
-- ИЗМЕНЕНИЯ:
--   [FIX-1] mart_daily_breakdown и mart_daily_top5 больше не ссылаются на
--           CTE из чужого запроса. Промежуточные данные материализованы во
--           временные таблицы, доступные в рамках всего скрипта.
--   [FIX-2] yesterday приводится к DATE явно уже в CTE dates, каст больше
--           не нужен в WHERE.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Шаг 0. Опорные даты (один раз на весь скрипт)
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS _tmp_dates;
CREATE TEMP TABLE _tmp_dates AS
SELECT
    MAX(date)                          AS today,
    (MAX(date) - INTERVAL '1 day')::DATE AS yesterday
FROM fact_sales;

-- -----------------------------------------------------------------------------
-- Шаг 1. Продажи за today и yesterday с разбивкой по жанру и формату
-- Материализуем в TEMP таблицу — она нужна и mart_daily_pulse, и mart_daily_breakdown
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS _tmp_daily_sales;
CREATE TEMP TABLE _tmp_daily_sales AS
SELECT
    s.date,
    CASE WHEN s.date = d.today THEN 'today' ELSE 'yesterday' END AS day_type,
    p.genre,
    p.format,
    COUNT(DISTINCT s.order_id)                         AS orders,
    SUM(s.sales_qty  - s.return_qty)                   AS net_qty,
    SUM(s.sales_amount - s.return_amount)              AS net_revenue,
    SUM(s.sales_amount - s.return_amount
        - (s.sales_qty - s.return_qty) * p.cost_rub)   AS gross_profit
FROM fact_sales s
JOIN dim_product p ON p.product_id = s.product_id
CROSS JOIN _tmp_dates d
WHERE s.date IN (d.today, d.yesterday)
GROUP BY s.date, day_type, p.genre, p.format;

-- -----------------------------------------------------------------------------
-- Шаг 2. Топ-5 товаров за today по выручке
-- Материализуем отдельно — нужна только mart_daily_top5
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS _tmp_top5;
CREATE TEMP TABLE _tmp_top5 AS
SELECT
    s.product_id,
    p.title,
    p.format,
    SUM(s.sales_amount - s.return_amount) AS revenue,
    RANK() OVER (ORDER BY SUM(s.sales_amount - s.return_amount) DESC) AS rnk
FROM fact_sales s
JOIN dim_product p ON p.product_id = s.product_id
CROSS JOIN _tmp_dates d
WHERE s.date = d.today
GROUP BY s.product_id, p.title, p.format;

-- -----------------------------------------------------------------------------
-- Шаг 3. Итоговые витрины
-- -----------------------------------------------------------------------------

-- KPI-сводка (2 строки: today / yesterday)
DROP TABLE IF EXISTS mart_daily_pulse;
CREATE TABLE mart_daily_pulse AS
SELECT
    ds.day_type,
    d.today                                             AS report_date,
    d.yesterday                                         AS prev_date,
    SUM(ds.net_revenue)                                 AS total_revenue,
    SUM(ds.orders)                                      AS total_orders,
    CASE
        WHEN SUM(ds.orders) > 0
        THEN ROUND(SUM(ds.net_revenue) / SUM(ds.orders), 2)
        ELSE 0
    END                                                 AS avg_order_value,
    SUM(ds.gross_profit)                                AS total_gross_profit,
    CASE
        WHEN SUM(ds.net_revenue) > 0
        THEN ROUND(SUM(ds.gross_profit) / SUM(ds.net_revenue), 4)
        ELSE 0
    END                                                 AS gross_margin
FROM _tmp_daily_sales ds
CROSS JOIN _tmp_dates d
GROUP BY ds.day_type, d.today, d.yesterday;

CREATE INDEX IF NOT EXISTS idx_mart_daily_pulse_day_type
    ON mart_daily_pulse(day_type);

-- Детальная разбивка за today/yesterday по жанру и формату
DROP TABLE IF EXISTS mart_daily_breakdown;
CREATE TABLE mart_daily_breakdown AS
SELECT
    day_type,
    genre,
    format,
    orders,
    net_qty,
    net_revenue,
    gross_profit
FROM _tmp_daily_sales;

CREATE INDEX IF NOT EXISTS idx_mart_daily_breakdown_type
    ON mart_daily_breakdown(day_type);

-- Топ-5 товаров за today
DROP TABLE IF EXISTS mart_daily_top5;
CREATE TABLE mart_daily_top5 AS
SELECT product_id, title, format, revenue, rnk
FROM _tmp_top5
WHERE rnk <= 5;

-- -----------------------------------------------------------------------------
-- Шаг 4. Очистка временных таблиц
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS _tmp_dates;
DROP TABLE IF EXISTS _tmp_daily_sales;
DROP TABLE IF EXISTS _tmp_top5;