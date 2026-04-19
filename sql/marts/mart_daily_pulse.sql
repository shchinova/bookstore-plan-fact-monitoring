-- =============================================================================
-- 01_mart_daily_pulse.sql
-- Витрина для оперативного отчёта daily_pulse.xlsx
--
-- Строк в результате: 2 (today / yesterday)
--
-- Структура колонок:
--   Служебные    : day_type, report_date, prev_date
--   Дневные KPI  : total_revenue, total_orders, avg_order_value,
--                  total_gross_profit, gross_margin
--                  — показатели за один конкретный день (report_date)
--   MTD-метрики  : mtd_revenue, mtd_orders, mtd_net_qty,
--                  mtd_gross_profit, mtd_gross_margin
--                  — накопительно с 1-го числа до today включительно
--                  — ОДИНАКОВЫ для обеих строк: MTD всегда считается
--                    относительно today, а не относительно day_type.
--                    В Excel читайте MTD только из строки day_type='today'.
--   prev-MTD     : prev_mtd_revenue, prev_mtd_orders, prev_mtd_net_qty,
--                  prev_mtd_gross_profit, prev_mtd_gross_margin
--                  — тот же период прошлого месяца (1-е до того же дня)
--                  — тоже одинаковы для обеих строк (по той же причине)
--                    В Excel читайте из строки day_type='today'.
--
-- Дельты (абсолютные и %) намеренно убраны из витрины —
-- считаются формулами прямо в Excel.
--
-- Используется: Excel -> Power Query -> daily_pulse.xlsx
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Шаг 0. Опорные даты
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS _tmp_dates;
CREATE TEMP TABLE _tmp_dates AS
SELECT
    MAX(date)                            AS today,
    (MAX(date) - INTERVAL '1 day')::DATE AS yesterday
FROM fact_sales;

-- -----------------------------------------------------------------------------
-- Шаг 1. Продажи за today и yesterday (дневные KPI и разбивка)
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS _tmp_daily_sales;
CREATE TEMP TABLE _tmp_daily_sales AS
SELECT
    s.date,
    CASE WHEN s.date = d.today THEN 'today' ELSE 'yesterday' END AS day_type,
    p.genre,
    p.format,
    COUNT(DISTINCT s.order_id)                                    AS orders,
    SUM(s.sales_qty  - s.return_qty)                              AS net_qty,
    SUM(s.sales_amount - s.return_amount)                         AS net_revenue,
    SUM(s.sales_amount - s.return_amount
        - (s.sales_qty - s.return_qty) * p.cost_rub)              AS gross_profit
FROM fact_sales s
JOIN dim_product p ON p.product_id = s.product_id
CROSS JOIN _tmp_dates d
WHERE s.date IN (d.today, d.yesterday)
GROUP BY s.date, day_type, p.genre, p.format;

-- -----------------------------------------------------------------------------
-- Шаг 2. Топ-5 товаров за today
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
-- Шаг 3. MTD текущего месяца (с 1-го числа до today включительно)
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS _tmp_mtd_current;
CREATE TEMP TABLE _tmp_mtd_current AS
SELECT
    COUNT(DISTINCT s.order_id)                              AS mtd_orders,
    SUM(s.sales_qty  - s.return_qty)                        AS mtd_net_qty,
    SUM(s.sales_amount - s.return_amount)                   AS mtd_revenue,
    SUM(s.sales_amount - s.return_amount
        - (s.sales_qty - s.return_qty) * p.cost_rub)        AS mtd_gross_profit
FROM fact_sales s
JOIN dim_product p ON p.product_id = s.product_id
CROSS JOIN _tmp_dates d
WHERE s.date >= DATE_TRUNC('month', d.today)::DATE
  AND s.date <= d.today;

-- -----------------------------------------------------------------------------
-- Шаг 4. MTD прошлого месяца (тот же период: 1-е до того же дня месяца)
-- Если today = 15.04, берём 01.03–15.03 — корректное сравнение для
-- незакрытого месяца (полный март дал бы заведомо большие цифры).
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS _tmp_mtd_prev;
CREATE TEMP TABLE _tmp_mtd_prev AS
SELECT
    COUNT(DISTINCT s.order_id)                              AS prev_mtd_orders,
    SUM(s.sales_qty  - s.return_qty)                        AS prev_mtd_net_qty,
    SUM(s.sales_amount - s.return_amount)                   AS prev_mtd_revenue,
    SUM(s.sales_amount - s.return_amount
        - (s.sales_qty - s.return_qty) * p.cost_rub)        AS prev_mtd_gross_profit
