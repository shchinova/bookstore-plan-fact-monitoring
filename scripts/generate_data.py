"""
Скрипт генерации синтетических данных для книжного онлайн-магазина
(аналог ЛитРес) для план-фактного анализа.

Период:      2024-01-01 – 2026-04-15
Выходные CSV:
    справочники: dim_date, dim_product,
    планы: fact_plan,
    факты:
        fact_sales_history,       # 01.01.2024 — 14.04.2026
        fact_sales_update,        # 15.04.2026
        fact_inventory_history,   # 01.01.2024 — 14.04.2026
        fact_inventory_update.    # 15.04.2026
Папка вывода: data/raw/

Запуск:
    python generate_bookstore_data.py

При запуске скрипт спросит: "Генерировать с аномалиями? (y/n)"
"""

import os
import warnings
import random
from datetime import datetime

import numpy as np
import pandas as pd
from faker import Faker

warnings.filterwarnings('ignore')

# ─────────────────────────────────────────────
# Интерактивный выбор режима
# ─────────────────────────────────────────────
def ask_user_choice() -> bool:
    """Запрашивает у пользователя, нужно ли генерировать аномалии."""
    while True:
        answer = input("\n🔧 Генерировать данные с аномалиями? (y/n): ").strip().lower()
        if answer in ('y', 'yes', 'да'):
            return True
        if answer in ('n', 'no', 'нет'):
            return False
        print("Пожалуйста, ответьте 'y' или 'n'.")

anomalies_enabled = ask_user_choice()

# ─────────────────────────────────────────────
# Воспроизводимость
# ─────────────────────────────────────────────
SEED = 42
random.seed(SEED)
np.random.seed(SEED)
fake = Faker('ru_RU')
Faker.seed(SEED)

# ─────────────────────────────────────────────
# Выходная директория
# ─────────────────────────────────────────────
OUTPUT_DIR = os.path.join('data', 'raw')
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─────────────────────────────────────────────
# Константы
# ─────────────────────────────────────────────
START_DATE = '2024-01-01'
END_DATE   = '2026-04-15'
DATE_RANGE = pd.date_range(start=START_DATE, end=END_DATE, freq='D')

AVG_ORDERS_PER_DAY  = 250
AVG_BASKET_SIZE     = 1.2
PROMO_PROB          = 0.12
PROMO_BOOST_LOW     = 1.3
PROMO_BOOST_HIGH    = 1.8
RETURN_PROB         = 0.02
MONTHLY_TREND       = 0.009
LOW_STOCK_THRESHOLD = 15

SEASONALITY = {
    1: 0.8, 2: 1.0, 3: 1.0, 4: 1.0,  5: 1.0,  6: 1.0,
    7: 0.6, 8: 1.0, 9: 1.3, 10: 1.0, 11: 1.0, 12: 1.8,
}

GENRES = [
    'Fiction', 'Fantasy', 'Mystery', 'Romance', 'Science',
    'Biography', 'Business', 'Children', 'History', 'Poetry',
]

FORMATS_BOOKS = {
    'eBook':     350,
    'Paperback': 300,
    'Hardcover': 150,
    'Audiobook': 150,
}

SUBSCRIPTIONS = [
    {'title': 'Monthly Subscription', 'format': 'Subscription', 'price': 399,  'cost': 399  * 0.15},
    {'title': 'Annual Subscription',  'format': 'Subscription', 'price': 3990, 'cost': 3990 * 0.15},
]

PUBLISHERS = [
    'Эксмо', 'АСТ', 'Альпина Паблишер', 'Манн, Иванов и Фербер',
    'Питер', 'Самокат', 'Росмэн', 'Просвещение', 'Corpus', 'НЛО',
    'Азбука', 'Детская литература', 'РИПОЛ классик', 'Вагриус', 'Амфора',
]

LANGUAGES  = ['Russian', 'English', 'Other']
LANG_PROBS = [0.80, 0.15, 0.05]

CHANNELS      = ['Web', 'Mobile App', 'API Partner']
CHANNEL_PROBS = [0.55, 0.35, 0.10]

PROMO_CODES = ['WINTER24', 'BACK2SCHOOL', 'BOOK15', 'FAVORITE10', 'NEWYEAR25']

MARGIN_RATIOS = {
    'eBook':        0.20,
    'Paperback':    0.60,
    'Hardcover':    0.55,
    'Audiobook':    0.35,
    'Subscription': 0.15,
}


# ═══════════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ УТИЛИТЫ
# ═══════════════════════════════════════════════════════════════════

def _sample_idx(df: pd.DataFrame, frac: float) -> np.ndarray:
    """Возвращает массив случайных индексов (позиций iloc) размером frac*len(df), минимум 1."""
    n = max(1, int(round(len(df) * frac)))
    return np.random.choice(len(df), size=n, replace=False)


def _safe_delete(df: pd.DataFrame, mask: pd.Series, label: str) -> pd.DataFrame:
    """
    Удаляет строки по маске.
    Если после удаления остаётся < 50 записей — возвращает исходный DF с WARNING.
    """
    result = df[~mask].copy()
    if len(result) < 50:
        print(f'  WARNING [{label}]: после удаления осталось {len(result)} строк (<50). Откат.')
        return df.copy()
    return result


