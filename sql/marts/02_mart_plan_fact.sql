-- =============================================================================
-- 02_mart_plan_fact.sql
-- Витрина план/факт для daily_pulse.xlsx
--
-- Создаёт 4 таблицы:
--   mart_plan_fact            — план/факт на текущую дату (текущий месяц)
--   mart_daily_breakdown     — разбивка по жанрам × форматам за today/yesterday
--   mart_daily_format_pulse  — разбивка выручки по форматам за today/yesterday
--   mart_plan_fact_history   — история выполнения плана по закрытым месяцам
--
-- ВАЖНО: все три таблицы считают только книжные форматы
-- (eBook, Paperback, Hardcover, Audiobook) — без Subscription.
-- Причина: fact_plan не содержит планов для подписок, поэтому включение
-- подписок в факт создавало бы расхождение с mart_mtd_pulse.
-- =============================================================================

-- =============================================================================
-- ТАБЛИЦА 1: mart_plan_fact
-- План/факт на текущую дату в разрезе жанр × формат.
-- Используется: Excel → план/факт на дату, % выполнения, прогноз месяца.
-- =============================================================================
DROP TABLE IF EXISTS mart_plan_fact;
CREATE TABLE mart_plan_fact AS

WITH

today AS (
    SELECT MAX(date) AS dt FROM fact_sales
),

-- Фактические продажи за текущий месяц (с 1-го числа до today)
-- Только книжные форматы — без Subscription
fact_current AS (
    SELECT
        p.genre,
        p.format,
        EXTRACT(YEAR  FROM s.date)::SMALLINT  AS year,
        EXTRACT(MONTH FROM s.date)::SMALLINT  AS month,
        SUM(s.sales_qty    - s.return_qty)    AS fact_qty,
        SUM(s.sales_amount - s.return_amount) AS fact_amount
    FROM fact_sales s
    JOIN dim_product p ON p.product_id = s.product_id
    CROSS JOIN today t
    WHERE s.date >= DATE_TRUNC('month', t.dt)
      AND s.date <= t.dt
      AND p.format IN ('eBook', 'Paperback', 'Hardcover', 'Audiobook')
    GROUP BY p.genre, p.format,
             EXTRACT(YEAR  FROM s.date)::SMALLINT,
             EXTRACT(MONTH FROM s.date)::SMALLINT
),

-- Прогресс месяца: сколько дней прошло из общего числа дней в месяце
month_progress AS (
    SELECT
        t.dt                                                AS today,
        EXTRACT(DAY FROM t.dt)::NUMERIC                     AS days_passed,
        EXTRACT(DAY FROM
            DATE_TRUNC('month', t.dt)
            + INTERVAL '1 month'
            - INTERVAL '1 day'
        )::NUMERIC                                          AS days_in_month
    FROM today t
),

-- План на дату = план_месяца × (days_passed / days_in_month)
plan_to_date AS (
    SELECT
        fp.genre,
        fp.format,
        fp.year,
        fp.month,
        fp.plan_qty,
        fp.plan_amount,
        fp.plan_margin_target,
        ROUND(fp.plan_qty    * mp.days_passed / mp.days_in_month)       AS plan_qty_to_date,
        ROUND(fp.plan_amount * mp.days_passed / mp.days_in_month, 2)    AS plan_amount_to_date,
        mp.today                                                         AS report_date,
        mp.days_passed,
        mp.days_in_month
    FROM fact_plan fp
    CROSS JOIN month_progress mp
    CROSS JOIN today t
    WHERE fp.year  = EXTRACT(YEAR  FROM t.dt)::SMALLINT
      AND fp.month = EXTRACT(MONTH FROM t.dt)::SMALLINT
)

