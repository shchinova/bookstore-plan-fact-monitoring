-- =============================================================================
-- 03_mart_inventory_alerts.sql
-- Витрина товаров ниже порога остатков
--
-- Создаёт 1 таблицу: mart_inventory_alerts
-- Используется: Excel → daily_pulse.xlsx (low-stock виджет),
--               Excel → reorder_tracker.xlsx (лист «К заказу»),
--               scripts/alerts.py (email-уведомления)
-- =============================================================================

DROP TABLE IF EXISTS mart_inventory_alerts;
CREATE TABLE mart_inventory_alerts AS

WITH

last_inv_date AS (
    SELECT MAX(date) AS dt FROM fact_inventory
),

-- Остатки на последнюю дату
latest_stock AS (
    SELECT
        i.product_id,
        i.closing_stock,
        i.is_low_stock,
        i.date AS stock_date
    FROM fact_inventory i
    CROSS JOIN last_inv_date l
    WHERE i.date = l.dt
),

-- Среднедневные продажи за последние 30 дней (для прогноза и рекомендации)
avg_sales_30d AS (
    SELECT
        s.product_id,
        ROUND(SUM(s.sales_qty - s.return_qty)::NUMERIC / 30, 1) AS avg_daily_sales_30d
    FROM fact_sales s
    CROSS JOIN last_inv_date l
    WHERE s.date >  l.dt - INTERVAL '30 days'
      AND s.date <= l.dt
    GROUP BY s.product_id
)

SELECT
    p.product_id,
    p.title,
    p.format,
    p.genre,
    p.publisher,
    p.price_rub,
    ls.closing_stock,
    ls.is_low_stock,
    ls.stock_date,
    -- Среднедневные продажи за 30 дней
    COALESCE(a.avg_daily_sales_30d, 0)      AS avg_daily_sales_30d,
    -- Дней до нуля при текущем темпе
    CASE
        WHEN COALESCE(a.avg_daily_sales_30d, 0) > 0
        THEN ROUND(ls.closing_stock / a.avg_daily_sales_30d)
        ELSE NULL
    END                                     AS days_until_stockout,
    -- Рекомендуемый заказ: запас на 45 дней минус текущий остаток
    GREATEST(
        0,
        CEIL(COALESCE(a.avg_daily_sales_30d, 0) * 45) - ls.closing_stock
    )::INTEGER                              AS recommended_order_qty,
    -- Оценочная стоимость рекомендуемого заказа по закупочной цене
    GREATEST(
        0,
        CEIL(COALESCE(a.avg_daily_sales_30d, 0) * 45) - ls.closing_stock
    ) * p.cost_rub                          AS recommended_order_cost,
    -- Уровень тревоги для цветовой индикации
    CASE
        WHEN ls.closing_stock = 0   THEN 'critical'   -- красный
        WHEN ls.closing_stock < 5   THEN 'urgent'     -- тёмно-оранжевый
        WHEN ls.closing_stock < 15  THEN 'warning'    -- жёлтый
        ELSE                             'ok'
    END                                     AS alert_level

FROM latest_stock ls
JOIN dim_product p
    ON  p.product_id = ls.product_id
    AND p.format IN ('Paperback', 'Hardcover')
LEFT JOIN avg_sales_30d a ON a.product_id = ls.product_id
-- Фильтруем по фактическому значению, а не по флагу (флаг может быть инвертирован аномалией)
WHERE ls.closing_stock < 15
ORDER BY ls.closing_stock ASC, a.avg_daily_sales_30d DESC NULLS LAST;

CREATE INDEX IF NOT EXISTS idx_mart_inv_alerts_level
    ON mart_inventory_alerts(alert_level);
CREATE INDEX IF NOT EXISTS idx_mart_inv_alerts_product
    ON mart_inventory_alerts(product_id);