-- =============================================================================
-- 05_mart_abc.sql
-- ABC-классификация ассортимента по вкладу в выручку
--
-- Логика:
--   A — топ 80% выручки
--   B — следующие 15% (80–95%)
--   C — последние 5% (95–100%)
--
-- Грануляция: один продукт = одна строка
-- Используется: Tableau (страница «Ассортимент»), notebooks/abc_analysis.ipynb
-- =============================================================================

DROP TABLE IF EXISTS mart_abc;

CREATE TABLE mart_abc AS

WITH

-- Суммарные продажи по каждому продукту за весь период
product_totals AS (
    SELECT
        s.product_id,
        SUM(s.sales_qty    - s.return_qty)    AS total_net_qty,
        SUM(s.sales_amount - s.return_amount) AS total_net_revenue,
        COUNT(DISTINCT s.date)                AS active_days,
        COUNT(DISTINCT s.order_id)            AS total_orders,
        SUM(s.lost_sales_qty)                 AS total_lost_qty
    FROM fact_sales s
    GROUP BY s.product_id
),

-- Общая выручка по всем продуктам
grand_total AS (
    SELECT SUM(total_net_revenue) AS total FROM product_totals
),

-- Ранжирование и накопленная доля
ranked AS (
    SELECT
        pt.product_id,
        pt.total_net_qty,
        pt.total_net_revenue,
        pt.active_days,
        pt.total_orders,
        pt.total_lost_qty,
        gt.total                             AS grand_total_revenue,
        ROUND(pt.total_net_revenue / gt.total * 100, 2) AS revenue_pct,
        SUM(pt.total_net_revenue) OVER (
            ORDER BY pt.total_net_revenue DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )                                    AS cumulative_revenue,
        SUM(pt.total_net_revenue) OVER (
            ORDER BY pt.total_net_revenue DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) / gt.total * 100                   AS cumulative_pct,
        RANK() OVER (ORDER BY pt.total_net_revenue DESC) AS revenue_rank
    FROM product_totals pt
    CROSS JOIN grand_total gt
)

SELECT
    r.product_id,
    p.title,
    p.author,
    p.publisher,
    p.genre,
    p.format,
    p.language,
    p.price_rub,
    p.cost_rub,
    p.avg_rating,
    p.review_count,

    r.total_net_qty,
    r.total_net_revenue,
    r.active_days,
    r.total_orders,
    r.total_lost_qty,
    r.grand_total_revenue,
    r.revenue_pct,
    r.cumulative_pct,
    r.revenue_rank,

    -- ABC-категория
    CASE
        WHEN r.cumulative_pct <= 80 THEN 'A'
        WHEN r.cumulative_pct <= 95 THEN 'B'
        ELSE                             'C'
    END AS abc_class,

    -- Маржинальность продукта
    CASE
        WHEN r.total_net_revenue > 0
        THEN ROUND(
            (r.total_net_revenue - r.total_net_qty * p.cost_rub)
            / r.total_net_revenue, 4
        )
        ELSE 0
    END AS product_margin

FROM ranked r
JOIN dim_product p ON p.product_id = r.product_id
ORDER BY r.revenue_rank;

CREATE INDEX IF NOT EXISTS idx_mart_abc_class
    ON mart_abc(abc_class);
CREATE INDEX IF NOT EXISTS idx_mart_abc_format
    ON mart_abc(format);