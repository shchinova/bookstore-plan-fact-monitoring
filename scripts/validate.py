"""
scripts/validate.py

Модуль проверки и очистки данных.
Используется в load_history.py и daily_update.py.

Каждая функция возвращает:
    clean_df : pd.DataFrame  — очищенный датафрейм, готовый к загрузке в БД
    report   : dict          — статистика и детали для отчёта о качестве данных

Коды правил соответствуют docs/data_quality_rules.md.

Исправления относительно предыдущей версии:
    [FIX-1] _log принимает параметр n и корректно увеличивает счётчики на реальное
            количество строк, а не на 1.
    [FIX-2] validate_fact_sales / validate_fact_inventory: дата записывается обратно
            в df после парсинга, чтобы колонка передавалась в БД как date, а не str.
    [FIX-3] validate_fact_sales S-07: clean['date'] приводится к datetime перед
            построением pd.date_range, исключая TypeError при строковых значениях.
    [FIX-4] validate_dim_product P-16: уровень исправлен с 'warn' на 'reject',
            поскольку строки реально удаляются из датафрейма.
    [FIX-5] validate_fact_sales: добавлена проверка lost_sales_qty >= 0 (S-19).
    [FIX-6] validate_fact_inventory I-05: счётчик теперь корректно отражает
            количество удалённых дублей (через _log с n=...).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

# ─────────────────────────────────────────────────────────────────────────────
# Справочники допустимых значений (из data_quality_rules.md)
# ─────────────────────────────────────────────────────────────────────────────
GENRES = {
    'Fiction', 'Fantasy', 'Mystery', 'Romance', 'Science',
    'Biography', 'Business', 'Children', 'History', 'Poetry', 'Subscription',
}
# Жанры, допустимые в fact_plan (без Subscription — подписки не планируются по жанру)
GENRES_PLAN = {
    'Fiction', 'Fantasy', 'Mystery', 'Romance', 'Science',
    'Biography', 'Business', 'Children', 'History', 'Poetry',
}
FORMATS = {'eBook', 'Paperback', 'Hardcover', 'Audiobook', 'Subscription'}
FORMATS_PLAN = {'eBook', 'Paperback', 'Hardcover', 'Audiobook'}
CHANNELS = {'Web', 'Mobile App', 'API Partner'}
PHYSICAL_FORMATS = {'Paperback', 'Hardcover'}
DIGITAL_FORMATS = {'eBook', 'Audiobook', 'Subscription'}
LOW_STOCK_THRESHOLD = 15
PROJECT_START = date(2024, 1, 1)


# ─────────────────────────────────────────────────────────────────────────────
# Вспомогательные утилиты
# ─────────────────────────────────────────────────────────────────────────────

def _make_report(table: str) -> dict:
    """Создаёт пустую структуру отчёта для таблицы."""
    return {
        'table': table,
        'rows_received': 0,
        'rows_rejected': 0,
        'rows_fixed': 0,
        'warnings': 0,
        'rows_loaded': 0,
        'details': [],   # список строк для раздела «Детали»
    }


def _log(report: dict, level: str, code: str, message: str, n: int = 1) -> None:
    """
    Добавляет запись в отчёт и увеличивает счётчик на n (реальное кол-во строк).

    [FIX-1] Предыдущая версия всегда добавляла +1 к счётчику независимо от
    числа затронутых строк. Теперь передаём n явно.

    level: 'reject' | 'fix' | 'warn'
    """
    report['details'].append(f'[{code}] {message}')
    if level == 'reject':
        report['rows_rejected'] += n
    elif level == 'fix':
        report['rows_fixed'] += n
    elif level == 'warn':
        report['warnings'] += n


def _apply_general_rules(df: pd.DataFrame, report: dict) -> pd.DataFrame:
    """
    G-03: пустые строки → NULL
    G-04: лишние пробелы
    G-05: полностью пустые строки
    G-01/G-02 обрабатываются на уровне чтения файла (в load_history.py).
    """
    str_cols = df.select_dtypes(include=['object', 'string']).columns

    # G-04: strip
    fixed_strip = 0
    for col in str_cols:
        mask = df[col].notna() & (df[col].str.strip() != df[col])
        if mask.any():
            df.loc[mask, col] = df.loc[mask, col].str.strip()
            fixed_strip += mask.sum()
    if fixed_strip:
        _log(report, 'fix', 'G-04', f'Лишние пробелы обрезаны: {fixed_strip} значений', n=fixed_strip)

    # G-03: '' или ' ' → NaN
    fixed_empty = 0
    for col in str_cols:
        mask = df[col].notna() & (df[col].str.strip() == '')
        if mask.any():
            df.loc[mask, col] = np.nan
            fixed_empty += mask.sum()
    if fixed_empty:
        _log(report, 'fix', 'G-03', f'Пустые строки заменены на NULL: {fixed_empty} значений', n=fixed_empty)

    # G-05: полностью пустые строки
    empty_rows = df.isna().all(axis=1)
    if empty_rows.any():
        n = int(empty_rows.sum())
        df = df[~empty_rows].copy()
        _log(report, 'reject', 'G-05', f'Полностью пустые строки удалены: {n}', n=n)

    return df


def _try_parse_date(series: pd.Series) -> pd.Series:
    """Пытается привести серию к datetime. Возвращает серию с NaT для неудач."""
    return pd.to_datetime(series, errors='coerce', format='%Y-%m-%d')


def _validate_isbn13(isbn: str) -> bool:
    """Проверяет контрольную сумму ISBN-13."""
    if not isinstance(isbn, str):
        return False
    digits = re.sub(r'[^0-9]', '', isbn)
    if len(digits) != 13:
        return False
    total = sum(
        int(d) * (1 if i % 2 == 0 else 3)
        for i, d in enumerate(digits)
    )
    return total % 10 == 0


# ─────────────────────────────────────────────────────────────────────────────
# dim_date
# ─────────────────────────────────────────────────────────────────────────────

def validate_dim_date(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Правила: D-01 … D-06  (+ общие G-03..G-05)
    """
    report = _make_report('dim_date')
    report['rows_received'] = len(df)
    df = df.copy()
    df = _apply_general_rules(df, report)
    reject_mask = pd.Series(False, index=df.index)

    # D-01 / D-02: формат и валидность даты
    parsed = _try_parse_date(df['date'])
    bad_date = parsed.isna()
    if bad_date.any():
        n = int(bad_date.sum())
        reject_mask |= bad_date
        _log(report, 'reject', 'D-01/D-02', f'Невалидный формат даты: {n} строк', n=n)
    df.loc[~bad_date, 'date'] = parsed[~bad_date].dt.date

    # D-03: дубликаты по date
    dupes = df.duplicated(subset=['date'], keep='first') & ~reject_mask
    if dupes.any():
        n = int(dupes.sum())
        reject_mask |= dupes
        _log(report, 'reject', 'D-03', f'Дубликаты по date: {n} строк удалено', n=n)

    # Применяем отклонения до проверки производных полей
    clean = df[~reject_mask].copy()
    parsed_clean = pd.to_datetime(clean['date'], errors='coerce')

    # D-04: year, month, quarter
    expected_year    = parsed_clean.dt.year
    expected_month   = parsed_clean.dt.month
    expected_quarter = parsed_clean.dt.quarter
    mismatches = (
        (clean['year']    != expected_year)   |
        (clean['month']   != expected_month)  |
        (clean['quarter'] != expected_quarter)
    )
    if mismatches.any():
        n = int(mismatches.sum())
        clean.loc[mismatches, 'year']    = expected_year[mismatches]
        clean.loc[mismatches, 'month']   = expected_month[mismatches]
        clean.loc[mismatches, 'quarter'] = expected_quarter[mismatches]
        _log(report, 'fix', 'D-04', f'year/month/quarter пересчитаны из date: {n} строк', n=n)

    # D-05: is_weekend
    expected_weekend = (parsed_clean.dt.weekday >= 5).astype(int)
    bad_weekend = clean['is_weekend'] != expected_weekend
    if bad_weekend.any():
        n = int(bad_weekend.sum())
        clean.loc[bad_weekend, 'is_weekend'] = expected_weekend[bad_weekend]
        _log(report, 'fix', 'D-05', f'is_weekend пересчитан: {n} строк', n=n)

    # D-06: is_holiday_ru — булево
    bad_holiday = ~clean['is_holiday_ru'].isin([0, 1])
    if bad_holiday.any():
        n = int(bad_holiday.sum())
        clean = clean[~bad_holiday]
        _log(report, 'reject', 'D-06', f'Недопустимое значение is_holiday_ru: {n} строк', n=n)

    report['rows_loaded'] = len(clean)
    return clean, report