SELECT
    pl.genre,
    pl.format,
    pl.year,
    pl.month,
    -- Плановые показатели
    COALESCE(pl.plan_qty,            0)     AS plan_qty,
    COALESCE(pl.plan_amount,         0)     AS plan_amount,
    COALESCE(pl.plan_qty_to_date,    0)     AS plan_qty_to_date,
    COALESCE(pl.plan_amount_to_date, 0)     AS plan_amount_to_date,
    COALESCE(pl.plan_margin_target,  0)     AS plan_margin_target,
    -- Фактические показатели
    COALESCE(fc.fact_qty,    0)             AS fact_qty,
    COALESCE(fc.fact_amount, 0)             AS fact_amount,
    -- % выполнения плана на дату
    CASE
        WHEN COALESCE(pl.plan_amount_to_date, 0) > 0
        THEN ROUND(COALESCE(fc.fact_amount, 0) / pl.plan_amount_to_date * 100, 1)
        ELSE NULL
    END                                     AS pct_of_plan_to_date,
    -- Абсолютное отклонение
    COALESCE(fc.fact_amount, 0) - COALESCE(pl.plan_amount_to_date, 0)
                                            AS delta_amount_to_date,
    -- Прогноз закрытия месяца: факт / прошедших дней × всего дней
    CASE
        WHEN pl.days_passed > 0
        THEN ROUND(COALESCE(fc.fact_amount, 0) / pl.days_passed * pl.days_in_month, 2)
        ELSE NULL
    END                                     AS forecast_month_amount,
    pl.report_date
FROM plan_to_date pl
LEFT JOIN fact_current fc
    ON  pl.genre  = fc.genre
    AND pl.format = fc.format
    AND pl.year   = fc.year
    AND pl.month  = fc.month;

CREATE INDEX IF NOT EXISTS idx_mart_plan_fact_genre_format
    ON mart_plan_fact(genre, format);

-- =============================================================================
-- ТАБЛИЦА 2: mart_daily_breakdown
-- Разбивка по жанрам × форматам за today/yesterday.
-- Содержит промо и потерянные продажи для детального анализа дня.
-- =============================================================================
DROP TABLE IF EXISTS mart_daily_breakdown;
CREATE TABLE mart_daily_breakdown AS

WITH dates AS (
    SELECT
        MAX(date)                            AS today,
        (MAX(date) - INTERVAL '1 day')::DATE AS yesterday
    FROM fact_sales
)

SELECT
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
    -- Промо (насколько продажи органические vs скидочные)
    SUM(s.is_promo)                                               AS promo_orders,
    SUM(CASE WHEN s.is_promo = 1
             THEN s.sales_amount - s.return_amount
             ELSE 0 END)                                          AS promo_revenue,
    -- Потерянные продажи из-за дефицита (только физические форматы)
    SUM(s.lost_sales_qty)                                         AS lost_sales_qty
FROM fact_sales s
JOIN dim_product p ON p.product_id = s.product_id
CROSS JOIN dates d
WHERE s.date IN (d.today, d.yesterday)
  AND p.format IN ('eBook', 'Paperback', 'Hardcover', 'Audiobook')
GROUP BY day_type, p.genre, p.format;

CREATE INDEX IF NOT EXISTS idx_mart_daily_breakdown_type
    ON mart_daily_breakdown(day_type);

-- =============================================================================
-- ТАБЛИЦА 2b: mart_daily_format_pulse
-- Разбивка выручки по форматам за today/yesterday — без разбивки по жанрам.
-- Используется для блока «Структура выручки по форматам» в Excel:
-- быстрый ответ «какой формат тянет день, а какой провалился».
-- =============================================================================
DROP TABLE IF EXISTS mart_daily_format_pulse;
CREATE TABLE mart_daily_format_pulse AS

WITH dates AS (
    SELECT
        MAX(date)                            AS today,
        (MAX(date) - INTERVAL '1 day')::DATE AS yesterday
    FROM fact_sales
)

SELECT
    CASE WHEN s.date = d.today THEN 'today' ELSE 'yesterday' END AS day_type,
    p.format,
    COUNT(DISTINCT s.order_id)                                    AS orders,
    SUM(s.sales_qty  - s.return_qty)                              AS net_qty,
    SUM(s.sales_amount - s.return_amount)                         AS net_revenue,
    SUM(s.sales_amount - s.return_amount
        - (s.sales_qty - s.return_qty) * p.cost_rub)              AS gross_profit,
    CASE
        WHEN SUM(s.sales_amount - s.return_amount) > 0
        THEN ROUND(
            SUM(s.sales_amount - s.return_amount
                - (s.sales_qty - s.return_qty) * p.cost_rub)
            / SUM(s.sales_amount - s.return_amount), 4)
        ELSE 0
    END                                                           AS gross_margin,
    SUM(s.return_qty)                                             AS return_qty,
    SUM(s.return_amount)                                          AS return_amount,
    SUM(s.is_promo)                                               AS promo_orders,
    SUM(CASE WHEN s.is_promo = 1
             THEN s.sales_amount - s.return_amount
             ELSE 0 END)                                          AS promo_revenue,
    SUM(s.lost_sales_qty)                                         AS lost_sales_qty