# ═══════════════════════════════════════════════════════════════════
# 1. dim_date
# ═══════════════════════════════════════════════════════════════════
def generate_dim_date() -> pd.DataFrame:
    """Календарь с праздниками и признаками."""
    df = pd.DataFrame({'date': DATE_RANGE})
    df['year']       = df['date'].dt.year
    df['month']      = df['date'].dt.month
    df['quarter']    = df['date'].dt.quarter
    df['is_weekend'] = (df['date'].dt.weekday >= 5).astype(int)

    holiday_days = {
        (1, 1), (1, 2), (1, 3), (1, 4), (1, 5),
        (1, 6), (1, 7), (1, 8),
        (2, 23), (3, 8), (5, 1), (5, 9),
    }
    df['is_holiday_ru'] = df.apply(
        lambda r: 1 if (r['date'].month, r['date'].day) in holiday_days else 0,
        axis=1,
    )
    df['date'] = df['date'].dt.date
    return df


# ═══════════════════════════════════════════════════════════════════
# 2. dim_product
# ═══════════════════════════════════════════════════════════════════
def generate_dim_product() -> pd.DataFrame:
    """Справочник товаров (~952 записи)."""
    products = []
    product_id = 1

    for fmt, count in FORMATS_BOOKS.items():
        for _ in range(count):
            genre = np.random.choice(GENRES)

            if fmt == 'eBook':
                price      = round(np.random.uniform(149, 599))
                page_count = None
            elif fmt == 'Paperback':
                price      = round(np.random.uniform(299, 1200))
                page_count = int(np.random.randint(150, 801))
            elif fmt == 'Hardcover':
                price      = round(np.random.uniform(599, 2500))
                page_count = int(np.random.randint(150, 801))
            else:  # Audiobook
                price      = round(np.random.uniform(199, 799))
                page_count = None

            cost = round(price * MARGIN_RATIOS[fmt], 2)

            if np.random.random() < 0.2:
                avg_rating = round(float(np.clip(np.random.normal(4.7, 0.2), 4.5, 5.0)), 1)
            else:
                avg_rating = round(float(np.clip(np.random.normal(4.0, 0.5), 3.2, 4.49)), 1)

            review_count = int(min(np.random.lognormal(mean=4.8, sigma=0.8), 5000))

            if np.random.random() < 0.3:
                pub_year = int(np.random.choice(range(2022, 2026)))
            else:
                pub_year = int(np.random.randint(1960, 2022))
            pub_date = fake.date_between(
                start_date=datetime(pub_year, 1, 1),
                end_date=datetime(pub_year, 12, 28),
            )

            stock_initial = None
            if fmt in ('Paperback', 'Hardcover'):
                stock_initial = int(np.random.randint(100, 3001))

            language  = np.random.choice(LANGUAGES, p=LANG_PROBS)
            author    = fake.name()
            title     = fake.sentence(nb_words=3).rstrip('.') + f' ({genre})'
            publisher = np.random.choice(PUBLISHERS)

            products.append({
                'product_id':     product_id,
                'isbn':           fake.isbn13(),
                'title':          title,
                'author':         author,
                'publisher':      publisher,
                'published_date': pub_date,
                'language':       language,
                'page_count':     page_count,
                'genre':          genre,
                'format':         fmt,
                'price_rub':      float(price),
                'cost_rub':       cost,
                'avg_rating':     avg_rating,
                'review_count':   review_count,
                'stock_initial':  stock_initial,
            })
            product_id += 1

    for sub in SUBSCRIPTIONS:
        products.append({
            'product_id':     product_id,
            'isbn':           None,
            'title':          sub['title'],
            'author':         None,
            'publisher':      None,
            'published_date': None,
            'language':       None,
            'page_count':     None,
            'genre':          'Subscription',
            'format':         'Subscription',
            'price_rub':      float(sub['price']),
            'cost_rub':       round(sub['cost'], 2),
            'avg_rating':     4.8,
            'review_count':   int(np.random.randint(500, 3001)),
            'stock_initial':  None,
        })
        product_id += 1

    df = pd.DataFrame(products)
    df['is_physical'] = df['format'].isin(['Paperback', 'Hardcover'])
    return df


# ═══════════════════════════════════════════════════════════════════
# 3. Веса продуктов
# ═══════════════════════════════════════════════════════════════════
def compute_daily_weights(dim_product: pd.DataFrame, date) -> np.ndarray:
    """Нормированные веса для выбора продуктов на заданную дату."""
    n      = len(dim_product)
    ranks  = np.arange(1, n + 1)
    alpha  = -np.log(0.2) / np.log(0.8)
    zipf_w = np.exp(-alpha * np.log(ranks))

    month        = pd.Timestamp(date).month
    genre_factor = np.ones(n)
    if month == 9:
        genre_factor[dim_product['genre'].isin(['Science', 'Biography', 'Children']).values] = 1.5
    if month == 12:
        genre_factor[dim_product['format'].isin(['Hardcover', 'Audiobook']).values] = 1.8

    novelty_factor = np.ones(n)
    pub_dates  = pd.to_datetime(dim_product['published_date'])
    today_ts   = pd.Timestamp(date)
    days_since = (today_ts - pub_dates).dt.days.values
    is_new = np.where(
        np.isfinite(days_since.astype(float)),
        (days_since >= 0) & (days_since <= 90),
        False,
    )
    novelty_factor[is_new] = 1.5

    rating_factor = np.ones(n)
    rating_factor[dim_product['avg_rating'].values >= 4.5] = 1.3

    final_w  = zipf_w * genre_factor * novelty_factor * rating_factor
    final_w /= final_w.sum()
    return final_w