# ─────────────────────────────────────────────────────────────────────────────
# dim_product
# ─────────────────────────────────────────────────────────────────────────────

def validate_dim_product(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Правила: P-01 … P-16  (+ общие)
    """
    report = _make_report('dim_product')
    report['rows_received'] = len(df)
    df = df.copy()
    df = _apply_general_rules(df, report)
    reject_mask = pd.Series(False, index=df.index)

    # P-01: product_id — уникальный, NOT NULL, > 0
    null_id = df['product_id'].isna()
    if null_id.any():
        n = int(null_id.sum())
        reject_mask |= null_id
        _log(report, 'reject', 'P-01', f'product_id IS NULL: {n} строк', n=n)

    df['product_id'] = pd.to_numeric(df['product_id'], errors='coerce')
    bad_id = df['product_id'].isna() | (df['product_id'] <= 0)
    new_bad = bad_id & ~reject_mask
    if new_bad.any():
        n = int(new_bad.sum())
        reject_mask |= new_bad
        _log(report, 'reject', 'P-01', f'product_id не является целым > 0: {n} строк', n=n)

    dupes_id = df.duplicated(subset=['product_id'], keep='first') & ~reject_mask
    if dupes_id.any():
        n = int(dupes_id.sum())
        reject_mask |= dupes_id
        _log(report, 'reject', 'P-01', f'Дубликаты product_id: {n} строк', n=n)

    # P-02: title NOT NULL
    bad_title = df['title'].isna() & ~reject_mask
    if bad_title.any():
        n = int(bad_title.sum())
        reject_mask |= bad_title
        _log(report, 'reject', 'P-02', f'title IS NULL: {n} строк', n=n)

    # P-05: format
    bad_format = ~df['format'].isin(FORMATS) & ~reject_mask
    if bad_format.any():
        n = int(bad_format.sum())
        reject_mask |= bad_format
        _log(report, 'reject', 'P-05', f'Недопустимый format: {n} строк', n=n)

    # P-06: genre
    bad_genre = ~df['genre'].isin(GENRES) & ~reject_mask
    if bad_genre.any():
        n = int(bad_genre.sum())
        reject_mask |= bad_genre
        _log(report, 'reject', 'P-06', f'Недопустимый genre: {n} строк', n=n)

    # P-08: price_rub > 0
    df['price_rub'] = pd.to_numeric(df['price_rub'], errors='coerce')
    bad_price = (df['price_rub'].isna() | (df['price_rub'] <= 0)) & ~reject_mask
    if bad_price.any():
        n = int(bad_price.sum())
        reject_mask |= bad_price
        _log(report, 'reject', 'P-08', f'price_rub <= 0 или NULL: {n} строк', n=n)

    # P-09: cost_rub > 0
    df['cost_rub'] = pd.to_numeric(df['cost_rub'], errors='coerce')
    bad_cost = (df['cost_rub'].isna() | (df['cost_rub'] <= 0)) & ~reject_mask
    if bad_cost.any():
        n = int(bad_cost.sum())
        reject_mask |= bad_cost
        _log(report, 'reject', 'P-09', f'cost_rub <= 0 или NULL: {n} строк', n=n)

    # P-13: avg_rating 0..5
    df['avg_rating'] = pd.to_numeric(df['avg_rating'], errors='coerce')
    bad_rating = (
        df['avg_rating'].isna() |
        (df['avg_rating'] < 0) |
        (df['avg_rating'] > 5)
    ) & ~reject_mask
    if bad_rating.any():
        n = int(bad_rating.sum())
        reject_mask |= bad_rating
        _log(report, 'reject', 'P-13', f'avg_rating вне диапазона [0, 5]: {n} строк', n=n)

    # P-14: review_count >= 0
    df['review_count'] = pd.to_numeric(df['review_count'], errors='coerce')
    bad_reviews = (df['review_count'].isna() | (df['review_count'] < 0)) & ~reject_mask
    if bad_reviews.any():
        n = int(bad_reviews.sum())
        reject_mask |= bad_reviews
        _log(report, 'reject', 'P-14', f'review_count < 0 или NULL: {n} строк', n=n)

    # Работаем с чистым датафреймом для предупреждений и исправлений
    clean = df[~reject_mask].copy()

    # P-03: author NULL у не-подписок
    warn_author = clean['author'].isna() & (clean['format'] != 'Subscription')
    if warn_author.any():
        n = int(warn_author.sum())
        _log(report, 'warn', 'P-03', f'author IS NULL у не-подписок: {n} строк', n=n)

    # P-04: publisher NULL у не-подписок
    warn_publisher = clean['publisher'].isna() & (clean['format'] != 'Subscription')
    if warn_publisher.any():
        n = int(warn_publisher.sum())
        _log(report, 'warn', 'P-04', f'publisher IS NULL у не-подписок: {n} строк', n=n)

    # P-07: ISBN
    book_mask = clean['format'] != 'Subscription'
    bad_isbn = book_mask & clean['isbn'].apply(
        lambda x: not _validate_isbn13(str(x)) if pd.notna(x) else False
    )
    if bad_isbn.any():
        n = int(bad_isbn.sum())
        _log(report, 'warn', 'P-07', f'Невалидный ISBN-13: {n} строк', n=n)

    # P-10: cost > price
    warn_margin = clean['cost_rub'] > clean['price_rub']
    if warn_margin.any():
        n = int(warn_margin.sum())
        _log(report, 'warn', 'P-10', f'cost_rub > price_rub: {n} строк', n=n)

    # P-11: page_count NULL у физических форматов
    warn_page_physical = clean['format'].isin(PHYSICAL_FORMATS) & clean['page_count'].isna()
    if warn_page_physical.any():
        n = int(warn_page_physical.sum())
        _log(report, 'warn', 'P-11', f'page_count IS NULL у Paperback/Hardcover: {n} строк', n=n)

    # P-12: stock_initial у цифровых форматов — обнуляем
    fix_stock = clean['format'].isin(DIGITAL_FORMATS) & clean['stock_initial'].notna()
    if fix_stock.any():
        n = int(fix_stock.sum())
        clean.loc[fix_stock, 'stock_initial'] = np.nan
        _log(report, 'fix', 'P-12', f'stock_initial обнулён у цифровых форматов: {n} строк', n=n)

    # P-15: is_physical пересчитываем из format
    expected_physical = clean['format'].isin(PHYSICAL_FORMATS).astype(int)
    bad_physical = clean['is_physical'].astype(int) != expected_physical
    if bad_physical.any():
        n = int(bad_physical.sum())
        clean.loc[bad_physical, 'is_physical'] = expected_physical[bad_physical]
        _log(report, 'fix', 'P-15', f'is_physical пересчитан из format: {n} строк', n=n)

    # P-16: дубликаты записей (все поля кроме product_id)
    # [FIX-4] Уровень исправлен с 'warn' на 'reject': строки реально удаляются,
    # поэтому счётчик rows_rejected должен отражать это, а не rows_warnings.
    dup_cols = [c for c in clean.columns if c != 'product_id']
    dupes_rec = clean.duplicated(subset=dup_cols, keep='first')
    if dupes_rec.any():
        n = int(dupes_rec.sum())
        clean = clean[~dupes_rec]
        _log(report, 'reject', 'P-16', f'Полные дубликаты записей (кроме product_id): {n} строк удалено', n=n)

    report['rows_loaded'] = len(clean)
    return clean, report


# ─────────────────────────────────────────────────────────────────────────────
# fact_plan
# ─────────────────────────────────────────────────────────────────────────────

def validate_fact_plan(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Правила: PL-01 … PL-10  (+ общие)
    """
    report = _make_report('fact_plan')
    report['rows_received'] = len(df)
    df = df.copy()
    df = _apply_general_rules(df, report)
    reject_mask = pd.Series(False, index=df.index)

    # PL-01: plan_id уникальный
    dupes_id = df.duplicated(subset=['plan_id'], keep='first')
    if dupes_id.any():
        n = int(dupes_id.sum())
        reject_mask |= dupes_id
        _log(report, 'reject', 'PL-01', f'Дубликаты plan_id: {n} строк', n=n)

    # PL-02: genre
    # [FIX-2] Используем явный набор GENRES_PLAN вместо GENRES - {'Subscription'},
    # чтобы не зависеть от состава константы GENRES при её возможном изменении.
    bad_genre = ~df['genre'].isin(GENRES_PLAN) & ~reject_mask
    if bad_genre.any():
        n = int(bad_genre.sum())
        reject_mask |= bad_genre
        _log(report, 'reject', 'PL-02', f'Недопустимый genre: {n} строк', n=n)

    # PL-03: format
    bad_format = ~df['format'].isin(FORMATS_PLAN) & ~reject_mask
    if bad_format.any():
        n = int(bad_format.sum())
        reject_mask |= bad_format
        _log(report, 'reject', 'PL-03', f'Недопустимый format: {n} строк', n=n)

    # PL-04: year
    df['year'] = pd.to_numeric(df['year'], errors='coerce')
    bad_year = (df['year'].isna() | ~df['year'].between(2024, 2026)) & ~reject_mask
    if bad_year.any():
        n = int(bad_year.sum())
        reject_mask |= bad_year
        _log(report, 'reject', 'PL-04', f'year вне диапазона [2024, 2026]: {n} строк', n=n)

    # PL-05: month
    df['month'] = pd.to_numeric(df['month'], errors='coerce')
    bad_month = (df['month'].isna() | ~df['month'].between(1, 12)) & ~reject_mask
    if bad_month.any():
        n = int(bad_month.sum())
        reject_mask |= bad_month
        _log(report, 'reject', 'PL-05', f'month вне диапазона [1, 12]: {n} строк', n=n)

    # PL-06: plan_qty >= 0
    df['plan_qty'] = pd.to_numeric(df['plan_qty'], errors='coerce')
    bad_qty = (df['plan_qty'].isna() | (df['plan_qty'] < 0)) & ~reject_mask
    if bad_qty.any():
        n = int(bad_qty.sum())
        reject_mask |= bad_qty
        _log(report, 'reject', 'PL-06', f'plan_qty < 0: {n} строк', n=n)

    # PL-07: plan_amount >= 0
    df['plan_amount'] = pd.to_numeric(df['plan_amount'], errors='coerce')
    bad_amount = (df['plan_amount'].isna() | (df['plan_amount'] < 0)) & ~reject_mask
    if bad_amount.any():
        n = int(bad_amount.sum())
        reject_mask |= bad_amount
        _log(report, 'reject', 'PL-07', f'plan_amount < 0: {n} строк', n=n)

    # PL-09: plan_margin_target 0..1
    df['plan_margin_target'] = pd.to_numeric(df['plan_margin_target'], errors='coerce')
    bad_margin = (
        df['plan_margin_target'].isna() |
        (df['plan_margin_target'] < 0) |
        (df['plan_margin_target'] > 1)
    ) & ~reject_mask
    if bad_margin.any():
        n = int(bad_margin.sum())
        reject_mask |= bad_margin
        _log(report, 'reject', 'PL-09', f'plan_margin_target вне [0, 1]: {n} строк', n=n)

    clean = df[~reject_mask].copy()

    # PL-08: qty=0 но amount!=0 (или наоборот)
    inconsistent = (
        ((clean['plan_qty'] == 0) & (clean['plan_amount'] != 0)) |
        ((clean['plan_amount'] == 0) & (clean['plan_qty'] != 0))
    )
    if inconsistent.any():
        n = int(inconsistent.sum())
        _log(report, 'warn', 'PL-08', f'plan_qty/plan_amount несогласованы: {n} строк', n=n)

    # PL-10: уникальность (genre, format, year, month)
    key_cols = ['genre', 'format', 'year', 'month']
    dupes_key = clean.duplicated(subset=key_cols, keep='first')
    if dupes_key.any():
        n = int(dupes_key.sum())
        clean = clean[~dupes_key]
        _log(report, 'reject', 'PL-10', f'Дубликаты (genre, format, year, month): {n} строк', n=n)

    report['rows_loaded'] = len(clean)
    return clean, report


# ─────────────────────────────────────────────────────────────────────────────
# fact_sales
# ─────────────────────────────────────────────────────────────────────────────

def validate_fact_sales(
    df: pd.DataFrame,
    dim_product: pd.DataFrame,
) -> tuple[pd.DataFrame, dict]:
    """
    Правила: S-01 … S-19  (+ общие)
    dim_product нужен для проверки product_id и определения формата.
    """
    report = _make_report('fact_sales')
    report['rows_received'] = len(df)
    df = df.copy()
    df = _apply_general_rules(df, report)
    reject_mask = pd.Series(False, index=df.index)

    valid_product_ids = set(dim_product['product_id'].dropna().astype(int))
    product_format = dim_product.set_index('product_id')['format'].to_dict()

    # S-01: sales_id уникальный
    dupes_sid = df.duplicated(subset=['sales_id'], keep='first')
    if dupes_sid.any():
        n = int(dupes_sid.sum())
        reject_mask |= dupes_sid
        _log(report, 'reject', 'S-01', f'Дубликаты sales_id: {n} строк', n=n)

    # S-02: order_id NOT NULL
    null_oid = df['order_id'].isna() & ~reject_mask
    if null_oid.any():
        n = int(null_oid.sum())
        _log(report, 'warn', 'S-02', f'order_id IS NULL: {n} строк', n=n)

    # S-04: product_id существует в dim_product
    df['product_id'] = pd.to_numeric(df['product_id'], errors='coerce')
    bad_pid = (~df['product_id'].isin(valid_product_ids)) & ~reject_mask
    if bad_pid.any():
        n = int(bad_pid.sum())
        reject_mask |= bad_pid
        _log(report, 'reject', 'S-04', f'product_id не в dim_product: {n} строк', n=n)

    # S-05 / S-06: дата
    parsed_date = _try_parse_date(df['date'])
    bad_date_fmt = parsed_date.isna() & ~reject_mask
    if bad_date_fmt.any():
        n = int(bad_date_fmt.sum())
        reject_mask |= bad_date_fmt
        _log(report, 'reject', 'S-05', f'Невалидный формат даты: {n} строк', n=n)

    today = pd.Timestamp(date.today())
    bad_date_range = (
        (parsed_date < pd.Timestamp(PROJECT_START)) |
        (parsed_date > today)
    ) & ~reject_mask & ~bad_date_fmt
    if bad_date_range.any():
        n = int(bad_date_range.sum())
        reject_mask |= bad_date_range
        _log(report, 'reject', 'S-06', f'Дата вне периода проекта: {n} строк', n=n)

    # [FIX-2] Записываем нормализованную дату обратно в df после финализации
    # reject_mask. Используем строку YYYY-MM-DD — совместимо с любым pandas dtype.
    valid_date_mask = ~bad_date_fmt & ~reject_mask
    df.loc[valid_date_mask, 'date'] = parsed_date[valid_date_mask].dt.strftime('%Y-%m-%d')

    # S-08: sales_qty > 0
    df['sales_qty'] = pd.to_numeric(df['sales_qty'], errors='coerce')
    bad_qty = (df['sales_qty'].isna() | (df['sales_qty'] <= 0)) & ~reject_mask
    if bad_qty.any():
        n = int(bad_qty.sum())
        reject_mask |= bad_qty
        _log(report, 'reject', 'S-08', f'sales_qty <= 0: {n} строк', n=n)

    # S-09: unit_price > 0
    df['unit_price'] = pd.to_numeric(df['unit_price'], errors='coerce')
    bad_uprice = (df['unit_price'].isna() | (df['unit_price'] <= 0)) & ~reject_mask
    if bad_uprice.any():
        n = int(bad_uprice.sum())
        reject_mask |= bad_uprice
        _log(report, 'reject', 'S-09', f'unit_price <= 0: {n} строк', n=n)

    # S-14: discount_percent 0..100
    df['discount_percent'] = pd.to_numeric(df['discount_percent'], errors='coerce')
    bad_disc = (
        df['discount_percent'].isna() |
        (df['discount_percent'] < 0) |
        (df['discount_percent'] > 100)
    ) & ~reject_mask
    if bad_disc.any():
        n = int(bad_disc.sum())
        reject_mask |= bad_disc
        _log(report, 'reject', 'S-14', f'discount_percent вне [0, 100]: {n} строк', n=n)

    # S-17: channel
    bad_channel = ~df['channel'].isin(CHANNELS) & ~reject_mask
    if bad_channel.any():
        n = int(bad_channel.sum())
        reject_mask |= bad_channel
        _log(report, 'reject', 'S-17', f'Недопустимый channel: {n} строк', n=n)

    clean = df[~reject_mask].copy()

    # Добавляем format для дальнейших проверок
    clean['_format'] = clean['product_id'].map(product_format)
    is_digital = clean['_format'].isin(DIGITAL_FORMATS)

    # S-03: уникальность (order_id, product_id)
    dupes_op = clean.duplicated(subset=['order_id', 'product_id'], keep='first')
    if dupes_op.any():
        n = int(dupes_op.sum())
        clean = clean[~dupes_op]
        _log(report, 'reject', 'S-03', f'Дубликаты (order_id, product_id): {n} строк', n=n)

    # S-07: потерянные дни
    # [FIX-3] Приводим clean['date'] к datetime перед pd.date_range, иначе
    # при строковых значениях возникает TypeError. После FIX-2 поле уже date,
    # но pd.to_datetime безопасно обрабатывает оба типа.
    clean_dates_dt = pd.to_datetime(clean['date'], errors='coerce')
    if clean_dates_dt.notna().any():
        all_dates   = pd.date_range(PROJECT_START, clean_dates_dt.max(), freq='D')
        sales_dates = set(clean_dates_dt.dropna().dt.date)
        missing_days = [d.date() for d in all_dates if d.date() not in sales_dates]
        if missing_days:
            sample = ', '.join(str(d) for d in missing_days[:5])
            suffix = f' ... (итого {len(missing_days)})' if len(missing_days) > 5 else ''
            _log(report, 'warn', 'S-07', f'Потерянные дни: {sample}{suffix}', n=len(missing_days))

    # S-10: sales_amount ≈ sales_qty × unit_price
    expected_amount = clean['sales_qty'] * clean['unit_price']
    bad_amount = ~np.isclose(
        clean['sales_amount'].astype(float),
        expected_amount,
        rtol=0.01,
    )
    if bad_amount.any():
        n = int(bad_amount.sum())
        _log(report, 'warn', 'S-10', f'sales_amount расходится с sales_qty × unit_price: {n} строк', n=n)

    # S-11: возвраты у цифровых форматов
    bad_return_digital = is_digital & (clean['return_qty'] > 0)
    if bad_return_digital.any():
        n = int(bad_return_digital.sum())
        _log(report, 'warn', 'S-11', f'return_qty > 0 у цифровых форматов: {n} строк', n=n)

    # S-12: return_qty > sales_qty
    bad_return_excess = clean['return_qty'] > clean['sales_qty']
    if bad_return_excess.any():
        n = int(bad_return_excess.sum())
        _log(report, 'warn', 'S-12', f'return_qty > sales_qty: {n} строк', n=n)

    # S-13: return_amount
    expected_ret_amount = clean['return_qty'] * clean['unit_price']
    bad_ret_amount = (
        ~np.isclose(
            clean['return_amount'].astype(float),
            expected_ret_amount,
            rtol=0.01,
        )
    ) | ((clean['return_qty'] == 0) & (clean['return_amount'] != 0))
    if bad_ret_amount.any():
        n = int(bad_ret_amount.sum())
        _log(report, 'warn', 'S-13', f'return_amount не соответствует return_qty × unit_price: {n} строк', n=n)

    # S-15: is_promo=1 → discount>0 и promo_code NOT NULL
    promo_on = clean['is_promo'] == 1
    bad_promo = promo_on & ((clean['discount_percent'] == 0) | clean['promo_code'].isna())
    if bad_promo.any():
        n = int(bad_promo.sum())
        _log(report, 'warn', 'S-15', f'is_promo=1 без скидки или promo_code: {n} строк', n=n)

    # S-16: is_promo=0 → discount=0
    promo_off = clean['is_promo'] == 0
    bad_no_promo = promo_off & (clean['discount_percent'] > 0)
    if bad_no_promo.any():
        n = int(bad_no_promo.sum())
        _log(report, 'warn', 'S-16', f'is_promo=0 но discount_percent > 0: {n} строк', n=n)

    # S-18: lost_sales_qty > 0 у цифровых форматов
    bad_lost_digital = is_digital & (clean['lost_sales_qty'] > 0)
    if bad_lost_digital.any():
        n = int(bad_lost_digital.sum())
        _log(report, 'warn', 'S-18', f'lost_sales_qty > 0 у цифровых форматов: {n} строк', n=n)

    # S-19: lost_sales_qty >= 0
    # [FIX-5] Добавлена отсутствовавшая проверка неотрицательности поля.
    df['lost_sales_qty'] = pd.to_numeric(clean.get('lost_sales_qty', 0), errors='coerce').fillna(0)
    bad_lost_neg = clean['lost_sales_qty'] < 0
    if bad_lost_neg.any():
        n = int(bad_lost_neg.sum())
        _log(report, 'warn', 'S-19', f'lost_sales_qty < 0: {n} строк', n=n)

    # Удаляем служебную колонку
    clean = clean.drop(columns=['_format'])

    report['rows_loaded'] = len(clean)
    return clean, report


# ─────────────────────────────────────────────────────────────────────────────
# fact_inventory
# ─────────────────────────────────────────────────────────────────────────────

def validate_fact_inventory(
    df: pd.DataFrame,
    dim_product: pd.DataFrame,
) -> tuple[pd.DataFrame, dict]:
    """
    Правила: I-01 … I-11  (+ общие)
    """
    report = _make_report('fact_inventory')
    report['rows_received'] = len(df)
    df = df.copy()
    df = _apply_general_rules(df, report)
    reject_mask = pd.Series(False, index=df.index)

    valid_physical_ids = set(
        dim_product.loc[dim_product['format'].isin(PHYSICAL_FORMATS), 'product_id']
        .dropna().astype(int)
    )
    valid_all_ids = set(dim_product['product_id'].dropna().astype(int))

    # I-01: product_id существует в dim_product
    df['product_id'] = pd.to_numeric(df['product_id'], errors='coerce')
    bad_pid = (~df['product_id'].isin(valid_all_ids)) & ~reject_mask
    if bad_pid.any():
        n = int(bad_pid.sum())
        reject_mask |= bad_pid
        _log(report, 'reject', 'I-01', f'product_id не в dim_product: {n} строк', n=n)

    # I-02: только физические форматы
    not_physical = (~df['product_id'].isin(valid_physical_ids)) & ~reject_mask
    if not_physical.any():
        n = int(not_physical.sum())
        reject_mask |= not_physical
        _log(report, 'reject', 'I-02', f'product_id не является физическим товаром: {n} строк', n=n)

    # I-03 / I-04: дата
    parsed_date = _try_parse_date(df['date'])
    bad_date_fmt = parsed_date.isna() & ~reject_mask
    if bad_date_fmt.any():
        n = int(bad_date_fmt.sum())
        reject_mask |= bad_date_fmt
        _log(report, 'reject', 'I-03', f'Невалидный формат даты: {n} строк', n=n)

    # [FIX-2] Записываем нормализованную дату обратно в df после финализации
    # reject_mask. Используем строку YYYY-MM-DD — совместимо с любым pandas dtype.
    valid_date_mask = ~bad_date_fmt & ~reject_mask
    df.loc[valid_date_mask, 'date'] = parsed_date[valid_date_mask].dt.strftime('%Y-%m-%d')

    today = pd.Timestamp(date.today())
    bad_date_range = (
        (parsed_date < pd.Timestamp(PROJECT_START)) |
        (parsed_date > today)
    ) & ~reject_mask & ~bad_date_fmt
    if bad_date_range.any():
        n = int(bad_date_range.sum())
        reject_mask |= bad_date_range
        _log(report, 'reject', 'I-04', f'Дата вне периода проекта: {n} строк', n=n)

    # I-06: opening_stock >= 0
    df['opening_stock'] = pd.to_numeric(df['opening_stock'], errors='coerce')
    bad_open = (df['opening_stock'].isna() | (df['opening_stock'] < 0)) & ~reject_mask
    if bad_open.any():
        n = int(bad_open.sum())
        reject_mask |= bad_open
        _log(report, 'reject', 'I-06', f'opening_stock < 0: {n} строк', n=n)

    # I-07: sold_qty >= 0
    df['sold_qty'] = pd.to_numeric(df['sold_qty'], errors='coerce')
    bad_sold = (df['sold_qty'].isna() | (df['sold_qty'] < 0)) & ~reject_mask
    if bad_sold.any():
        n = int(bad_sold.sum())
        reject_mask |= bad_sold
        _log(report, 'reject', 'I-07', f'sold_qty < 0: {n} строк', n=n)

    # I-08: replenishment_qty >= 0
    df['replenishment_qty'] = pd.to_numeric(df['replenishment_qty'], errors='coerce')
    bad_replen = (df['replenishment_qty'].isna() | (df['replenishment_qty'] < 0)) & ~reject_mask
    if bad_replen.any():
        n = int(bad_replen.sum())
        reject_mask |= bad_replen
        _log(report, 'reject', 'I-08', f'replenishment_qty < 0: {n} строк', n=n)

    # I-09: closing_stock >= 0
    df['closing_stock'] = pd.to_numeric(df['closing_stock'], errors='coerce')
    bad_close = (df['closing_stock'].isna() | (df['closing_stock'] < 0)) & ~reject_mask
    if bad_close.any():
        n = int(bad_close.sum())
        reject_mask |= bad_close
        _log(report, 'reject', 'I-09', f'closing_stock < 0: {n} строк', n=n)

    clean = df[~reject_mask].copy()

    # I-05: уникальность (product_id, date)
    # [FIX-6] Передаём реальное количество удалённых строк в _log через n=.
    dupes = clean.duplicated(subset=['product_id', 'date'], keep='first')
    if dupes.any():
        n = int(dupes.sum())
        clean = clean[~dupes]
        _log(report, 'reject', 'I-05', f'Дубликаты (product_id, date): {n} строк', n=n)

    # I-10: баланс closing = opening - sold + replenishment
    expected_close = clean['opening_stock'] - clean['sold_qty'] + clean['replenishment_qty']
    bad_balance = ~np.isclose(
        clean['closing_stock'].astype(float),
        expected_close.astype(float),
        atol=1,
    )
    if bad_balance.any():
        n = int(bad_balance.sum())
        _log(report, 'warn', 'I-10',
             f'closing_stock не совпадает с opening - sold + replenishment: {n} строк', n=n)

    # I-11: is_low_stock пересчитываем
    expected_low = (clean['closing_stock'] < LOW_STOCK_THRESHOLD).astype(int)
    bad_low = clean['is_low_stock'].astype(int) != expected_low
    if bad_low.any():
        n = int(bad_low.sum())
        clean.loc[bad_low, 'is_low_stock'] = expected_low[bad_low]
        _log(report, 'fix', 'I-11', f'is_low_stock пересчитан: {n} строк', n=n)

    report['rows_loaded'] = len(clean)
    return clean, report


# ─────────────────────────────────────────────────────────────────────────────
# Формирование текста отчёта
# ─────────────────────────────────────────────────────────────────────────────

def build_report_md(
    reports: list[dict],
    script_name: str,
) -> str:
    """Собирает Markdown-отчёт из списка результатов по каждой таблице."""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    lines = [
        '# Отчёт о качестве данных',
        f'Дата запуска: {now}',
        f'Скрипт: {script_name}',
        '',
        '## Сводка',
        '',
        '| Таблица | Строк получено | Отклонено | Исправлено | Предупреждений | Загружено |',
        '|---------|----------------|-----------|------------|----------------|-----------|',
    ]

    total_issues = 0
    for r in reports:
        lines.append(
            f"| {r['table']} "
            f"| {r['rows_received']} "
            f"| {r['rows_rejected']} "
            f"| {r['rows_fixed']} "
            f"| {r['warnings']} "
            f"| {r['rows_loaded']} |"
        )
        total_issues += r['rows_rejected'] + r['warnings']

    lines += ['', '## Детали по таблицам', '']
    for r in reports:
        if r['details']:
            lines.append(f"### {r['table']}")
            for d in r['details']:
                lines.append(f'- {d}')
            lines.append('')

    status = 'Загрузка завершена без ошибок.' if total_issues == 0 \
        else f'Загрузка завершена. Всего проблем: {total_issues}.'
    lines += ['## Итог', status, '']

    return '\n'.join(lines)