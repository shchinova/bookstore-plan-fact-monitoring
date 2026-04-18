-- =============================================================================
-- 03_mart_inventory_alerts.sql
-- Витрина товаров с низким остатком для daily_pulse.xlsx и reorder_tracker.xlsx
--
-- Логика:
--   - Берём остатки на последнюю дату в fact_inventory
--   - Низкий остаток: closing_stock < 15 (константа LOW_STOCK_THRESHOLD)
--   - Добавляем средние продажи за последние 30 дней -> рекомендованный заказ
--
-- Используется:
--   Excel -> Power Query -> daily_pulse.xlsx     (виджет low-stock)
--   Excel -> Power Query -> reorder_tracker.xlsx (лист «К заказу»)
--
-- ИЗМЕНЕНИЯ:
--   [FIX-1] Фильтр изменён с WHERE is_low_stock = 1 на WHERE closing_stock < 15.
--           Флаг is_low_stock может быть инвертирован аномалией в исходных данных
--           (аномалия №20 в generate_bookstore_data.py), поэтому полагаться на
--           него как на основной фильтр ненадёжно. Прямое сравнение с порогом
--           гарантирует, что ни один критический товар не пропадёт из витрины.
--   [FIX-2] Добавлен явный фильтр p.format IN ('Paperback', 'Hardcover') в JOIN
--           с dim_product: fact_inventory содержит только физические товары,
--           фильтр защищает от изменений модели данных в будущем.
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

-- Средние продажи за последние 30 дней (для расчёта рекомендованного заказа)
avg_sales_30d AS (
    SELECT
        s.product_id,
        ROUND(
            SUM(s.sales_qty - s.return_qty)::NUMERIC / 30, 1
        ) AS avg_daily_sales_30d
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
    ls.closing_stock,
    ls.is_low_stock,
    ls.stock_date,
    -- Среднедневные продажи за 30 дней
    COALESCE(a.avg_daily_sales_30d, 0)      AS avg_daily_sales_30d,
    -- Дней до нуля при текущем темпе продаж
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
    -- Уровень тревоги для цветовой индикации в Excel
    CASE
        WHEN ls.closing_stock = 0   THEN 'critical'   -- красный
        WHEN ls.closing_stock < 5   THEN 'urgent'     -- тёмно-красный
        WHEN ls.closing_stock < 15  THEN 'warning'    -- жёлтый
        ELSE                             'ok'          -- зелёный (не попадёт в витрину)
    END                                     AS alert_level

FROM latest_stock ls
-- [FIX-2] Явный фильтр на физические форматы
JOIN dim_product p
    ON  p.product_id = ls.product_id
    AND p.format IN ('Paperback', 'Hardcover')
LEFT JOIN avg_sales_30d a ON a.product_id = ls.product_id
-- [FIX-1] Фильтруем по фактическому значению остатка, а не по флагу
WHERE ls.closing_stock < 15
ORDER BY ls.closing_stock ASC, a.avg_daily_sales_30d DESC NULLS LAST;

CREATE INDEX IF NOT EXISTS idx_mart_inv_alerts_level
    ON mart_inventory_alerts(alert_level);