# ═══════════════════════════════════════════════════════════════════
# 4. fact_sales
# ═══════════════════════════════════════════════════════════════════
def generate_fact_sales(dim_date: pd.DataFrame, dim_product: pd.DataFrame) -> pd.DataFrame:
    """Генерирует таблицу фактов продаж."""
    dates           = pd.to_datetime(dim_date['date']).tolist()
    product_ids     = dim_product['product_id'].values
    formats_arr     = dim_product['format'].values
    prices_arr      = dim_product['price_rub'].values
    is_physical_arr = dim_product['is_physical'].values
    idx_range       = np.arange(len(dim_product))

    sales_rows = []
    sales_id   = 1
    order_id   = 1

    for ts in dates:
        date_val          = ts.date()
        months_from_start = (ts.year - 2024) * 12 + (ts.month - 1)
        trend_factor      = (1 + MONTHLY_TREND) ** months_from_start
        season_factor     = SEASONALITY.get(ts.month, 1.0)
        target_orders     = AVG_ORDERS_PER_DAY * trend_factor * season_factor

        n_orders = int(np.random.poisson(target_orders))
        if n_orders == 0:
            order_id += 1
            continue

        weights = compute_daily_weights(dim_product, ts)  # один раз на день

        for _ in range(n_orders):
            basket_size    = max(1, int(np.random.poisson(AVG_BASKET_SIZE)))
            chosen_indices = np.random.choice(idx_range, size=basket_size, p=weights)

            for ci in chosen_indices:
                fmt        = formats_arr[ci]
                base_price = prices_arr[ci]
                is_phys    = is_physical_arr[ci]

                is_promo     = 0
                discount_pct = 0
                promo_code   = None
                if fmt != 'Subscription' and np.random.random() < PROMO_PROB:
                    is_promo     = 1
                    discount_pct = int(np.random.randint(5, 36))
                    promo_code   = str(np.random.choice(PROMO_CODES))

                unit_price = round(base_price * (1 - discount_pct / 100.0), 2)

                base_qty  = int(np.random.choice([1, 2, 3], p=[0.7, 0.2, 0.1]))
                sales_qty = base_qty
                if is_promo:
                    boost     = np.random.uniform(PROMO_BOOST_LOW, PROMO_BOOST_HIGH)
                    sales_qty = max(1, int(round(base_qty * boost)))

                channel = str(np.random.choice(CHANNELS, p=CHANNEL_PROBS))

                return_qty = 0
                if is_phys and np.random.random() < RETURN_PROB:
                    return_qty = int(np.random.randint(1, sales_qty + 1))

                sales_rows.append({
                    'sales_id':         sales_id,
                    'order_id':         order_id,
                    'product_id':       int(product_ids[ci]),
                    'date':             date_val,
                    'sales_qty':        sales_qty,
                    'return_qty':       return_qty,
                    'unit_price':       unit_price,
                    'sales_amount':     round(sales_qty * unit_price, 2),
                    'return_amount':    round(return_qty * unit_price, 2),
                    'discount_percent': discount_pct,
                    'is_promo':         is_promo,
                    'promo_code':       promo_code,
                    'channel':          channel,
                    'lost_sales_qty':   0,
                })
                sales_id += 1
            order_id += 1

    return pd.DataFrame(sales_rows)


