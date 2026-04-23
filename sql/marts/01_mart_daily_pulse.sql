-- =============================================================================
-- Пересоздаём все VIEW с нуля: DROP + CREATE вместо CREATE OR REPLACE,
-- чтобы PostgreSQL не ругался при изменении структуры колонок.
-- CASCADE гарантирует что зависимые объекты тоже удалятся.
-- =============================================================================
DROP VIEW IF EXISTS mart_daily_top5        CASCADE;
DROP VIEW IF EXISTS mart_daily_revenue_mtd CASCADE;
DROP VIEW IF EXISTS mart_mtd_pulse         CASCADE;
DROP VIEW IF EXISTS mart_daily_channels    CASCADE;
DROP VIEW IF EXISTS mart_daily_pulse       CASCADE;
DROP VIEW IF EXISTS _v_daily_channels      CASCADE;
DROP VIEW IF EXISTS _v_mtd_prev            CASCADE;
DROP VIEW IF EXISTS _v_mtd_current         CASCADE;
DROP VIEW IF EXISTS _v_daily_sales         CASCADE;
DROP VIEW IF EXISTS _v_dates               CASCADE;

-- =============================================================================
-- 01_mart_daily_pulse.sql
-- Витрина оперативных KPI для daily_pulse.xlsx
--
-- Реализация: VIEW.
-- Создаёт объекты:
--   _v_dates               — служебный: опорные даты
--   _v_daily_sales         — служебный: продажи за today/yesterday по жанру/формату
--   _v_daily_channels      — служебный: продажи за today/yesterday по каналу
--   _v_mtd_current         — служебный: MTD текущего месяца
--   _v_mtd_prev            — служебный: MTD прошлого месяца (тот же период)
--   mart_daily_pulse       — KPI текущего и прошлого дня (2 строки)
--   mart_daily_channels    — разбивка продаж по каналам за today/yesterday
--   mart_mtd_pulse         — накопительный итог текущего месяца (1 строка)
--   mart_daily_revenue_mtd — выручка по дням текущего месяца
--   mart_daily_top5        — топ-5 товаров текущего дня
-- =============================================================================

-- =============================================================================
-- ВСПОМОГАТЕЛЬНЫЕ VIEW (не экспортируются в Excel)
-- =============================================================================

-- Опорные даты: today и yesterday
CREATE VIEW _v_dates AS
SELECT
    MAX(date)                            AS today,
    (MAX(date) - INTERVAL '1 day')::DATE AS yesterday
FROM fact_sales;

-- Продажи за today и yesterday в разрезе жанра и формата.
-- Содержит все метрики для mart_daily_pulse и mart_daily_breakdown.
CREATE VIEW _v_daily_sales AS
SELECT
    s.date,
    CASE WHEN s.date = d.today THEN 'today' ELSE 'yesterday' END AS day_type,
    p.genre,
    p.format,
    COUNT(DISTINCT s.order_id)                                    AS orders,
    SUM(s.sales_qty  - s.return_qty)                              AS net_qty,
    SUM(s.sales_amount - s.return_amount)                         AS net_revenue,
    SUM(s.sales_amount - s.return_amount
        - (s.sales_qty - s.return_qty) * p.cost_rub)              AS gross_profit,
    -- Возвраты
    SUM(s.return_qty)                                             AS return_qty,
    SUM(s.return_amount)                                          AS return_amount,
    -- Промо
    SUM(s.is_promo)                                               AS promo_orders,
    SUM(CASE WHEN s.is_promo = 1
             THEN s.sales_amount - s.return_amount
             ELSE 0 END)                                          AS promo_revenue,
    -- Потерянные продажи из-за дефицита (только физические форматы)
    SUM(s.lost_sales_qty)                                         AS lost_sales_qty
FROM fact_sales s
JOIN dim_product p ON p.product_id = s.product_id
CROSS JOIN _v_dates d
WHERE s.date IN (d.today, d.yesterday)
GROUP BY s.date, day_type, p.genre, p.format;

