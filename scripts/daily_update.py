"""
scripts/daily_update.py

Эмуляция ежедневного обновления базы данных.

Порядок работы:
    1. Читает sales_update.csv и inventory_update.csv из data/raw/
    2. Валидирует данные через scripts/validate.py
       — для fact_sales нужен dim_product из БД (проверка product_id)
       — для fact_inventory нужен dim_product из БД (проверка физических форматов)
    3. Сохраняет отчёт о качестве данных в reports/
       — имя файла содержит временну́ю метку: каждый отчёт уникален,
         предыдущие отчёты не удаляются и не перезаписываются
    4. Добавляет очищенные строки в fact_sales и fact_inventory (INSERT)
       — строки, уже присутствующие в БД по первичному ключу, пропускаются
    5. Пересчитывает витрины данных через scripts/refresh_all_marts.py

Идемпотентность:
    Повторный запуск с теми же файлами безопасен: INSERT ... ON CONFLICT
    DO NOTHING гарантирует, что дублей в БД не появится.

Запуск из корня проекта:
    python scripts/daily_update.py

Переменные окружения (задаются в .env в корне проекта):
    DB_HOST      — хост PostgreSQL        (по умолчанию: localhost)
    DB_PORT      — порт                   (по умолчанию: 5432)
    DB_NAME      — имя базы данных        (по умолчанию: bookstore_ods)
    DB_USER      — пользователь           (по умолчанию: postgres)
    DB_PASSWORD  — пароль

Зависимости:
    pip install pandas sqlalchemy psycopg2-binary python-dotenv
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# ─── пути ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

from validate import (
    validate_fact_sales,
    validate_fact_inventory,
    build_report_md,
)
from refresh_all_marts import refresh_all_marts

RAW_DIR     = ROOT / 'data'    / 'raw'
REPORTS_DIR = ROOT / 'reports'


# ═════════════════════════════════════════════════════════════════════════════
# Подключение к БД
# ═════════════════════════════════════════════════════════════════════════════

def _db_params() -> dict:
    load_dotenv(ROOT / '.env')
    return {
        'host':     os.getenv('DB_HOST',     'localhost'),
        'port':     os.getenv('DB_PORT',     '5432'),
        'dbname':   os.getenv('DB_NAME',     'bookstore_ods'),
        'user':     os.getenv('DB_USER',     'postgres'),
        'password': os.getenv('DB_PASSWORD', ''),
    }


def get_engine():
    p   = _db_params()
    url = (
        f"postgresql+psycopg2://{p['user']}:{p['password']}"
        f"@{p['host']}:{p['port']}/{p['dbname']}"
    )
    engine = create_engine(url, echo=False)
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))
    print(f"  ✓ SQLAlchemy engine: {p['dbname']}@{p['host']}:{p['port']}")
    return engine


def get_psycopg2_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(**_db_params())


# ═════════════════════════════════════════════════════════════════════════════
# Чтение CSV
# ═════════════════════════════════════════════════════════════════════════════

def read_csv(filename: str) -> pd.DataFrame:
    """
    Читает CSV из data/raw/.
    Кодировка: utf-8-sig → utf-8 → cp1251.
    Все колонки — строки; конвертация типов — в validate.py.
    """
    path = RAW_DIR / filename
    for encoding in ('utf-8-sig', 'utf-8', 'cp1251'):
        try:
            df = pd.read_csv(
                path,
                encoding=encoding,
                dtype=str,
                on_bad_lines='warn',
            )
            print(f'    {filename}: {len(df):,} строк [{encoding}]')
            return df
        except UnicodeDecodeError:
            continue
    raise RuntimeError(
        f'Не удалось прочитать {filename}: ни одна кодировка не подошла'
    )


# ═════════════════════════════════════════════════════════════════════════════
# Приведение типов перед загрузкой
# ═════════════════════════════════════════════════════════════════════════════

# Схема типов — только для обновляемых таблиц.
# Nullable-поля (order_id, stock_initial и т.п.) → float64, чтобы
# pandas мог хранить NaN без исключений.
_DTYPE_MAP: dict[str, dict[str, str]] = {
    'fact_sales': {
        'sales_id':         'int32',
        'order_id':         'float64',
        'product_id':       'int32',
        'date':             'date',
        'sales_qty':        'int32',
        'return_qty':       'int32',
        'unit_price':       'float64',
        'sales_amount':     'float64',
        'return_amount':    'float64',
        'discount_percent': 'int16',
        'is_promo':         'int16',
        'lost_sales_qty':   'int32',
    },
    'fact_inventory': {
        'inventory_id':      'int32',
        'product_id':        'int32',
        'date':              'date',
        'opening_stock':     'int32',
        'sold_qty':          'int32',
        'replenishment_qty': 'int32',
        'closing_stock':     'int32',
        'is_low_stock':      'int16',
    },
}


def cast_types(df: pd.DataFrame, table: str) -> pd.DataFrame:
    """
    Приводит колонки к типам, совместимым со схемой PostgreSQL.
    date-колонки → Python date (psycopg2 передаёт их как DATE).
    Числовые → явный dtype из _DTYPE_MAP.
    """
    df      = df.copy()
    mapping = _DTYPE_MAP.get(table, {})
    for col, dtype in mapping.items():
        if col not in df.columns:
            continue
        if dtype == 'date':
            df[col] = pd.to_datetime(df[col], errors='coerce').dt.date
        else:
            df[col] = pd.to_numeric(df[col], errors='coerce').astype(dtype)
    return df


# ═════════════════════════════════════════════════════════════════════════════
# Загрузка dim_product из БД (нужен для validate_fact_sales / validate_fact_inventory)
# ═════════════════════════════════════════════════════════════════════════════

def load_dim_product(engine) -> pd.DataFrame:
    """
    Читает dim_product из PostgreSQL.
    validate_fact_sales и validate_fact_inventory принимают его как аргумент
    для проверки product_id и определения формата (физический / цифровой).
    """
    df = pd.read_sql('SELECT * FROM dim_product', con=engine)
    print(f'    dim_product из БД: {len(df):,} строк')
    return df


# ═════════════════════════════════════════════════════════════════════════════
# Вставка строк с защитой от дублей
# ═════════════════════════════════════════════════════════════════════════════

def _build_insert_sql(table: str, columns: list[str]) -> str:
    """
    Формирует INSERT ... ON CONFLICT DO NOTHING.
    Конфликт определяется по первичному ключу таблицы:
      fact_sales      → sales_id
      fact_inventory  → inventory_id (или UNIQUE(product_id, date))
    PostgreSQL сам использует PK/UNIQUE из схемы, явно указывать не нужно.
    """
    cols_sql  = ', '.join(f'"{c}"' for c in columns)
    vals_sql  = ', '.join(f'%s' for _ in columns)
    return (
        f'INSERT INTO {table} ({cols_sql}) '
        f'VALUES ({vals_sql}) '
        f'ON CONFLICT DO NOTHING'
    )


def insert_rows(
    df: pd.DataFrame,
    table: str,
    conn: psycopg2.extensions.connection,
) -> int:
    """
    Вставляет строки датафрейма в таблицу пакетами по 2 000 строк.
    Возвращает количество реально вставленных строк (без конфликтов).

    ON CONFLICT DO NOTHING обеспечивает идемпотентность: повторный запуск
    с теми же данными не создаст дублей и не выдаст ошибку.
    """
    if df.empty:
        print(f'    → [{table}]: нет строк для вставки')
        return 0

    columns  = list(df.columns)
    sql      = _build_insert_sql(table, columns)
    # Заменяем pandas NA/NaN → None, чтобы psycopg2 передал их как SQL NULL
    records  = [
        tuple(None if pd.isna(v) else v for v in row)
        for row in df.itertuples(index=False, name=None)
    ]

    inserted = 0
    batch    = 2_000
    t0       = time.perf_counter()

    with conn.cursor() as cur:
        for start in range(0, len(records), batch):
            chunk = records[start : start + batch]
            psycopg2.extras.execute_batch(cur, sql, chunk, page_size=batch)
            inserted += cur.rowcount  # rowcount = реально вставленных строк
    conn.commit()

    elapsed = time.perf_counter() - t0
    skipped = len(df) - inserted
    print(
        f'    → [{table}]: {inserted:,} вставлено'
        + (f', {skipped:,} пропущено (дубли)' if skipped else '')
        + f' — {elapsed:.1f}s'
    )
    return inserted


# ═════════════════════════════════════════════════════════════════════════════
# Сохранение отчёта
# ═════════════════════════════════════════════════════════════════════════════

def save_report(reports: list[dict]) -> Path:
    """
    Сохраняет Markdown-отчёт в reports/ с временнóй меткой в имени файла.

    Имя формата: daily_update_YYYY-MM-DD_HH-MM-SS.md
    Секунды в метке гарантируют уникальность даже при нескольких запусках
    в течение одной минуты. Предыдущие отчёты не удаляются и не изменяются.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    # Секунды в метке — уникальность при нескольких запусках в день
    timestamp   = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    report_path = REPORTS_DIR / f'daily_update_{timestamp}.md'
    report_md   = build_report_md(reports, script_name='daily_update.py')
    report_path.write_text(report_md, encoding='utf-8')
    print(f'  ✓ Отчёт: {report_path.relative_to(ROOT)}')
    return report_path