# ═══════════════════════════════════════════════════════════════════
# 5. Постпроцессинг продаж (дефицит физических книг)
# ═══════════════════════════════════════════════════════════════════
def postprocess_sales_for_inventory(
    fact_sales: pd.DataFrame,
    dim_product: pd.DataFrame,
) -> pd.DataFrame:
    """Корректирует sales_qty при дефиците остатка для физических книг."""
    df = fact_sales.copy()
    df['date'] = pd.to_datetime(df['date'])

    physical_ids = dim_product.loc[dim_product['is_physical'], 'product_id'].tolist()
    phys_mask    = df['product_id'].isin(physical_ids)

    daily_sales = (
        df[phys_mask]
        .groupby(['product_id', 'date'], as_index=False)
        .agg(total_sales=('sales_qty', 'sum'), total_returns=('return_qty', 'sum'))
    )
    daily_sales['net_sold'] = daily_sales['total_sales'] - daily_sales['total_returns']

    for prod_id in physical_ids:
        prod_info = dim_product.loc[dim_product['product_id'] == prod_id].iloc[0]
        stock     = int(prod_info['stock_initial'] or 0)

        prod_daily         = daily_sales[daily_sales['product_id'] == prod_id].sort_values('date')
        days_since_replen  = 0
        replen_interval    = int(np.random.randint(30, 91))

        for _, row in prod_daily.iterrows():
            days_since_replen += 1
            if days_since_replen >= replen_interval:
                stock            += int(np.random.randint(50, 501))
                days_since_replen = 0
                replen_interval   = int(np.random.randint(30, 91))

            net = int(row['net_sold'])
            if net > stock:
                lost            = net - stock
                new_total_sales = int(row['total_sales']) - lost
                mask_day        = phys_mask & (df['product_id'] == prod_id) & (df['date'] == row['date'])
                total_before    = df.loc[mask_day, 'sales_qty'].sum()

                if total_before > 0:
                    ratio = new_total_sales / total_before
                    for idx_i in df.index[mask_day]:
                        old_qty = int(df.at[idx_i, 'sales_qty'])
                        new_qty = max(0, int(round(old_qty * ratio)))
                        diff    = old_qty - new_qty
                        df.at[idx_i, 'sales_qty']      = new_qty
                        df.at[idx_i, 'sales_amount']   = round(new_qty * df.at[idx_i, 'unit_price'], 2)
                        if df.at[idx_i, 'return_qty'] > new_qty:
                            df.at[idx_i, 'return_qty']    = new_qty
                            df.at[idx_i, 'return_amount'] = round(new_qty * df.at[idx_i, 'unit_price'], 2)
                        df.at[idx_i, 'lost_sales_qty'] += diff
                stock = 0
            else:
                stock = max(0, stock - net)

    df['lost_sales_qty'] = df['lost_sales_qty'].fillna(0).astype(int)

    exceed_mask = df['return_qty'] > df['sales_qty']
    df.loc[exceed_mask, 'return_qty']    = df.loc[exceed_mask, 'sales_qty']
    df.loc[exceed_mask, 'return_amount'] = (
        df.loc[exceed_mask, 'return_qty'] * df.loc[exceed_mask, 'unit_price']
    ).round(2)

    return df


# ═══════════════════════════════════════════════════════════════════
# 6. fact_plan
# ═══════════════════════════════════════════════════════════════════
def generate_fact_plan(
    fact_sales: pd.DataFrame,
    dim_product: pd.DataFrame,
) -> pd.DataFrame:
    """Помесячные планы для комбинаций (genre × format × year × month)."""
    genres_plan  = GENRES
    formats_plan = ['eBook', 'Paperback', 'Hardcover', 'Audiobook']

    end_dt = pd.to_datetime(END_DATE)
    plan_periods = [
        (year, month)
        for year in range(2024, end_dt.year + 1)
        for month in range(1, 13)
        if pd.Timestamp(year, month, 1) <= end_dt.replace(day=1)
    ]

    tmp               = fact_sales.merge(dim_product[['product_id', 'genre', 'format']], on='product_id')
    tmp['date']       = pd.to_datetime(tmp['date'])
    tmp['year']       = tmp['date'].dt.year
    tmp['month']      = tmp['date'].dt.month
    tmp['net_amount'] = tmp['sales_amount'] - tmp['return_amount']
    tmp['net_qty']    = tmp['sales_qty']    - tmp['return_qty']

    grouped = tmp.groupby(['year', 'month', 'genre', 'format']).agg(
        fact_qty    = ('net_qty',    'sum'),
        fact_amount = ('net_amount', 'sum'),
    ).reset_index()

    combos = pd.DataFrame(
        [(y, m, g, f)
         for y, m in plan_periods
         for g in genres_plan
         for f in formats_plan],
        columns=['year', 'month', 'genre', 'format'],
    )
    grid = combos.merge(grouped, on=['year', 'month', 'genre', 'format'], how='left')
    grid[['fact_qty', 'fact_amount']] = grid[['fact_qty', 'fact_amount']].fillna(0)

    plan_rows = []
    plan_id   = 1

    for _, row in grid.iterrows():
        year, month, genre, fmt = int(row['year']), int(row['month']), row['genre'], row['format']

        if year == 2024:
            factor      = np.random.uniform(0.9, 1.1)
            plan_qty    = max(0, int(round(row['fact_qty']   * factor)))
            plan_amount = max(0.0, round(row['fact_amount']  * factor, 2))
        else:
            prev = grid[
                (grid['year']   == year - 1) &
                (grid['month']  == month)    &
                (grid['genre']  == genre)    &
                (grid['format'] == fmt)
            ]
            if not prev.empty and prev.iloc[0]['fact_qty'] > 0:
                factor      = np.random.uniform(0.92, 1.20)
                plan_qty    = max(0, int(round(prev.iloc[0]['fact_qty']   * factor)))
                plan_amount = max(0.0, round(prev.iloc[0]['fact_amount']  * factor, 2))
            else:
                plan_qty    = 0
                plan_amount = 0.0

        base_margin = 1 - MARGIN_RATIOS.get(fmt, 0.5)
        plan_margin = round(base_margin + np.random.uniform(0.01, 0.02), 4)

        plan_rows.append({
            'plan_id':            plan_id,
            'genre':              genre,
            'format':             fmt,
            'year':               year,
            'month':              month,
            'plan_qty':           plan_qty,
            'plan_amount':        plan_amount,
            'plan_margin_target': plan_margin,
        })
        plan_id += 1

    return pd.DataFrame(plan_rows)


