# Каталог чартов

> KPI, графики, таблицы, визуализации.
> ОТ = оперативный отчёт  ·  УП = управление остатками  ·  Д = дашборд Tableau

### Блок А. Оперативные KPI (день / MTD)

*Источник: 01_mart_daily_pulse (строка day_type = 'today'), mart_mtd_pulse.*

|**#**|**Название**|**Тип**|**Поля**|**Формула в Excel / примечание**|**Куда**|
|---|---|---|---|---|---|
|А-1|Выручка за день|KPI + дельта|total_revenue (today / yesterday)|=[today] — дельта: =[today]-[yesterday], %: =([today]-[yesterday])/[yesterday]|ОТ|
|А-2|Заказы за день|KPI + дельта|total_orders (today / yesterday)|Аналогично А-1|ОТ|
|А-3|Средний чек за день|KPI + дельта|avg_order_value (today / yesterday)|Аналогично А-1|ОТ|
|А-4|Gross margin за день|KPI + дельта|gross_margin (today / yesterday)|Дельта в п.п.: =[today]-[yesterday]|ОТ|
|А-5|Выручка MTD|KPI + дельта к пр. мес.|mtd_revenue, prev_mtd_revenue|Δ абс.: =mtd-prev_mtd  Δ%: =(mtd-prev)/prev|ОТ|
|А-6|Заказы MTD|KPI + дельта|mtd_orders, prev_mtd_orders|Аналогично А-5|ОТ|
|А-7|Валовая прибыль MTD|KPI + дельта|mtd_gross_profit, prev_mtd_gross_profit|Аналогично А-5|ОТ|
|А-8|Gross margin MTD|KPI + дельта|mtd_gross_margin, prev_mtd_gross_margin|Дельта в п.п.|ОТ|
|А-9|Средний чек MTD|KPI + дельта|mtd_avg_order_value, prev_mtd_avg_order_value|Аналогично А-5|ОТ|

### Блок Б. Выполнение плана

*Источник: 02_mart_plan_fact (текущий месяц), mart_plan_fact_history (закрытые месяцы).*

|**#**|**Название**|**Тип**|**Поля**|**Формула в Excel / примечание**|**Куда**|
|---|---|---|---|---|---|
|Б-1|% выполнения плана на дату|KPI|pct_of_plan_to_date (агрегат по всем genre/format)|=SUMIF([format],...,[fact_amount]) / SUMIF([format],...,[plan_amount_to_date])|ОТ|
|Б-2|Прогноз закрытия месяца|KPI|fact_amount, days_passed (день месяца), days_in_month|=SUM(fact_amount)/DAY(TODAY())*DAY(EOMONTH(TODAY(),0))|ОТ|
|Б-3|Отклонение факта от плана (абс.)|KPI|delta_amount_to_date|=SUM(delta_amount_to_date)|ОТ|
|Б-4|Факт vs план-на-дату по жанрам|Сводная таблица|genre, fact_amount, plan_amount_to_date, pct_of_plan_to_date|Строки: genre. Значения: fact, plan_to_date, %. Условное форматирование по %|ОТ|
|Б-5|Факт vs план-на-дату по форматам|Сводная таблица|format, fact_amount, plan_amount_to_date, pct_of_plan_to_date|Строки: format. Аналогично Б-4|ОТ|
|Б-6|История выполнения плана по месяцам|График (линии)|mart_plan_fact_history: year, month, pct_of_plan (closed)|Ось X: год+месяц. Линия: pct_of_plan. Цель: 100%. Только month_status='closed'|Д|
|Б-7|Тепловая карта план/факт (жанр × месяц)|Диаграмма (heatmap)|mart_plan_fact_history: genre, month, pct_of_plan|В Tableau: Rows=genre, Cols=month, Color=pct_of_plan|Д|

### Блок В. Тренды продаж

*Источник: 04_mart_sales_trends (месячный), mart_daily_revenue_mtd (дневной, текущий месяц).*

|**#**|**Название**|**Тип**|**Поля**|**Формула в Excel / примечание**|**Куда**|
|---|---|---|---|---|---|
|В-1|Выручка по дням текущего месяца|График (линия)|mart_daily_revenue_mtd: date, net_revenue|Линейный график. Ось X: date, Y: net_revenue. Доп. линия: плановый темп = plan_amount/days_in_month|ОТ|
|В-2|Выручка по месяцам (история)|График (линия)|mart_sales_trends: month_start, net_revenue (GROUP BY month_start)|Ось X: month_start, Y: SUM(net_revenue). Фильтр по году|Д|
|В-3|Выручка по форматам (месячно)|График (grouped bar)|mart_sales_trends: month_start, format, net_revenue|Ось X: month_start, группировка по format|Д|
|В-4|Выручка по жанрам (месячно)|График (stacked bar)|mart_sales_trends: month_start, genre, net_revenue|Ось X: month_start, стек по genre|Д|
|В-5|Доля возвратов в выручке|График (линия)|mart_sales_trends: month_start, return_revenue, gross_revenue|Y: return_revenue/gross_revenue. Тренд качества|Д|
|В-6|Промо-выручка vs обычная|График (stacked bar)|mart_sales_trends: month_start, promo_revenue, net_revenue-promo_revenue|Стек: промо / органика|Д|
|В-7|Потерянные продажи по месяцам|График (bar)|mart_sales_trends: month_start, lost_sales_qty (физ. форматы)|Бар по месяцам. Фильтр: format IN (Paperback, Hardcover)|Д|