FROM fact_sales s
JOIN dim_product p ON p.product_id = s.product_id
CROSS JOIN _tmp_dates d
WHERE s.date >= (DATE_TRUNC('month', d.today) - INTERVAL '1 month')::DATE
  AND s.date <= (d.today - INTERVAL '1 month')::DATE;

-- -----------------------------------------------------------------------------
-- Шаг 5. Итоговая витрина mart_daily_pulse
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS mart_daily_pulse;
CREATE TABLE mart_daily_pulse AS
SELECT
    ds.day_type,

    -- Служебные даты
    CASE WHEN ds.day_type = 'today'
         THEN d.today
         ELSE d.yesterday
    END::DATE                                               AS report_date,
    CASE WHEN ds.day_type = 'today'
         THEN d.yesterday
         ELSE (d.yesterday - INTERVAL '1 day')::DATE
    END::DATE                                               AS prev_date,

    -- Дневные KPI (за один день = report_date)
    SUM(ds.net_revenue)                                     AS total_revenue,
    SUM(ds.orders)                                          AS total_orders,
    CASE
        WHEN SUM(ds.orders) > 0
        THEN ROUND(SUM(ds.net_revenue) / SUM(ds.orders), 2)
        ELSE 0
    END                                                     AS avg_order_value,
    SUM(ds.gross_profit)                                    AS total_gross_profit,
    CASE
        WHEN SUM(ds.net_revenue) > 0
        THEN ROUND(SUM(ds.gross_profit) / SUM(ds.net_revenue), 4)
        ELSE 0
    END                                                     AS gross_margin,

    -- MTD текущего месяца (одинаково для обеих строк — см. комментарий выше)
    m.mtd_revenue,
    m.mtd_orders,
    m.mtd_net_qty,
    m.mtd_gross_profit,
    CASE
        WHEN m.mtd_revenue > 0
        THEN ROUND(m.mtd_gross_profit / m.mtd_revenue, 4)
        ELSE 0
    END                                                     AS mtd_gross_margin,

    -- MTD прошлого месяца (тот же период, одинаково для обеих строк)
    pm.prev_mtd_revenue,
    pm.prev_mtd_orders,
    pm.prev_mtd_net_qty,
    pm.prev_mtd_gross_profit,
    CASE
        WHEN pm.prev_mtd_revenue > 0
        THEN ROUND(pm.prev_mtd_gross_profit / pm.prev_mtd_revenue, 4)
        ELSE 0
    END                                                     AS prev_mtd_gross_margin

FROM _tmp_daily_sales ds
CROSS JOIN _tmp_dates d
CROSS JOIN _tmp_mtd_current m
CROSS JOIN _tmp_mtd_prev pm
GROUP BY
    ds.day_type, d.today, d.yesterday,
    m.mtd_revenue, m.mtd_orders, m.mtd_net_qty, m.mtd_gross_profit,
    pm.prev_mtd_revenue, pm.prev_mtd_orders,
    pm.prev_mtd_net_qty, pm.prev_mtd_gross_profit;

CREATE INDEX IF NOT EXISTS idx_mart_daily_pulse_day_type
    ON mart_daily_pulse(day_type);

-- -----------------------------------------------------------------------------
-- Шаг 6. Детальная разбивка за today/yesterday по жанру и формату
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS mart_daily_breakdown;
CREATE TABLE mart_daily_breakdown AS
SELECT day_type, genre, format, orders, net_qty, net_revenue, gross_profit
FROM _tmp_daily_sales;

CREATE INDEX IF NOT EXISTS idx_mart_daily_breakdown_type
    ON mart_daily_breakdown(day_type);

-- -----------------------------------------------------------------------------
-- Шаг 7. Топ-5 товаров за today
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS mart_daily_top5;
CREATE TABLE mart_daily_top5 AS
SELECT product_id, title, format, revenue, rnk
FROM _tmp_top5
WHERE rnk <= 5;

-- -----------------------------------------------------------------------------
-- Шаг 8. Очистка временных таблиц
-- -----------------------------------------------------------------------------
DROP TABLE IF EXISTS _tmp_dates;
DROP TABLE IF EXISTS _tmp_daily_sales;
DROP TABLE IF EXISTS _tmp_top5;
DROP TABLE IF EXISTS _tmp_mtd_current;
DROP TABLE IF EXISTS _tmp_mtd_prev;