# ═══════════════════════════════════════════════════════════════════
# 7. fact_inventory
# ═══════════════════════════════════════════════════════════════════
def generate_fact_inventory(
    fact_sales: pd.DataFrame,
    dim_product: pd.DataFrame,
) -> pd.DataFrame:
    """Ежедневные остатки для Paperback и Hardcover."""
    physical_prods = dim_product[dim_product['is_physical']].copy()

    sales_phys             = fact_sales[fact_sales['product_id'].isin(physical_prods['product_id'])].copy()
    sales_phys['date']     = pd.to_datetime(sales_phys['date'])
    sales_phys['net_sold'] = sales_phys['sales_qty'] - sales_phys['return_qty']

    daily_net = (
        sales_phys.groupby(['product_id', 'date'])['net_sold']
        .sum()
        .reset_index()
        .rename(columns={'net_sold': 'net_sold_day'})
    )

    date_range     = pd.date_range(START_DATE, END_DATE, freq='D')
    inventory_rows = []
    inv_id         = 1

    for _, prod_row in physical_prods.iterrows():
        prod_id    = int(prod_row['product_id'])
        stock      = int(prod_row['stock_initial'] or 0)
        prod_daily = (
            daily_net[daily_net['product_id'] == prod_id]
            .set_index('date')['net_sold_day']
        )

        days_since_replen = 0
        replen_interval   = int(np.random.randint(30, 91))

        for dt in date_range:
            opening = stock
            sold    = int(prod_daily.get(dt, 0))

            replenishment     = 0
            days_since_replen += 1
            if days_since_replen >= replen_interval:
                replenishment     = int(np.random.randint(50, 501))
                days_since_replen = 0
                replen_interval   = int(np.random.randint(30, 91))

            closing = max(0, opening - sold + replenishment)
            is_low  = 1 if closing < LOW_STOCK_THRESHOLD else 0

            inventory_rows.append({
                'inventory_id':      inv_id,
                'product_id':        prod_id,
                'date':              dt.date(),
                'opening_stock':     opening,
                'sold_qty':          max(0, sold),
                'replenishment_qty': replenishment,
                'closing_stock':     closing,
                'is_low_stock':      is_low,
            })
            inv_id += 1
            stock   = closing

    return pd.DataFrame(inventory_rows)


# ═══════════════════════════════════════════════════════════════════
# 8. АНОМАЛИИ
# ═══════════════════════════════════════════════════════════════════

def apply_dim_product_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """
    Аномалии для dim_product.
    Порядок: №1→2→3→4→5→6→7→23→8(дубли — в самом конце).
    """
    out = df.copy()
    n   = len(out)

    # №1: author = NaN (1% строк)
    idx = _sample_idx(out, 0.01)
    out.iloc[idx, out.columns.get_loc('author')] = np.nan
    print(f'    Аномалия №1  (author=NaN):          {len(idx)} строк')

    # №2: publisher = NaN (1% строк)
    idx = _sample_idx(out, 0.01)
    out.iloc[idx, out.columns.get_loc('publisher')] = np.nan
    print(f'    Аномалия №2  (publisher=NaN):        {len(idx)} строк')

    # №3: page_count = NaN для Paperback/Hardcover (2% таких строк)
    phys_mask = out['format'].isin(['Paperback', 'Hardcover'])
    phys_idx  = np.where(phys_mask)[0]
    if len(phys_idx) > 0:
        n_anom = max(1, int(round(len(phys_idx) * 0.02)))
        chosen = np.random.choice(phys_idx, size=n_anom, replace=False)
        out.iloc[chosen, out.columns.get_loc('page_count')] = np.nan
        print(f'    Аномалия №3  (page_count=NaN):       {n_anom} строк')

    # №4: isbn = 'INVALID_ISBN' (1% строк)
    idx = _sample_idx(out, 0.01)
    out.iloc[idx, out.columns.get_loc('isbn')] = 'INVALID_ISBN'
    print(f'    Аномалия №4  (INVALID_ISBN):         {len(idx)} строк')

    # №5: price_rub < 0 (0.1% строк)
    idx = _sample_idx(out, 0.001)
    out.iloc[idx, out.columns.get_loc('price_rub')] = \
        -out.iloc[idx]['price_rub'].abs().values
    print(f'    Аномалия №5  (price_rub<0):          {len(idx)} строк')

    # №6: cost_rub < 0 (0.1% строк)
    idx = _sample_idx(out, 0.001)
    out.iloc[idx, out.columns.get_loc('cost_rub')] = \
        -out.iloc[idx]['cost_rub'].abs().values
    print(f'    Аномалия №6  (cost_rub<0):           {len(idx)} строк')

    # №7: stock_initial у eBook/Audiobook (1% таких строк)
    dig_mask = out['format'].isin(['eBook', 'Audiobook'])
    dig_idx  = np.where(dig_mask)[0]
    if len(dig_idx) > 0:
        n_anom = max(1, int(round(len(dig_idx) * 0.01)))
        chosen = np.random.choice(dig_idx, size=n_anom, replace=False)
        out.iloc[chosen, out.columns.get_loc('stock_initial')] = \
            np.random.randint(1, 1001, size=n_anom)
        print(f'    Аномалия №7  (stock_initial цифр.):  {n_anom} строк')

    # №23: пустая строка в случайном поле author/publisher/genre (0.2% строк)
    idx    = _sample_idx(out, 0.002)
    fields = ['author', 'publisher', 'genre']
    for i in idx:
        col = np.random.choice(fields)
        out.iloc[i, out.columns.get_loc(col)] = ''
    print(f'    Аномалия №23 (пустая строка):        {len(idx)} строк')

    # №8: дубликаты строк (0.3% строк) — В САМОМ КОНЦЕ
    idx        = _sample_idx(out, 0.003)
    dupes      = out.iloc[idx].copy()
    max_id     = out['product_id'].max()
    dupes['product_id'] = range(max_id + 1, max_id + 1 + len(dupes))
    out        = pd.concat([out, dupes], ignore_index=True)
    print(f'    Аномалия №8  (дубликаты):            {len(dupes)} строк')

    return out