-- Продажи за today и yesterday в разрезе канала.
-- Нужен для mart_daily_channels — отдельная таблица в Excel.
CREATE VIEW _v_daily_channels AS
SELECT
    s.date,
    CASE WHEN s.date = d.today THEN 'today' ELSE 'yesterday' END AS day_type,
    s.channel,
    COUNT(DISTINCT s.order_id)                                    AS orders,
    SUM(s.sales_qty  - s.return_qty)                              AS net_qty,
    SUM(s.sales_amount - s.return_amount)                         AS net_revenue,
    SUM(s.sales_amount - s.return_amount
        - (s.sales_qty - s.return_qty) * p.cost_rub)              AS gross_profit,
    SUM(s.is_promo)                                               AS promo_orders,
    SUM(CASE WHEN s.is_promo = 1
             THEN s.sales_amount - s.return_amount
             ELSE 0 END)                                          AS promo_revenue
FROM fact_sales s
JOIN dim_product p ON p.product_id = s.product_id
CROSS JOIN _v_dates d
WHERE s.date IN (d.today, d.yesterday)
GROUP BY s.date, day_type, s.channel;

-- MTD текущего месяца (1-е число до today включительно)
CREATE VIEW _v_mtd_current AS
SELECT
    COUNT(DISTINCT s.order_id)                              AS mtd_orders,
    SUM(s.sales_qty  - s.return_qty)                        AS mtd_net_qty,
    SUM(s.sales_amount - s.return_amount)                   AS mtd_revenue,
    SUM(s.sales_amount - s.return_amount
        - (s.sales_qty - s.return_qty) * p.cost_rub)        AS mtd_gross_profit,
    -- Возвраты MTD
    SUM(s.return_qty)                                       AS mtd_return_qty,
    SUM(s.return_amount)                                    AS mtd_return_amount,
    -- Промо MTD
    SUM(s.is_promo)                                         AS mtd_promo_orders,
    SUM(CASE WHEN s.is_promo = 1
             THEN s.sales_amount - s.return_amount
             ELSE 0 END)                                    AS mtd_promo_revenue,
    -- Потерянные продажи MTD
    SUM(s.lost_sales_qty)                                   AS mtd_lost_sales_qty
FROM fact_sales s
JOIN dim_product p ON p.product_id = s.product_id
CROSS JOIN _v_dates d
WHERE s.date >= DATE_TRUNC('month', d.today)::DATE
  AND s.date <= d.today;

-- MTD прошлого месяца (тот же период: 1-е до того же дня месяца).
-- Если today = 15.04, берём 01.03–15.03 — корректное сравнение для
-- незакрытого месяца (полный март дал бы заведомо большие цифры).
CREATE VIEW _v_mtd_prev AS
SELECT
    COUNT(DISTINCT s.order_id)                              AS prev_mtd_orders,
    SUM(s.sales_qty  - s.return_qty)                        AS prev_mtd_net_qty,
    SUM(s.sales_amount - s.return_amount)                   AS prev_mtd_revenue,
    SUM(s.sales_amount - s.return_amount
        - (s.sales_qty - s.return_qty) * p.cost_rub)        AS prev_mtd_gross_profit,
    -- Возвраты prev MTD
    SUM(s.return_qty)                                       AS prev_mtd_return_qty,
    SUM(s.return_amount)                                    AS prev_mtd_return_amount
FROM fact_sales s
JOIN dim_product p ON p.product_id = s.product_id
CROSS JOIN _v_dates d
WHERE s.date >= (DATE_TRUNC('month', d.today) - INTERVAL '1 month')::DATE
  AND s.date <= (d.today - INTERVAL '1 month')::DATE;