### Блок Г. Маржинальность

*Источник: mart_margin_by_format, mart_margin_by_publisher, 04_mart_sales_trends.*

|**#**|**Название**|**Тип**|**Поля**|**Формула в Excel / примечание**|**Куда**|
|---|---|---|---|---|---|
|Г-1|Маржа по форматам (месячно)|График (линии)|mart_margin_by_format: month_start, format, gross_margin|Одна линия на формат. Ось Y: gross_margin (0–1, формат %)|Д|
|Г-2|Маржа vs выручка (scatter)|Диаграмма (scatter)|mart_margin_by_format: net_revenue, gross_margin, format|Пузырьковая: X=net_revenue, Y=gross_margin, размер=net_qty|Д|
|Г-3|Рейтинг издателей по прибыли|Таблица (горизонтальный bar)|mart_margin_by_publisher: publisher, gross_profit, gross_margin, rank_overall|TOP-20 издателей. Условное форматирование gross_margin|Д|
|Г-4|Маржа по жанрам (тепловая карта)|Диаграмма (heatmap)|mart_sales_trends: genre, month_start, gross_margin|Rows=genre, Cols=month, Color=gross_margin. В Tableau|Д|

### Блок Д. Остатки и управление запасами

*Источник: 03_mart_inventory_alerts.

|**#**|**Название**|**Тип**|**Поля**|**Формула в Excel / примечание**|**Куда**|
|---|---|---|---|---|---|
|Д-1|Low-stock виджет|Таблица|mart_inventory_alerts: title, format, closing_stock, days_until_stockout, recommended_order_qty, alert_level|Условное форматирование по alert_level: critical=красный, urgent=оранжевый, warning=жёлтый|ОТ, УП|
|Д-2|Число товаров по уровню тревоги|KPI (3 числа)|mart_inventory_alerts: COUNT по alert_level|=COUNTIF([alert_level],"critical") и т.д.|ОТ, УП|
|Д-3|Дней до нуля — распределение|График (bar)|mart_inventory_alerts: days_until_stockout|Гистограмма. Бакеты: 0, 1–7, 8–30, 30+|УП|
|Д-4|Рекомендованный заказ (сумма)|KPI|mart_inventory_alerts: SUM(recommended_order_qty × price_rub)|JOIN с dim_product по product_id для цены|УП|
|Д-5|Таблица к заказу|Таблица|mart_inventory_alerts: все поля + publisher для контакта|Сортировка: closing_stock ASC. Экспорт в reorder_tracker|УП|
|Д-6|Динамика остатков по товару|График (линия)|fact_inventory: date, closing_stock по product_id|Фильтр по конкретному product_id. Линия порога 15|УП|

### Блок Е. ABC-анализ ассортимента

*Источник: 05_mart_abc.*

|**#**|**Название**|**Тип**|**Поля**|**Формула в Excel / примечание**|**Куда**|
|---|---|---|---|---|---|
|Е-1|Число SKU по ABC-классам|KPI (3 числа)|mart_abc: COUNT по abc_class|=COUNTIF([abc_class],"A")|Д|
|Е-2|Доля выручки по классам|Диаграмма (pie/donut)|mart_abc: abc_class, SUM(total_net_revenue)|Три сегмента. Подписи: % от итога|Д|
|Е-3|ABC-таблица с маржой|Таблица со спарклайнами|mart_abc: title, format, genre, abc_class, total_net_revenue, product_margin, revenue_rank|Условное форматирование abc_class. Спарклайн — потребует доп. таблицу с историей|Д|
|Е-4|Маржа vs ABC-класс (box plot)|Диаграмма|mart_abc: abc_class, product_margin|В Tableau: распределение маржи внутри каждого класса|Д|
|Е-5|Потерянные продажи по ABC|График (bar)|mart_abc: abc_class, SUM(total_lost_qty)|Показывает, у каких товаров дефицит влияет на выручку сильнее всего|Д|

### Блок Ж. Топ-5 товаров

*Источник: mart_daily_top5.*

|**#**|**Название**|**Тип**|**Поля**|**Формула в Excel / примечание**|**Куда**|
|---|---|---|---|---|---|
|Ж-1|Топ-5 товаров дня|Таблица|mart_daily_top5: rnk, title, format, revenue|5 строк. Мини-бар выручки через условное форматирование (Data Bars)|ОТ|

### Блок З. Подписки

*Источник: mart_subscriptions_monthly, 07_mart_subscriptions.*

|**#**|**Название**|**Тип**|**Поля**|**Формула в Excel / примечание**|**Куда**|
|---|---|---|---|---|---|
|З-1|Динамика подписок по месяцам|График (grouped bar)|mart_subscriptions_monthly: month_start, subscription_type, net_qty, cancellations|Два бара: новые / отмены. Группировка по типу подписки|Д|
|З-2|Выручка подписок (месячно)|График (линия)|mart_subscriptions_monthly: month_start, net_revenue|Отдельно Monthly и Annual|Д|
|З-3|Сводная карточка подписок|KPI + таблица|mart_subscriptions_summary: subscription_type, net_subscriptions, total_revenue, cancellation_rate_pct, avg_ltv, margin|Две строки (Monthly / Annual). Выделить margin и avg_ltv|Д|
|З-4|Доля промо в подписках|KPI + график|mart_subscriptions_monthly: promo_pct по месяцам|Линейный график тренда доли промо|Д|
