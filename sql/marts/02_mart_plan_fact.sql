-- =============================================================================
-- 02_mart_plan_fact.sql
-- Витрина план/факт для оперативного отчёта daily_pulse.xlsx
--
-- Логика:
--   - Факт: продажи с начала месяца до today включительно
--   - План на дату: план_месяца × (день_today / дней_в_месяце)
--   - Срез: genre × format × year × month
--
-- Используется: Excel → Power Query → daily_pulse.xlsx (лист «Сегодня»)
-- =============================================================================

DROP TABLE IF EXISTS mart_plan_fact;

CREATE TABLE mart_plan_fact AS

WITH

today AS (
    SELECT MAX(date) AS dt FROM fact_sales
),

-- Фактические продажи за текущий месяц (с 1-го числа до today)
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
    GROUP BY p.genre, p.format, year, month
),

-- Прогресс месяца: сколько дней прошло из общего числа дней в месяце
month_progress AS (
    SELECT
        t.dt                                               AS today,
        EXTRACT(DAY FROM t.dt)::NUMERIC                    AS days_passed,
        EXTRACT(DAY FROM
            DATE_TRUNC('month', t.dt)
            + INTERVAL '1 month'
            - INTERVAL '1 day'
        )::NUMERIC                                         AS days_in_month
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
        ROUND(fp.plan_qty    * mp.days_passed / mp.days_in_month) AS plan_qty_to_date,
        ROUND(fp.plan_amount * mp.days_passed / mp.days_in_month, 2) AS plan_amount_to_date
    FROM fact_plan fp
    CROSS JOIN month_progress mp
    CROSS JOIN today t
    WHERE fp.year  = EXTRACT(YEAR  FROM t.dt)
      AND fp.month = EXTRACT(MONTH FROM t.dt)
)

SELECT
    COALESCE(pl.genre,  fc.genre)   AS genre,
    COALESCE(pl.format, fc.format)  AS format,
    COALESCE(pl.year,   fc.year)    AS year,
    COALESCE(pl.month,  fc.month)   AS month,

    -- Плановые показатели
    COALESCE(pl.plan_qty,            0)  AS plan_qty,
    COALESCE(pl.plan_amount,         0)  AS plan_amount,
    COALESCE(pl.plan_qty_to_date,    0)  AS plan_qty_to_date,
    COALESCE(pl.plan_amount_to_date, 0)  AS plan_amount_to_date,
    COALESCE(pl.plan_margin_target,  0)  AS plan_margin_target,

    -- Фактические показатели
    COALESCE(fc.fact_qty,    0)          AS fact_qty,
    COALESCE(fc.fact_amount, 0)          AS fact_amount,

    -- Выполнение плана на дату (%)
    CASE
        WHEN COALESCE(pl.plan_amount_to_date, 0) > 0
        THEN ROUND(
            COALESCE(fc.fact_amount, 0) / pl.plan_amount_to_date * 100, 1
        )
        ELSE NULL
    END AS pct_of_plan_to_date,

    -- Абсолютное отклонение факта от плана на дату
    COALESCE(fc.fact_amount, 0) - COALESCE(pl.plan_amount_to_date, 0)
        AS delta_amount_to_date,

    (SELECT dt FROM today) AS report_date

FROM plan_to_date pl
FULL OUTER JOIN fact_current fc
    ON  pl.genre  = fc.genre
    AND pl.format = fc.format
    AND pl.year   = fc.year
    AND pl.month  = fc.month;

CREATE INDEX IF NOT EXISTS idx_mart_plan_fact_genre_format
    ON mart_plan_fact(genre, format);