-- =============================================================================
-- 04_mart_sales_trends.sql
-- Агрегированные продажи для дашборда Tableau
--
-- Создаёт 1 таблицу: mart_sales_trends
-- Грануляция: год × месяц × жанр × формат × язык
-- Используется: Tableau (страницы «Обзор», «Ассортимент», «История»)
-- =============================================================================

DROP TABLE IF EXISTS mart_sales_trends;
CREATE TABLE mart_sales_trends AS

SELECT
    d.year,
    d.month,
    d.quarter,
    DATE_TRUNC('month', s.date)::DATE               AS month_start,
    p.genre,
    p.format,
    COALESCE(p.language, 'N/A')                     AS language,

    -- Объём
    COUNT(DISTINCT s.order_id)                      AS orders,
    SUM(s.sales_qty)                                AS gross_qty,
    SUM(s.return_qty)                               AS return_qty,
    SUM(s.sales_qty  - s.return_qty)                AS net_qty,

    -- Выручка
    SUM(s.sales_amount)                             AS gross_revenue,
    SUM(s.return_amount)                            AS return_revenue,
    SUM(s.sales_amount - s.return_amount)           AS net_revenue,

    -- Себестоимость и маржа
    SUM((s.sales_qty - s.return_qty) * p.cost_rub)  AS total_cost,
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

    -- Средний чек
    CASE
        WHEN COUNT(DISTINCT s.order_id) > 0
        THEN ROUND(
            SUM(s.sales_amount - s.return_amount)
            / COUNT(DISTINCT s.order_id), 2)
        ELSE 0
    END                                             AS avg_order_value,

    -- Промо
    SUM(s.is_promo)                                 AS promo_orders,
    SUM(CASE WHEN s.is_promo = 1
             THEN s.sales_amount - s.return_amount
             ELSE 0 END)                            AS promo_revenue,

    -- Потерянные продажи из-за дефицита (только физические форматы)
    SUM(s.lost_sales_qty)                           AS lost_sales_qty

FROM fact_sales s
JOIN dim_product p ON p.product_id = s.product_id
JOIN dim_date    d ON d.date        = s.date
GROUP BY
    d.year, d.month, d.quarter,
    DATE_TRUNC('month', s.date),
    p.genre, p.format,
    COALESCE(p.language, 'N/A');

CREATE INDEX IF NOT EXISTS idx_mart_sales_trends_month
    ON mart_sales_trends(year, month);
CREATE INDEX IF NOT EXISTS idx_mart_sales_trends_genre_format
    ON mart_sales_trends(genre, format);
CREATE INDEX IF NOT EXISTS idx_mart_sales_trends_month_start
    ON mart_sales_trends(month_start);