def apply_fact_sales_anomalies(
    df: pd.DataFrame,
    dim_product: pd.DataFrame,
) -> pd.DataFrame:
    """
    Аномалии для fact_sales.
    Порядок: №11→12→13→9→10→14
    (сначала модификации, потом удаления, потом дубли).
    """
    out = df.copy()

    # Маска физических товаров (нужна для №12)
    phys_ids  = set(dim_product.loc[dim_product['is_physical'], 'product_id'])
    sub_ids   = set(dim_product.loc[dim_product['format'] == 'Subscription', 'product_id'])

    # №11: некорректный sales_amount (0.2% строк)
    idx = _sample_idx(out, 0.002)
    multipliers = np.random.uniform(0.5, 1.5, size=len(idx))
    out.iloc[idx, out.columns.get_loc('sales_amount')] = \
        (out.iloc[idx]['sales_amount'].values * multipliers).round(2)
    print(f'    Аномалия №11 (sales_amount×rand):    {len(idx)} строк')

    # №12: return_qty = sales_qty + 1 для физических товаров (0.5% таких строк)
    phys_mask = out['product_id'].isin(phys_ids)
    phys_idx  = np.where(phys_mask)[0]
    if len(phys_idx) > 0:
        n_anom = max(1, int(round(len(phys_idx) * 0.005)))
        chosen = np.random.choice(phys_idx, size=n_anom, replace=False)
        for i in chosen:
            sq = int(out.iloc[i]['sales_qty'])
            rq = sq + 1
            out.iloc[i, out.columns.get_loc('return_qty')]    = rq
            out.iloc[i, out.columns.get_loc('return_amount')] = round(
                rq * out.iloc[i]['unit_price'], 2
            )
        print(f'    Аномалия №12 (return_qty>sales_qty): {n_anom} строк')

    # №13: is_promo=1, discount=20 для подписок (0.5% строк-подписок)
    sub_mask = out['product_id'].isin(sub_ids)
    sub_idx  = np.where(sub_mask)[0]
    if len(sub_idx) > 0:
        n_anom = max(1, int(round(len(sub_idx) * 0.005)))
        chosen = np.random.choice(sub_idx, size=n_anom, replace=False)
        out.iloc[chosen, out.columns.get_loc('is_promo')]         = 1
        out.iloc[chosen, out.columns.get_loc('discount_percent')] = 20
        print(f'    Аномалия №13 (промо на подписку):    {n_anom} строк')

    # №9: удалить 5% уникальных order_id целиком
    all_orders    = out['order_id'].unique()
    n_drop        = max(1, int(round(len(all_orders) * 0.05)))
    drop_orders   = np.random.choice(all_orders, size=n_drop, replace=False)
    drop_mask     = out['order_id'].isin(drop_orders)
    rows_before   = len(out)
    out           = _safe_delete(out, drop_mask, 'Аномалия №9')
    print(f'    Аномалия №9  (удал. order_id):       -{rows_before - len(out)} строк '
          f'({n_drop} заказов)')

    # №10: удалить все записи за 1% случайных дат
    all_dates   = out['date'].unique()
    n_drop_d    = max(1, int(round(len(all_dates) * 0.01)))
    drop_dates  = np.random.choice(all_dates, size=n_drop_d, replace=False)
    date_mask   = out['date'].isin(drop_dates)
    rows_before = len(out)
    out         = _safe_delete(out, date_mask, 'Аномалия №10')
    print(f'    Аномалия №10 (удал. дни):            -{rows_before - len(out)} строк '
          f'({n_drop_d} дней)')

    # №14: дублировать 0.5% строк с новым sales_id
    idx         = _sample_idx(out, 0.005)
    dupes       = out.iloc[idx].copy()
    max_sid     = out['sales_id'].max()
    dupes['sales_id'] = range(max_sid + 1, max_sid + 1 + len(dupes))
    out         = pd.concat([out, dupes], ignore_index=True)
    print(f'    Аномалия №14 (дубл. позиций):        +{len(dupes)} строк')

    return out


