# Справочник SQL-запросов

> Запросы для самостоятельной работы — всё, что не вошло в витрины и отчёты.

### Быстрые проверки данных

**Последняя дата в каждой таблице фактов:**

```sql
SELECT 'fact_sales'     AS tbl, MAX(date) AS last_date FROM fact_sales
UNION ALL
SELECT 'fact_inventory' AS tbl, MAX(date)             FROM fact_inventory;
```

**Число строк по таблицам:**

```sql
SELECT 'dim_product'    AS tbl, COUNT(*) FROM dim_product
UNION ALL SELECT 'fact_sales',    COUNT(*) FROM fact_sales
UNION ALL SELECT 'fact_inventory',COUNT(*) FROM fact_inventory
UNION ALL SELECT 'fact_plan',     COUNT(*) FROM fact_plan;
```

### Продажи

**Дневная выручка с разбивкой по каналу за последние 30 дней:**

```sql
SELECT s.date, s.channel,
  SUM(s.sales_amount - s.return_amount) AS net_revenue,
  COUNT(DISTINCT s.order_id)            AS orders
FROM fact_sales s
WHERE s.date >= (SELECT MAX(date) - 30 FROM fact_sales)
GROUP BY s.date, s.channel
ORDER BY s.date DESC, net_revenue DESC;
```

**Выручка и маржа по жанру за произвольный период:**

```sql
SELECT p.genre,
  SUM(s.sales_amount - s.return_amount)                      AS net_revenue,
  SUM(s.sales_amount - s.return_amount
      - (s.sales_qty - s.return_qty) * p.cost_rub)           AS gross_profit,
  ROUND(SUM(s.sales_amount - s.return_amount
      - (s.sales_qty - s.return_qty) * p.cost_rub)
    / NULLIF(SUM(s.sales_amount - s.return_amount),0), 4)    AS gross_margin
FROM fact_sales s JOIN dim_product p ON p.product_id = s.product_id
WHERE s.date BETWEEN '2026-01-01' AND '2026-04-15'
GROUP BY p.genre ORDER BY net_revenue DESC;
```

**Топ-20 товаров по выручке за всё время (с ABC-классом):**

```sql
SELECT a.revenue_rank, p.title, p.format, p.genre,
  a.total_net_revenue, a.product_margin, a.abc_class
FROM 05_mart_abc a
JOIN dim_product p ON p.product_id = a.product_id
ORDER BY a.revenue_rank
LIMIT 20;
```

**Промо-эффективность: выручка и средний чек с промо и без:**

```sql
SELECT s.is_promo,
  COUNT(DISTINCT s.order_id)            AS orders,
  SUM(s.sales_amount - s.return_amount) AS net_revenue,
  ROUND(SUM(s.sales_amount - s.return_amount)
        / NULLIF(COUNT(DISTINCT s.order_id),0), 2) AS avg_order
FROM fact_sales s
WHERE s.date >= '2026-01-01'
GROUP BY s.is_promo;
```

**Потерянные продажи по товару (топ-10 по убыткам):**

```sql
SELECT p.title, p.format,
  SUM(s.lost_sales_qty)                         AS total_lost_qty,
  SUM(s.lost_sales_qty * s.unit_price)          AS lost_revenue_est
FROM fact_sales s JOIN dim_product p ON p.product_id = s.product_id
WHERE p.format IN ('Paperback','Hardcover')
GROUP BY p.title, p.format
ORDER BY lost_revenue_est DESC NULLS LAST
LIMIT 10;
```

### Остатки

**Динамика остатков по конкретному товару:**

```sql
SELECT i.date, i.opening_stock, i.sold_qty,
  i.replenishment_qty, i.closing_stock, i.is_low_stock
FROM fact_inventory i
WHERE i.product_id = 42   -- подставить нужный product_id
ORDER BY i.date;
```

**Товары с нулевым остатком прямо сейчас:**

```sql
SELECT p.title, p.format, p.publisher,
  i.closing_stock, i.date AS stock_date
FROM fact_inventory i
JOIN dim_product p ON p.product_id = i.product_id
WHERE i.date = (SELECT MAX(date) FROM fact_inventory)
  AND i.closing_stock = 0
ORDER BY p.title;
```

**Частота пополнений по товару:**

```sql
SELECT p.title, p.format,
  COUNT(*) FILTER (WHERE i.replenishment_qty > 0) AS replenishments,
  SUM(i.replenishment_qty)                        AS total_replenished
FROM fact_inventory i
JOIN dim_product p ON p.product_id = i.product_id
GROUP BY p.title, p.format
ORDER BY replenishments DESC;
```

### Планирование

**Выполнение плана по всем закрытым месяцам — сводка:**

```sql
SELECT year, month,
  SUM(plan_amount)  AS total_plan,
  SUM(fact_amount)  AS total_fact,
  ROUND(SUM(fact_amount)/NULLIF(SUM(plan_amount),0)*100,1) AS pct
FROM mart_plan_fact_history
WHERE month_status = 'closed'
GROUP BY year, month
ORDER BY year, month;
```

**Жанры, систематически не выполняющие план (меньше 80% чаще 2 раз):**

```sql
SELECT genre, COUNT(*) AS missed_months
FROM mart_plan_fact_history
WHERE month_status = 'closed'
  AND pct_of_plan < 80
GROUP BY genre
HAVING COUNT(*) >= 2
ORDER BY missed_months DESC;
```

### Маржинальность

**Сравнение маржи формата год к году:**

```sql
SELECT format, year,
  ROUND(SUM(gross_profit)/NULLIF(SUM(net_revenue),0),4) AS gross_margin
FROM 04_mart_sales_trends
WHERE format != 'Subscription'
GROUP BY format, year
ORDER BY format, year;
```

**Издатели с маржой ниже 30% и выручкой выше среднего:**

```sql
SELECT publisher, format, gross_margin, net_revenue, rank_overall
FROM mart_margin_by_publisher
WHERE gross_margin < 0.30
  AND net_revenue > (SELECT AVG(net_revenue) FROM mart_margin_by_publisher)
ORDER BY gross_margin ASC;
```

### Подписки

**Помесячная динамика отмен vs новых подписок:**

```sql
SELECT year, month, subscription_type,
  new_subscriptions, cancellations,
  ROUND(cancellations::NUMERIC/NULLIF(new_subscriptions,0)*100,1) AS churn_pct
FROM mart_subscriptions_monthly
ORDER BY year, month, subscription_type;
```