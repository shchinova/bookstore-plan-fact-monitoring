-- =============================================================================
-- 02_mart_plan_fact.sql
-- Витрина план/факт для оперативного отчёта daily_pulse.xlsx
--
-- Логика:
--   - Факт: продажи с начала месяца до today включительно
--   - План на дату: план_месяца x (день_today / дней_в_месяце)
--   - Срез: genre x format x year x month
--
-- Используется: Excel -> Power Query -> daily_pulse.xlsx (лист «Сегодня»)
--
-- ИЗМЕНЕНИЯ:
--   [FIX-1] EXTRACT(...) явно приводится к SMALLINT во всех WHERE/JOIN,
--           чтобы тип совпадал с SMALLINT-колонками fact_plan.
--   [FIX-2] report_date берётся из mp.today (уже доступен через CROSS JOIN),
--           а не через дорогой коррелированный подзапрос на каждую строку.
--   [FIX-3] FULL OUTER JOIN заменён на LEFT JOIN: fact_plan генерируется
--           по всем комбинациям из продаж, поэтому строк «факт без плана»
--           в нормальных данных не бывает. LEFT JOIN точнее отражает логику.
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
    GROUP BY p.genre, p.format,
             EXTRACT(YEAR  FROM s.date)::SMALLINT,
             EXTRACT(MONTH FROM s.date)::SMALLINT
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

-- План на дату = план_месяца x (days_passed / days_in_month)
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
        mp.today                                                         AS report_date
    FROM fact_plan fp
    CROSS JOIN month_progress mp
    CROSS JOIN today t
    -- [FIX-1] Явный каст EXTRACT -> SMALLINT совпадает с типом колонок fact_plan
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
    -- Выполнение плана на дату (%)
    CASE
        WHEN COALESCE(pl.plan_amount_to_date, 0) > 0
        THEN ROUND(
            COALESCE(fc.fact_amount, 0) / pl.plan_amount_to_date * 100, 1
        )
        ELSE NULL
    END                                     AS pct_of_plan_to_date,
    -- Абсолютное отклонение факта от плана на дату
    COALESCE(fc.fact_amount, 0) - COALESCE(pl.plan_amount_to_date, 0)
                                            AS delta_amount_to_date,
    -- [FIX-2] report_date из plan_to_date, не через коррелированный подзапрос
    pl.report_date
FROM plan_to_date pl
-- [FIX-3] LEFT JOIN вместо FULL OUTER JOIN: fact_plan покрывает все комбинации продаж
LEFT JOIN fact_current fc
    ON  pl.genre  = fc.genre
    AND pl.format = fc.format
    AND pl.year   = fc.year
    AND pl.month  = fc.month;

CREATE INDEX IF NOT EXISTS idx_mart_plan_fact_genre_format
    ON mart_plan_fact(genre, format);