# ═════════════════════════════════════════════════════════════════════════════
# Проверка наличия данных за обновляемую дату в БД
# ═════════════════════════════════════════════════════════════════════════════

def _check_update_date(df: pd.DataFrame, table: str, engine) -> None:
    """
    Информирует, если данные за дату из файла уже частично есть в БД.
    Не блокирует вставку (ON CONFLICT DO NOTHING справится),
    но помогает при отладке понять, что происходит повторный запуск.
    """
    dates = pd.to_datetime(df['date'], errors='coerce').dt.date.dropna().unique()
    if not len(dates):
        return
    dates_list = ', '.join(f"'{d}'" for d in sorted(dates))
    query      = f"SELECT COUNT(*) FROM {table} WHERE date IN ({dates_list})"
    existing   = pd.read_sql(query, con=engine).iloc[0, 0]
    if existing:
        print(
            f'    ℹ {table}: в БД уже {existing:,} строк за эти даты '
            f'— дубли будут пропущены (ON CONFLICT DO NOTHING)'
        )


# ═════════════════════════════════════════════════════════════════════════════
# Главная функция
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    total_start = time.perf_counter()
    print('=' * 62)
    print('  daily_update.py — ежедневное обновление данных')
    print('=' * 62)

    # ── Подключение ───────────────────────────────────────────────────────
    print('\n[1/5] Подключение к БД...')
    engine        = get_engine()
    psycopg2_conn = get_psycopg2_conn()

    # ── Шаг 2: чтение CSV, загрузка dim_product, валидация ───────────────
    # dim_product берём из БД (он уже загружен через load_history.py) —
    # validate_fact_sales и validate_fact_inventory проверяют по нему
    # product_id и определяют формат (физический / цифровой).
    print('\n[2/5] Чтение CSV и валидация...')

    print('  dim_product (из БД)')
    dim_product = load_dim_product(engine)

    reports: list[dict] = []

    print('  fact_sales (update)')
    raw_sales              = read_csv('sales_update.csv')
    clean_sales, rep_sales = validate_fact_sales(raw_sales, dim_product)
    clean_sales            = cast_types(clean_sales, 'fact_sales')
    reports.append(rep_sales)

    print('  fact_inventory (update)')
    raw_inv              = read_csv('inventory_update.csv')
    clean_inv, rep_inv   = validate_fact_inventory(raw_inv, dim_product)
    clean_inv            = cast_types(clean_inv, 'fact_inventory')
    reports.append(rep_inv)

    # ── Шаг 3: отчёт о качестве ──────────────────────────────────────────
    # Сохраняем ДО загрузки в БД: даже если вставка упадёт,
    # отчёт об аномалиях останется на диске для анализа.
    print('\n[3/5] Сохранение отчёта о качестве данных...')
    save_report(reports)

    # ── Шаг 4: вставка в БД ──────────────────────────────────────────────
    print('\n[4/5] Вставка новых строк в PostgreSQL...')

    # Информируем о возможных дублях (не блокирует)
    _check_update_date(clean_sales, 'fact_sales',     engine)
    _check_update_date(clean_inv,   'fact_inventory', engine)

    try:
        inserted_sales = insert_rows(clean_sales, 'fact_sales',     psycopg2_conn)
        inserted_inv   = insert_rows(clean_inv,   'fact_inventory', psycopg2_conn)
    except Exception as exc:
        psycopg2_conn.rollback()
        print(f'\n  ✗ Ошибка вставки: {exc}')
        print('  Транзакция отменена, БД не изменена.')
        raise

    # ── Шаг 5: витрины ───────────────────────────────────────────────────
    # Пересчитываем только если реально что-то вставлено:
    # если все строки оказались дублями — витрины не изменятся,
    # но пересчёт всё равно запускается для согласованности.
    print('\n[5/5] Пересчёт витрин данных...')
    refresh_all_marts(psycopg2_conn)

    # ── Итог ─────────────────────────────────────────────────────────────
    psycopg2_conn.close()
    total_elapsed = time.perf_counter() - total_start

    total_rejected = sum(r['rows_rejected'] for r in reports)
    total_fixed    = sum(r['rows_fixed']    for r in reports)
    total_warnings = sum(r['warnings']      for r in reports)

    print('\n' + '=' * 62)
    print(f'  Вставлено строк продаж:    {inserted_sales:>8,}')
    print(f'  Вставлено строк остатков:  {inserted_inv:>8,}')
    print(f'  Отклонено при валидации:   {total_rejected:>8,}')
    print(f'  Исправлено при валидации:  {total_fixed:>8,}')
    print(f'  Предупреждений валидации:  {total_warnings:>8,}')
    print(f'  Время выполнения:          {total_elapsed:>7.1f}s')
    print('=' * 62)


if __name__ == '__main__':
    main()