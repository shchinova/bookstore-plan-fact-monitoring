-- =============================================================================
-- 07_mart_subscriptions.sql
-- Метрики подписок для дашборда Tableau
--
-- Создаёт 3 таблицы:
--   mart_subscriptions          — сводка за весь период (1 строка на тип)
--   mart_subscriptions_monthly  — динамика по месяцам
--
-- Подписки намеренно исключены из mart_abc и mart_plan_fact —
-- все их метрики сосредоточены здесь.
-- =============================================================================

-- =============================================================================
-- ТАБЛИЦА 1: mart_subscriptions
-- Сводные метрики за весь период — одна строка на тип подписки.
-- Аналог mart_subscriptions_summary с более согласованным именем.
-- =============================================================================
DROP TABLE IF EXISTS mart_subscriptions;
CREATE TABLE mart_subscriptions AS

WITH sub_sales AS (
    SELECT
        s.order_id,
        s.date,
        p.product_id,
        p.title         AS subscription_type,
        p.price_rub,
        p.cost_rub,
        s.sales_qty,
        s.return_qty,
        s.sales_amount,
        s.return_amount,
        s.is_promo
    FROM fact_sales s
    JOIN dim_product p ON p.product_id = s.product_id
    WHERE p.format = 'Subscription'
),

by_type AS (
    SELECT
        subscription_type,
        price_rub,
        cost_rub,
        COUNT(DISTINCT order_id)            AS total_orders,
        SUM(sales_qty    - return_qty)      AS net_subscriptions,
        SUM(sales_amount - return_amount)   AS total_revenue,
        -- Валовая прибыль: выручка минус себестоимость
        SUM(sales_amount - return_amount
            - (sales_qty - return_qty) * cost_rub) AS gross_profit,
        SUM(return_qty)                     AS total_cancellations,
        SUM(is_promo)                       AS promo_count,
        MIN(date)                           AS first_sale_date,
        MAX(date)                           AS last_sale_date
    FROM sub_sales
    GROUP BY subscription_type, price_rub, cost_rub
)

SELECT
    subscription_type,
    total_orders,
    net_subscriptions,
    total_revenue,
    gross_profit,
    CASE
        WHEN total_revenue > 0
        THEN ROUND(gross_profit / total_revenue, 4)
        ELSE 0
    END                                     AS gross_margin,
    total_cancellations,
    CASE
        WHEN total_orders > 0
        THEN ROUND(total_cancellations::NUMERIC / total_orders * 100, 1)
        ELSE 0
    END                                     AS cancellation_rate_pct,
    promo_count,
    CASE
        WHEN total_orders > 0
        THEN ROUND(promo_count::NUMERIC / total_orders * 100, 1)
        ELSE 0
    END                                     AS promo_rate_pct,
    first_sale_date,
    last_sale_date,
    price_rub                               AS unit_price,
    cost_rub                                AS unit_cost,
    ROUND((price_rub - cost_rub) / NULLIF(price_rub, 0), 4) AS unit_margin,
    -- LTV: средняя выручка на одну подписку
    CASE
        WHEN net_subscriptions > 0
        THEN ROUND(total_revenue / net_subscriptions, 2)
        ELSE 0
    END                                     AS avg_ltv
FROM by_type
ORDER BY total_revenue DESC;

-- =============================================================================
-- ТАБЛИЦА 2: mart_subscriptions_monthly
-- Динамика подписок по месяцам — для трендов и Tableau.
-- ИЗМЕНЕНИЕ: добавлены gross_profit и gross_margin (рекомендация из анализа).
-- =============================================================================
DROP TABLE IF EXISTS mart_subscriptions_monthly;
CREATE TABLE mart_subscriptions_monthly AS

SELECT
    d.year,
    d.month,
    d.quarter,
    DATE_TRUNC('month', s.date)::DATE           AS month_start,
    p.title                                     AS subscription_type,

    COUNT(DISTINCT s.order_id)                  AS new_subscriptions,
    SUM(s.sales_qty    - s.return_qty)          AS net_qty,
    SUM(s.sales_amount - s.return_amount)       AS net_revenue,

    -- Валовая прибыль за месяц (добавлено)
    SUM(s.sales_amount - s.return_amount
        - (s.sales_qty - s.return_qty) * p.cost_rub) AS gross_profit,
    CASE
        WHEN SUM(s.sales_amount - s.return_amount) > 0
        THEN ROUND(
            SUM(s.sales_amount - s.return_amount
                - (s.sales_qty - s.return_qty) * p.cost_rub)
            / SUM(s.sales_amount - s.return_amount), 4)
        ELSE 0
    END                                         AS gross_margin,

    SUM(s.return_qty)                           AS cancellations,
    SUM(s.return_amount)                        AS cancelled_revenue,

    -- Доля промо-подписок в месяце
    ROUND(
        SUM(s.is_promo)::NUMERIC / NULLIF(COUNT(*), 0) * 100, 1
    )                                           AS promo_pct,

    -- Средняя стоимость подписки
    CASE
        WHEN SUM(s.sales_qty - s.return_qty) > 0
        THEN ROUND(
            SUM(s.sales_amount - s.return_amount)
            / SUM(s.sales_qty  - s.return_qty), 2)
        ELSE 0
    END                                         AS avg_subscription_price

FROM fact_sales s
JOIN dim_product p ON p.product_id = s.product_id
JOIN dim_date    d ON d.date        = s.date
WHERE p.format = 'Subscription'
GROUP BY d.year, d.month, d.quarter, DATE_TRUNC('month', s.date), p.title
ORDER BY month_start, subscription_type;

CREATE INDEX IF NOT EXISTS idx_mart_subs_monthly_month
    ON mart_subscriptions_monthly(year, month);