def apply_fact_plan_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Аномалии для fact_plan: №17 и №18."""
    out = df.copy()

    # №17: plan_qty < 0 (0.5% строк)
    idx = _sample_idx(out, 0.005)
    out.iloc[idx, out.columns.get_loc('plan_qty')] = \
        -out.iloc[idx]['plan_qty'].abs().values
    print(f'    Аномалия №17 (plan_qty<0):           {len(idx)} строк')

    # №18: plan_amount < 0 (0.5% строк)
    idx = _sample_idx(out, 0.005)
    out.iloc[idx, out.columns.get_loc('plan_amount')] = \
        -out.iloc[idx]['plan_amount'].abs().values
    print(f'    Аномалия №18 (plan_amount<0):        {len(idx)} строк')

    return out


def apply_fact_inventory_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """Аномалии для fact_inventory: №19 и №20."""
    out = df.copy()

    # №19: closing_stock = -5 (0.2% строк)
    idx = _sample_idx(out, 0.002)
    out.iloc[idx, out.columns.get_loc('closing_stock')] = -5
    print(f'    Аномалия №19 (closing_stock=-5):     {len(idx)} строк')

    # №20: инверсия is_low_stock (0.5% строк)
    idx = _sample_idx(out, 0.005)
    out.iloc[idx, out.columns.get_loc('is_low_stock')] = \
        1 - out.iloc[idx]['is_low_stock'].values
    print(f'    Аномалия №20 (инверсия is_low_stock):{len(idx)} строк')

    return out

def split_and_save_fact(df: pd.DataFrame, base_name: str, date_col: str = 'date'):
    """Разбивает фактовую таблицу на history и update по дате 15.04.2026."""
    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    
    # Граничная дата: все, что до 15.04.2026 (не включая) - в history
    # 15.04.2026 и позже - в update
    cutoff_date = pd.Timestamp('2026-04-15')
    
    # History: с 01.01.2024 до 14.04.2026 включительно
    mask_history = df[date_col] < cutoff_date
    if mask_history.any():
        path = os.path.join(OUTPUT_DIR, f'{base_name}_history.csv')
        df.loc[mask_history].to_csv(path, index=False, encoding='utf-8-sig')
        print(f'  ✓ {base_name}_history.csv — {mask_history.sum():,} строк')
    
    # Update: 15.04.2026
    mask_update = df[date_col] == cutoff_date
    if mask_update.any():
        path = os.path.join(OUTPUT_DIR, f'{base_name}_update.csv')
        df.loc[mask_update].to_csv(path, index=False, encoding='utf-8-sig')
        print(f'  ✓ {base_name}_update.csv — {mask_update.sum():,} строк')
    else:
        # Если нет данных за 15.04.2026, создаём пустой файл
        path = os.path.join(OUTPUT_DIR, f'{base_name}_update.csv')
        pd.DataFrame(columns=df.columns).to_csv(path, index=False, encoding='utf-8-sig')
        print(f'  ⚠ {base_name}_update.csv — пустой (нет данных за 2026-04-15)')

# ═══════════════════════════════════════════════════════════════════
# 9. Главная функция
# ═══════════════════════════════════════════════════════════════════
def main():
    def save(df: pd.DataFrame, name: str):
        path = os.path.join(OUTPUT_DIR, f'{name}.csv')
        df.to_csv(path, index=False, encoding='utf-8-sig')
        print(f'  ✓ {name}.csv — {len(df):,} строк → {path}')

    # ── 1. Генерация чистых данных ────────────────────────────────
    print('\n[1/5] Генерация dim_date...')
    dim_date = generate_dim_date()

    print('[2/5] Генерация dim_product...')
    dim_product = generate_dim_product()

    print('[3/5] Генерация fact_sales (несколько минут)...')
    fact_sales = generate_fact_sales(dim_date, dim_product)
    print(f'       → исходно {len(fact_sales):,} строк')

    print('       Постобработка дефицита...')
    fact_sales = postprocess_sales_for_inventory(fact_sales, dim_product)

    print('[4/5] Генерация fact_plan...')
    fact_plan = generate_fact_plan(fact_sales, dim_product)

    print('[5/5] Генерация fact_inventory...')
    fact_inventory = generate_fact_inventory(fact_sales, dim_product)

    # ── 2. Применение аномалий ────────────────────────────────────
    if anomalies_enabled:
        print('\n  Внесение аномалий...')
        print('  dim_product:')
        dim_product    = apply_dim_product_anomalies(dim_product)
        print('  fact_sales:')
        fact_sales = apply_fact_sales_anomalies(fact_sales, dim_product)
        print('  fact_plan:')
        fact_plan      = apply_fact_plan_anomalies(fact_plan)
        print('  fact_inventory:')
        fact_inventory = apply_fact_inventory_anomalies(fact_inventory)
    else:
        print('\n  [режим БЕЗ аномалий]')

    # ── 3. Сохранение ─────────────────────────────────────────────
    print('\nСохранение файлов...')

    # Справочники и план – целиком
    save(dim_date, 'dim_date')
    save(dim_product, 'dim_product')
    save(fact_plan, 'fact_plan')

    # Факты продаж – с разбиением
    print('\n  Разбиение fact_sales по периодам:')
    split_and_save_fact(fact_sales, 'sales', date_col='date')

    # Факты остатков – с разбиением
    print('\n  Разбиение fact_inventory по периодам:')
    split_and_save_fact(fact_inventory, 'inventory', date_col='date')

    # ── 4. Автоматические проверки ───────────────────────────────
    print('\nАвтоматические проверки (структурные)...')

    fact_sales_dt     = fact_sales.copy()
    fact_sales_dt['date'] = pd.to_datetime(fact_sales_dt['date'])
    fact_inventory_dt = fact_inventory.copy()
    fact_inventory_dt['date'] = pd.to_datetime(fact_inventory_dt['date'])
    dim_date_ts       = pd.to_datetime(dim_date['date'])

    # Проверка 1: нет висящих product_id
    base_product_ids = set(dim_product['product_id'])
    orphans = set(fact_sales_dt['product_id']) - base_product_ids
    if orphans:
        print(f'  ! Проверка 1: {len(orphans)} product_id не из базовых (ожидаемо после аномалий)')
    else:
        print('  ✓ Проверка 1: нет висящих product_id')

    # Проверка 2: closing_stock >= 0 (кроме аномалии №19)
    phys_ids = dim_product.loc[dim_product['format'].isin(['Paperback', 'Hardcover']), 'product_id']
    phys_inv = fact_inventory_dt[fact_inventory_dt['product_id'].isin(phys_ids)]
    neg_stock = (phys_inv['closing_stock'] < 0).sum()
    if neg_stock > 0 and anomalies_enabled:
        print(f'  ! Проверка 2: {neg_stock} отриц. остатков (ожидаемо, аномалия №19)')
    else:
        assert neg_stock == 0, 'Неожиданный отрицательный остаток!'
        print('  ✓ Проверка 2: closing_stock ≥ 0')

    # Проверка 3: цифровые форматы не в fact_inventory
    dig_ids = dim_product.loc[dim_product['format'].isin(['eBook', 'Audiobook', 'Subscription']), 'product_id']
    dig_inv = fact_inventory_dt[fact_inventory_dt['product_id'].isin(dig_ids)]
    assert dig_inv.empty, 'Цифровые продукты найдены в fact_inventory'
    print('  ✓ Проверка 3: цифровые продукты не в fact_inventory')

    # Проверка 4: даты fact_sales в пределах dim_date
    assert fact_sales_dt['date'].min() >= dim_date_ts.min(), 'Дата раньше начала периода'
    assert fact_sales_dt['date'].max() <= dim_date_ts.max(), 'Дата позже конца периода'
    print('  ✓ Проверка 4: даты в fact_sales корректны')

    # Проверка 5: полнота комбинаций в fact_plan
    end_dt = pd.to_datetime(END_DATE)
    expected_combos = set(
        (g, f, m, y)
        for y in range(2024, end_dt.year + 1)
        for m in range(1, 13)
        if pd.Timestamp(y, m, 1) <= end_dt.replace(day=1)
        for g in GENRES
        for f in ['eBook', 'Paperback', 'Hardcover', 'Audiobook']
    )
    actual_combos = set(zip(fact_plan['genre'], fact_plan['format'], fact_plan['month'], fact_plan['year']))
    missing = expected_combos - actual_combos
    if missing:
        print(f'  ! Проверка 5: пропущено {len(missing)} комбинаций (нормально для малых выборок)')
    else:
        print('  ✓ Проверка 5: все комбинации в fact_plan присутствуют')

    # Проверка 6: return_qty ≤ sales_qty (после аномалии №12 нарушается намеренно)
    violations = (fact_sales_dt['return_qty'] > fact_sales_dt['sales_qty']).sum()
    if violations > 0 and anomalies_enabled:
        print(f'  ! Проверка 6: {violations} нарушений return_qty>sales_qty (ожидаемо, аномалия №12)')
    else:
        assert violations == 0, 'return_qty > sales_qty!'
        print('  ✓ Проверка 6: return_qty ≤ sales_qty')

    # Проверка 7: для цифровых форматов возвраты = 0
    dig_sales = fact_sales_dt.merge(dim_product[['product_id', 'format']], on='product_id')
    dig_sales = dig_sales[dig_sales['format'].isin(['eBook', 'Audiobook', 'Subscription'])]
    assert (dig_sales['return_qty'] == 0).all(), 'Возвраты у цифровых продуктов'
    print('  ✓ Проверка 7: нет возвратов у цифровых форматов')

    # Проверка 8: sales_amount корректен (после аномалии №11 часть нарушена)
    mismatches = (~np.isclose(
        fact_sales_dt['sales_amount'],
        fact_sales_dt['sales_qty'] * fact_sales_dt['unit_price'],
        atol=0.02,
    )).sum()
    if mismatches > 0 and anomalies_enabled:
        print(f'  ! Проверка 8: {mismatches} некорректных sales_amount (ожидаемо, аномалия №11)')
    else:
        assert mismatches == 0, 'Неверный sales_amount!'
        print('  ✓ Проверка 8: sales_amount корректен')

    mode = 'С АНОМАЛИЯМИ' if anomalies_enabled else 'БЕЗ АНОМАЛИЙ'
    print(f'\n✅ Генерация завершена [{mode}]')
    print(f'📂 Файлы сохранены в: {os.path.abspath(OUTPUT_DIR)}\n')


if __name__ == '__main__':
    main()