FROM fact_sales s
JOIN dim_product p ON p.product_id = s.product_id
CROSS JOIN dates d
WHERE s.date IN (d.today, d.yesterday)
GROUP BY day_type, p.format;

CREATE INDEX IF NOT EXISTS idx_mart_daily_format_pulse_type
    ON mart_daily_format_pulse(day_type);

-- =============================================================================
-- ТАБЛИЦА 3: mart_plan_fact_history
-- История выполнения плана по всем месяцам (кроме текущего открытого).
-- Для закрытых месяцев: факт = весь месяц целиком, план = полный месячный план.
-- Для текущего месяца: факт = MTD, план = полный (как ориентир без пропорции).
-- =============================================================================
DROP TABLE IF EXISTS mart_plan_fact_history;
CREATE TABLE mart_plan_fact_history AS

WITH

current_month AS (
    SELECT
        EXTRACT(YEAR  FROM MAX(date))::SMALLINT AS cur_year,
        EXTRACT(MONTH FROM MAX(date))::SMALLINT AS cur_month
    FROM fact_sales
),

-- Агрегат факта по всем месяцам, только книжные форматы
fact_monthly AS (
    SELECT
        EXTRACT(YEAR  FROM s.date)::SMALLINT  AS year,
        EXTRACT(MONTH FROM s.date)::SMALLINT  AS month,
        p.genre,
        p.format,
        SUM(s.sales_qty    - s.return_qty)    AS fact_qty,
        SUM(s.sales_amount - s.return_amount) AS fact_amount
    FROM fact_sales s
    JOIN dim_product p ON p.product_id = s.product_id
    WHERE p.format IN ('eBook', 'Paperback', 'Hardcover', 'Audiobook')
    GROUP BY
        EXTRACT(YEAR  FROM s.date)::SMALLINT,
        EXTRACT(MONTH FROM s.date)::SMALLINT,
        p.genre, p.format
)

SELECT
    fp.genre,
    fp.format,
    fp.year,
    fp.month,
    fp.plan_qty,
    fp.plan_amount,
    fp.plan_margin_target,
    COALESCE(fm.fact_qty,    0)             AS fact_qty,
    COALESCE(fm.fact_amount, 0)             AS fact_amount,
    -- % выполнения: факт vs полный план (для закрытых — итоговый %, для текущего — ориентир)
    CASE
        WHEN fp.plan_amount > 0
        THEN ROUND(COALESCE(fm.fact_amount, 0) / fp.plan_amount * 100, 1)
        ELSE NULL
    END                                     AS pct_of_plan,
    COALESCE(fm.fact_amount, 0) - fp.plan_amount
                                            AS delta_amount,
    -- Флаг статуса месяца
    CASE
        WHEN fp.year < c.cur_year
          OR (fp.year = c.cur_year AND fp.month < c.cur_month)
        THEN 'closed'
        ELSE 'current'
    END                                     AS month_status

FROM fact_plan fp
LEFT JOIN fact_monthly fm
    ON  fp.genre  = fm.genre
    AND fp.format = fm.format
    AND fp.year   = fm.year
    AND fp.month  = fm.month
CROSS JOIN current_month c
WHERE fp.format IN ('eBook', 'Paperback', 'Hardcover', 'Audiobook')
ORDER BY fp.year, fp.month, fp.genre, fp.format;

CREATE INDEX IF NOT EXISTS idx_mart_plan_fact_history_ym
    ON mart_plan_fact_history(year, month);
CREATE INDEX IF NOT EXISTS idx_mart_plan_fact_history_status
    ON mart_plan_fact_history(month_status);