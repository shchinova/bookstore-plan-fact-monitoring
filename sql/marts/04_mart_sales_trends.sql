-- =============================================================================
-- 04_mart_sales_trends.sql
-- Витрина агрегированных продаж для дашборда Tableau
--
-- Грануляция: год x месяц x жанр x формат x язык
-- Содержит: выручку, количество, маржу, возвраты
--
-- Используется: Tableau Public (страницы «Обзор», «Ассортимент»)
--
-- ИЗМЕНЕНИЯ:
--   [FIX-1] p.language обёрнут в COALESCE(p.language, 'N/A'): у подписок
--           поле language = NULL (генератор не заполняет его для формата
--           Subscription). NULL в GROUP BY корректно группируется, но в
--           Tableau создаёт строку без подписи. Явное значение 'N/A' делает
--           срез читаемым и позволяет фильтровать подписки на дашборде.
-- =============================================================================

DROP TABLE IF EXISTS mart_sales_trends;

CREATE TABLE mart_sales_trends AS

SELECT
    d.year,
    d.month,
    d.quarter,
    -- Первый день месяца — удобен для оси времени в Tableau
    DATE_TRUNC('month', s.date)::DATE               AS month_start,
    p.genre,
    p.format,
    -- [FIX-1] NULL у подписок -> 'N/A'
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
            / SUM(s.sales_amount - s.return_amount), 4
        )
        ELSE 0
    END                                             AS gross_margin,
    -- Промо
    SUM(s.is_promo)                                 AS promo_orders,
    SUM(CASE WHEN s.is_promo = 1
             THEN s.sales_amount - s.return_amount
             ELSE 0 END)                            AS promo_revenue,
    -- Потерянные продажи (дефицит физических книг)
    SUM(s.lost_sales_qty)                           AS lost_sales_qty,
    -- Средний чек
    CASE
        WHEN COUNT(DISTINCT s.order_id) > 0
        THEN ROUND(
            SUM(s.sales_amount - s.return_amount)
            / COUNT(DISTINCT s.order_id), 2)
        ELSE 0
    END                                             AS avg_order_value

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