-- =============================================================================
-- ВИТРИНА 1: mart_daily_pulse
-- KPI текущего и прошлого дня — 2 строки (today / yesterday).
-- Содержит все дневные метрики: выручка, заказы, возвраты, промо,
-- потерянные продажи, маржа.
-- =============================================================================
CREATE VIEW mart_daily_pulse AS
SELECT
    ds.day_type,
    CASE WHEN ds.day_type = 'today'
         THEN d.today
         ELSE d.yesterday
    END::DATE                                               AS report_date,
    CASE WHEN ds.day_type = 'today'
         THEN d.yesterday
         ELSE (d.yesterday - INTERVAL '1 day')::DATE
    END::DATE                                               AS prev_date,

    -- Основные KPI
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

    -- Возвраты (сигнал о проблеме с качеством или партией)
    SUM(ds.return_qty)                                      AS return_qty,
    SUM(ds.return_amount)                                   AS return_amount,
    -- Доля возвратов в выручке (до вычета возвратов)
    CASE
        WHEN SUM(ds.net_revenue + ds.return_amount) > 0
        THEN ROUND(
            SUM(ds.return_amount)
            / SUM(ds.net_revenue + ds.return_amount) * 100, 1)
        ELSE 0
    END                                                     AS return_rate_pct,

    -- Промо (насколько день «органический»)
    SUM(ds.promo_orders)                                    AS promo_orders,
    SUM(ds.promo_revenue)                                   AS promo_revenue,
    CASE
        WHEN SUM(ds.orders) > 0
        THEN ROUND(SUM(ds.promo_orders)::NUMERIC / SUM(ds.orders) * 100, 1)
        ELSE 0
    END                                                     AS promo_orders_pct,

    -- Потерянные продажи из-за дефицита
    SUM(ds.lost_sales_qty)                                  AS lost_sales_qty

FROM _v_daily_sales ds
CROSS JOIN _v_dates d
GROUP BY ds.day_type, d.today, d.yesterday;

-- =============================================================================
-- ВИТРИНА 2: mart_daily_channels
-- Продажи по каналам за today и yesterday.
-- Позволяет быстро заметить просадку конкретного канала.
-- =============================================================================
CREATE VIEW mart_daily_channels AS
SELECT
    dc.day_type,
    CASE WHEN dc.day_type = 'today'
         THEN d.today
         ELSE d.yesterday
    END::DATE                                               AS report_date,
    dc.channel,
    dc.orders,
    dc.net_qty,
    dc.net_revenue,
    dc.gross_profit,
    CASE
        WHEN dc.net_revenue > 0
        THEN ROUND(dc.gross_profit / dc.net_revenue, 4)
        ELSE 0
    END                                                     AS gross_margin,
    dc.promo_orders,
    dc.promo_revenue,
    CASE
        WHEN dc.orders > 0
        THEN ROUND(dc.promo_orders::NUMERIC / dc.orders * 100, 1)
        ELSE 0
    END                                                     AS promo_orders_pct
FROM _v_daily_channels dc
CROSS JOIN _v_dates d;

-- =============================================================================
-- ВИТРИНА 3: mart_mtd_pulse
-- Накопительный итог текущего месяца — 1 строка.
-- Дельты не хранятся: считаются формулами в Excel.
-- =============================================================================
CREATE VIEW mart_mtd_pulse AS
SELECT
    DATE_TRUNC('month', d.today)::DATE  AS month_start,
    d.today                             AS month_to_date,

    -- MTD текущего месяца
    m.mtd_revenue,
    m.mtd_orders,
    m.mtd_net_qty,
    m.mtd_gross_profit,
    CASE
        WHEN m.mtd_revenue > 0
        THEN ROUND(m.mtd_gross_profit / m.mtd_revenue, 4)
        ELSE 0
    END                                 AS mtd_gross_margin,
    CASE
        WHEN m.mtd_orders > 0
        THEN ROUND(m.mtd_revenue / m.mtd_orders, 2)
        ELSE 0
    END                                 AS mtd_avg_order_value,
    -- Возвраты MTD
    m.mtd_return_qty,
    m.mtd_return_amount,
    CASE
        WHEN (m.mtd_revenue + m.mtd_return_amount) > 0
        THEN ROUND(
            m.mtd_return_amount
            / (m.mtd_revenue + m.mtd_return_amount) * 100, 1)
        ELSE 0
    END                                 AS mtd_return_rate_pct,
    -- Промо MTD
    m.mtd_promo_orders,
    m.mtd_promo_revenue,
    CASE
        WHEN m.mtd_orders > 0
        THEN ROUND(m.mtd_promo_orders::NUMERIC / m.mtd_orders * 100, 1)
        ELSE 0
    END                                 AS mtd_promo_orders_pct,
    -- Потерянные продажи MTD
    m.mtd_lost_sales_qty,

    -- MTD прошлого месяца (для сравнения)
    pm.prev_mtd_revenue,
    pm.prev_mtd_orders,
    pm.prev_mtd_net_qty,
    pm.prev_mtd_gross_profit,
    CASE
        WHEN pm.prev_mtd_revenue > 0
        THEN ROUND(pm.prev_mtd_gross_profit / pm.prev_mtd_revenue, 4)
        ELSE 0
    END                                 AS prev_mtd_gross_margin,
    CASE
        WHEN pm.prev_mtd_orders > 0
        THEN ROUND(pm.prev_mtd_revenue / pm.prev_mtd_orders, 2)
        ELSE 0
    END                                 AS prev_mtd_avg_order_value,
    -- Возвраты prev MTD (для сравнения динамики возвратов)
    pm.prev_mtd_return_qty,
    pm.prev_mtd_return_amount

