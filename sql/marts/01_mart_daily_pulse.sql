-- =============================================================================
-- 01_mart_daily_pulse.sql
-- Витрина для оперативного отчёта daily_pulse.xlsx
--
-- Содержит два слоя по каждому KPI:
--   today     = последняя дата в fact_sales (MAX(date))
--   yesterday = последняя дата − 1 день
--
-- Строк в результате: 2 (по одной на каждый day_type)
-- Используется: Excel → Power Query → daily_pulse.xlsx
-- =============================================================================

DROP TABLE IF EXISTS mart_daily_pulse;

CREATE TABLE mart_daily_pulse AS

WITH

-- Определяем опорные даты
dates AS (
    SELECT
        MAX(date)            AS today,
        MAX(date) - INTERVAL '1 day' AS yesterday
    FROM fact_sales
),

-- Продажи за today и yesterday с разбивкой по жанру и формату
daily_sales AS (
    SELECT
        s.date,
        CASE WHEN s.date = d.today THEN 'today' ELSE 'yesterday' END AS day_type,
        p.genre,
        p.format,
        COUNT(DISTINCT s.order_id)                          AS orders,
        SUM(s.sales_qty - s.return_qty)                     AS net_qty,
        SUM(s.sales_amount - s.return_amount)               AS net_revenue,
        SUM(s.sales_amount - s.return_amount -
            (s.sales_qty - s.return_qty) * p.cost_rub)      AS gross_profit
    FROM fact_sales s
    JOIN dim_product p  ON p.product_id = s.product_id
    CROSS JOIN dates d
    WHERE s.date IN (d.today, d.yesterday::DATE)
    GROUP BY s.date, day_type, p.genre, p.format
),

-- Топ-5 товаров за today по выручке
top5 AS (
    SELECT
        s.product_id,
        p.title,
        p.format,
        SUM(s.sales_amount - s.return_amount) AS revenue,
        RANK() OVER (ORDER BY SUM(s.sales_amount - s.return_amount) DESC) AS rnk
    FROM fact_sales s
    JOIN dim_product p  ON p.product_id = s.product_id
    CROSS JOIN dates d
    WHERE s.date = d.today
    GROUP BY s.product_id, p.title, p.format
),

-- Агрегаты по KPI для каждого day_type
kpi AS (
    SELECT
        day_type,
        SUM(net_revenue)                                AS total_revenue,
        SUM(orders)                                     AS total_orders,
        CASE WHEN SUM(orders) > 0
             THEN ROUND(SUM(net_revenue) / SUM(orders), 2)
             ELSE 0
        END                                             AS avg_order_value,
        SUM(gross_profit)                               AS total_gross_profit,
        CASE WHEN SUM(net_revenue) > 0
             THEN ROUND(SUM(gross_profit) / SUM(net_revenue), 4)
             ELSE 0
        END                                             AS gross_margin
    FROM daily_sales
    GROUP BY day_type
)

SELECT
    k.day_type,
    (SELECT today     FROM dates)   AS report_date,
    (SELECT yesterday FROM dates)   AS prev_date,
    k.total_revenue,
    k.total_orders,
    k.avg_order_value,
    k.total_gross_profit,
    k.gross_margin
FROM kpi k;

-- Индекс для быстрого чтения из Power Query
CREATE INDEX IF NOT EXISTS idx_mart_daily_pulse_day_type
    ON mart_daily_pulse(day_type);

-- -----------------------------------------------------------------------------
-- Отдельная таблица: разбивка за today по жанру и формату
-- (используется для детальной таблицы план/факт в daily_pulse.xlsx)
-- -----------------------------------------------------------------------------
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
FROM daily_sales;

CREATE INDEX IF NOT EXISTS idx_mart_daily_breakdown_type
    ON mart_daily_breakdown(day_type);

-- -----------------------------------------------------------------------------
-- Отдельная таблица: топ-5 товаров за today
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS mart_daily_top5;

CREATE TABLE mart_daily_top5 AS
SELECT product_id, title, format, revenue, rnk
FROM top5
WHERE rnk <= 5;