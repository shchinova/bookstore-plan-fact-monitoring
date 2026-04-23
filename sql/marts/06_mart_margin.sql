-- =============================================================================
-- 06_mart_margin.sql
-- Маржинальность продуктов для дашборда Tableau
--
-- Создаёт 3 таблицы:
--   mart_margin             — сводный агрегат по форматам за весь период
--   mart_margin_by_format   — маржа по формату × месяц (для трендов)
--   mart_margin_by_publisher — рейтинг издателей по суммарной марже
-- =============================================================================

-- =============================================================================
-- ТАБЛИЦА 1: mart_margin
-- Сводный агрегат за весь период — одна строка на формат.
-- Даёт быстрый ответ «какой формат самый маржинальный» без фильтрации.
-- =============================================================================
DROP TABLE IF EXISTS mart_margin;
CREATE TABLE mart_margin AS

SELECT
    p.format,
    COUNT(DISTINCT p.product_id)                            AS unique_products,
    SUM(s.sales_qty    - s.return_qty)                      AS net_qty,
    SUM(s.sales_amount - s.return_amount)                   AS net_revenue,
    SUM((s.sales_qty   - s.return_qty) * p.cost_rub)        AS total_cost,
    SUM(s.sales_amount - s.return_amount
        - (s.sales_qty - s.return_qty) * p.cost_rub)        AS gross_profit,
    CASE
        WHEN SUM(s.sales_amount - s.return_amount) > 0
        THEN ROUND(
            SUM(s.sales_amount - s.return_amount
                - (s.sales_qty - s.return_qty) * p.cost_rub)
            / SUM(s.sales_amount - s.return_amount), 4)
        ELSE 0
    END                                                     AS gross_margin,
    ROUND(AVG(p.avg_rating)::NUMERIC, 2)                    AS avg_rating
FROM fact_sales s
JOIN dim_product p ON p.product_id = s.product_id
GROUP BY p.format
ORDER BY gross_profit DESC;

CREATE INDEX IF NOT EXISTS idx_mart_margin_format
    ON mart_margin(format);

-- =============================================================================
-- ТАБЛИЦА 2: mart_margin_by_format
-- Маржа по формату × месяц — для трендовых графиков в Tableau.
-- =============================================================================
DROP TABLE IF EXISTS mart_margin_by_format;
CREATE TABLE mart_margin_by_format AS

SELECT
    d.year,
    d.month,
    d.quarter,
    DATE_TRUNC('month', s.date)::DATE               AS month_start,
    p.format,
    SUM(s.sales_qty    - s.return_qty)              AS net_qty,
    SUM(s.sales_amount - s.return_amount)           AS net_revenue,
    SUM((s.sales_qty   - s.return_qty) * p.cost_rub) AS total_cost,
    SUM(s.sales_amount - s.return_amount
        - (s.sales_qty - s.return_qty) * p.cost_rub) AS gross_profit,
    CASE
        WHEN SUM(s.sales_amount - s.return_amount) > 0
        THEN ROUND(
            SUM(s.sales_amount - s.return_amount
                - (s.sales_qty - s.return_qty) * p.cost_rub)
            / SUM(s.sales_amount - s.return_amount), 4)
        ELSE 0
    END                                             AS gross_margin,
    COUNT(DISTINCT p.product_id)                    AS unique_products
FROM fact_sales s
JOIN dim_product p ON p.product_id = s.product_id
JOIN dim_date    d ON d.date        = s.date
GROUP BY d.year, d.month, d.quarter, DATE_TRUNC('month', s.date), p.format;

CREATE INDEX IF NOT EXISTS idx_mart_margin_format_month
    ON mart_margin_by_format(year, month, format);

-- =============================================================================
-- ТАБЛИЦА 3: mart_margin_by_publisher
-- Рейтинг издателей по суммарной марже за весь период.
-- publisher IS NOT NULL уже исключает подписки (у них publisher = NULL).
-- =============================================================================
DROP TABLE IF EXISTS mart_margin_by_publisher;
CREATE TABLE mart_margin_by_publisher AS

WITH publisher_stats AS (
    SELECT
        p.publisher,
        p.format,
        COUNT(DISTINCT p.product_id)                            AS product_count,
        SUM(s.sales_qty    - s.return_qty)                      AS net_qty,
        SUM(s.sales_amount - s.return_amount)                   AS net_revenue,
        SUM((s.sales_qty   - s.return_qty) * p.cost_rub)        AS total_cost,
        SUM(s.sales_amount - s.return_amount
            - (s.sales_qty - s.return_qty) * p.cost_rub)        AS gross_profit,
        AVG(p.avg_rating)                                        AS avg_product_rating
    FROM fact_sales s
    JOIN dim_product p ON p.product_id = s.product_id
    WHERE p.publisher IS NOT NULL
    GROUP BY p.publisher, p.format
)

SELECT
    publisher,
    format,
    product_count,
    net_qty,
    net_revenue,
    total_cost,
    gross_profit,
    CASE
        WHEN net_revenue > 0
        THEN ROUND(gross_profit / net_revenue, 4)
        ELSE 0
    END                                         AS gross_margin,
    ROUND(avg_product_rating::NUMERIC, 2)       AS avg_product_rating,
    RANK() OVER (
        PARTITION BY format
        ORDER BY gross_profit DESC
    )                                           AS rank_by_format,
    RANK() OVER (
        ORDER BY gross_profit DESC
    )                                           AS rank_overall
FROM publisher_stats
ORDER BY gross_profit DESC;

CREATE INDEX IF NOT EXISTS idx_mart_margin_publisher
    ON mart_margin_by_publisher(publisher);