FROM _v_mtd_current m
CROSS JOIN _v_mtd_prev pm
CROSS JOIN _v_dates d;

-- =============================================================================
-- ВИТРИНА 4: mart_daily_revenue_mtd
-- Выручка по дням текущего месяца + плановый суточный темп.
-- Используется для линейного графика «факт vs плановый темп» в Excel.
-- =============================================================================
CREATE VIEW mart_daily_revenue_mtd AS
SELECT
    s.date,
    SUM(s.sales_amount - s.return_amount)                   AS net_revenue,
    SUM(s.sales_amount - s.return_amount
        - (s.sales_qty - s.return_qty) * p.cost_rub)         AS gross_profit,
    COUNT(DISTINCT s.order_id)                               AS orders,
    SUM(s.return_qty)                                        AS return_qty,
    SUM(s.return_amount)                                     AS return_amount,
    -- Плановый суточный темп = план месяца / дней в месяце.
    -- Показывается как горизонтальная линия на графике.
    ROUND(
        (SELECT SUM(fp.plan_amount)
         FROM fact_plan fp
         CROSS JOIN _v_dates d2
         WHERE fp.year  = EXTRACT(YEAR  FROM d2.today)::SMALLINT
           AND fp.month = EXTRACT(MONTH FROM d2.today)::SMALLINT
           AND fp.format IN ('eBook','Paperback','Hardcover','Audiobook'))
        / EXTRACT(DAY FROM (
            DATE_TRUNC('month', (SELECT today FROM _v_dates))
            + INTERVAL '1 month'
            - INTERVAL '1 day'
          ))::NUMERIC
    , 2)                                                     AS daily_plan_target
FROM fact_sales s
JOIN dim_product p ON p.product_id = s.product_id
CROSS JOIN _v_dates d
WHERE s.date >= DATE_TRUNC('month', d.today)::DATE
GROUP BY s.date
ORDER BY s.date;

-- =============================================================================
-- ВИТРИНА 5: mart_daily_top5
-- Топ-5 товаров текущего дня по чистой выручке.
-- =============================================================================
CREATE VIEW mart_daily_top5 AS
SELECT product_id, title, format, genre, revenue, net_qty, rnk
FROM (
    SELECT
        s.product_id,
        p.title,
        p.format,
        p.genre,
        SUM(s.sales_amount - s.return_amount)   AS revenue,
        SUM(s.sales_qty - s.return_qty)         AS net_qty,
        RANK() OVER (
            ORDER BY SUM(s.sales_amount - s.return_amount) DESC
        )                                       AS rnk
    FROM fact_sales s
    JOIN dim_product p ON p.product_id = s.product_id
    CROSS JOIN _v_dates d
    WHERE s.date = d.today
    GROUP BY s.product_id, p.title, p.format, p.genre
) ranked
WHERE rnk <= 5;