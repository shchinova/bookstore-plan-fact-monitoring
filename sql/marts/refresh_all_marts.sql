-- =============================================================================
-- refresh_all_marts.sql
-- Пересчитывает все витрины данных в правильном порядке.
--
-- Запускается из Python:
--   load_history.py  — после первичной загрузки данных
--   daily_update.py  — после каждого ежедневного обновления
--
-- Порядок важен: mart_daily_pulse и mart_plan_fact зависят от актуальных
-- данных в fact_sales, поэтому они идут последними среди оперативных витрин.
-- =============================================================================

\i sql/marts/01_mart_daily_pulse.sql
\i sql/marts/02_mart_plan_fact.sql
\i sql/marts/03_mart_inventory_alerts.sql
\i sql/marts/04_mart_sales_trends.sql
\i sql/marts/05_mart_abc.sql
\i sql/marts/06_mart_margin.sql
\i sql/marts/07_mart_subscriptions.sql