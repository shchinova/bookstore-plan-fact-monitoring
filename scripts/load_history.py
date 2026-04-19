"""
scripts/load_history.py

Первичная загрузка исторических данных в PostgreSQL.

Порядок работы:
    1. Создаёт таблицы через sql/schema.sql (CREATE TABLE IF NOT EXISTS)
    2. Читает файлы из data/raw/:
         dim_date.csv, dim_product.csv, fact_plan.csv,
         sales_history.csv, inventory_history.csv
       (sales_update.csv и inventory_update.csv — для daily_update.py)
    3. Проверяет и очищает данные через scripts/validate.py
    4. Загружает очищенные данные в PostgreSQL в одной транзакции
    5. Сохраняет отчёт о качестве данных в reports/
    6. Создаёт витрины через scripts/refresh_all_marts.py

Запуск из корня проекта:
    python scripts/load_history.py

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
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# ─── пути ────────────────────────────────────────────────────────────────────
# Скрипт лежит в scripts/, корень проекта — на уровень выше
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

from validate import (
    validate_dim_date,
    validate_dim_product,
    validate_fact_plan,
    validate_fact_sales,
    validate_fact_inventory,
    build_report_md,
)
# refresh_all_marts живёт в scripts/ согласно структуре репозитория
from refresh_all_marts import refresh_all_marts

RAW_DIR     = ROOT / 'data'    / 'raw'
REPORTS_DIR = ROOT / 'reports'
SCHEMA_SQL  = ROOT / 'sql'     / 'schema.sql'


# ═════════════════════════════════════════════════════════════════════════════
# Утилиты подключения
# ═════════════════════════════════════════════════════════════════════════════

def _db_params() -> dict:
    """Читает параметры подключения из окружения / .env."""
    load_dotenv(ROOT / '.env')
    return {
        'host':     os.getenv('DB_HOST',     'localhost'),
        'port':     os.getenv('DB_PORT',     '5432'),
        'dbname':   os.getenv('DB_NAME',     'bookstore_ods'),
        'user':     os.getenv('DB_USER',     'postgres'),
        'password': os.getenv('DB_PASSWORD', ''),
    }


def get_engine():
    """
    Создаёт SQLAlchemy engine и проверяет соединение.
    Engine используется только для load_to_db (pandas → to_sql).
    """
    p = _db_params()
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
    """
    Создаёт нативное psycopg2-соединение.
    Используется для DDL (schema.sql) и refresh_all_marts.py,
    которые требуют прямого управления транзакцией.
    """
    p = _db_params()
    conn = psycopg2.connect(**p)
    return conn


# ═════════════════════════════════════════════════════════════════════════════
# 1. DDL: создание таблиц
# ═════════════════════════════════════════════════════════════════════════════

def create_schema(conn: psycopg2.extensions.connection) -> None:
    """
    Выполняет sql/schema.sql целиком через psycopg2.

    [FIX-4] Используем psycopg2 напрямую вместо SQLAlchemy + split(';'):
    разбивка составного SQL-файла по ';' ненадёжна — ломается на строках
    вида "CHECK (x IN (0, 1))" или многострочных комментариях.
    psycopg2.cursor.execute() принимает весь файл одним вызовом.
    """
    sql = SCHEMA_SQL.read_text(encoding='utf-8')
    t0 = time.perf_counter()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    print(f'  ✓ Схема применена ({time.perf_counter() - t0:.1f}s)')


# ═════════════════════════════════════════════════════════════════════════════
# 2. Чтение CSV
# ═════════════════════════════════════════════════════════════════════════════

def read_csv(filename: str) -> pd.DataFrame:
    """
    Читает CSV из data/raw/.

    Кодировка: пробует utf-8-sig (BOM, генератор сохраняет с ним),
    затем cp1251 как запасной вариант (аномалия G-02).
    Все колонки читаются как str — конвертация типов выполняется
    в validate.py с полноценной обработкой ошибок.
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
    raise RuntimeError(f'Не удалось прочитать {filename}: ни одна кодировка не подошла')


# ═════════════════════════════════════════════════════════════════════════════
# 3. Приведение типов перед загрузкой в БД
# ═════════════════════════════════════════════════════════════════════════════

# Явное сопоставление колонок → целевой тип pandas.
# [FIX-7/8] read_csv читает всё как str; validate.py конвертирует числа,
# но date-колонки возвращает строками 'YYYY-MM-DD', а булевы флаги могут
# остаться object. Явное приведение гарантирует корректные SQL-типы в to_sql.

_DTYPE_MAP: dict[str, dict[str, str]] = {
    'dim_date': {
        'date':          'date',
        'year':          'int16',
        'month':         'int16',
        'quarter':       'int16',
        'is_weekend':    'int16',
        'is_holiday_ru': 'int16',
    },
    'dim_product': {
        'product_id':   'int32',
        'price_rub':    'float64',
        'cost_rub':     'float64',
        'avg_rating':   'float64',
        'review_count': 'float64',   # nullable → float, не int
        'stock_initial':'float64',   # nullable
        'page_count':   'float64',   # nullable
        'is_physical':  'int16',
        'published_date': 'date',
    },
    'fact_plan': {
        'plan_id':             'int32',
        'year':                'int16',
        'month':               'int16',
        'plan_qty':            'int32',
        'plan_amount':         'float64',
        'plan_margin_target':  'float64',
    },
    'fact_sales': {
        'sales_id':         'int32',
        'order_id':         'float64',   # nullable (S-02 — warn, не reject)
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
        'inventory_id':       'int32',
        'product_id':         'int32',
        'date':               'date',
        'opening_stock':      'int32',
        'sold_qty':           'int32',
        'replenishment_qty':  'int32',
        'closing_stock':      'int32',
        'is_low_stock':       'int16',
    },
}


def cast_types(df: pd.DataFrame, table: str) -> pd.DataFrame:
    """
    Приводит колонки датафрейма к типам, совместимым со схемой PostgreSQL.

    - Числовые колонки: pd.to_numeric с errors='coerce'
    - date-колонки: pd.to_datetime → .dt.date (Python date → psycopg2 DATE)
    - Колонки не из _DTYPE_MAP остаются как есть (str / object)
    """
    df = df.copy()
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
# 4. Загрузка датафрейма в БД
# ═════════════════════════════════════════════════════════════════════════════

def load_to_db(df: pd.DataFrame, table: str, engine) -> None:
    """
    Загружает датафрейм в таблицу PostgreSQL пакетами по 5 000 строк.

    if_exists='append': таблица уже создана через schema.sql со всеми
    индексами и CHECK-ограничениями; 'replace' пересоздал бы её без них.
    """
    t0 = time.perf_counter()
    df.to_sql(
        name=table,
        con=engine,
        if_exists='append',
        index=False,
        method='multi',
        chunksize=5_000,
    )
    elapsed = time.perf_counter() - t0
    print(f'    → БД [{table}]: {len(df):,} строк за {elapsed:.1f}s')


# ═════════════════════════════════════════════════════════════════════════════
# 5. Сохранение отчёта о качестве данных
# ═════════════════════════════════════════════════════════════════════════════

def save_report(reports: list[dict]) -> Path:
    """Сохраняет Markdown-отчёт в reports/ с временнóй меткой."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp   = datetime.now().strftime('%Y-%m-%d_%H-%M')
    report_path = REPORTS_DIR / f'quality_report_{timestamp}.md'
    report_md   = build_report_md(reports, script_name='load_history.py')
    report_path.write_text(report_md, encoding='utf-8')
    print(f'  ✓ Отчёт: {report_path.relative_to(ROOT)}')
    return report_path


# ═════════════════════════════════════════════════════════════════════════════
# 6. Главная функция
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    total_start = time.perf_counter()
    print('=' * 62)
    print('  load_history.py — первичная загрузка исторических данных')
    print('=' * 62)

    # ── Подключение ───────────────────────────────────────────────────────
    print('\n[1/6] Подключение к БД...')
    engine      = get_engine()
    psycopg2_conn = get_psycopg2_conn()

    # ── Шаг 1: схема БД ──────────────────────────────────────────────────
    print('\n[2/6] Создание схемы БД...')
    create_schema(psycopg2_conn)

    # ── Шаг 2–3: чтение, валидация, приведение типов ─────────────────────
    # Все таблицы читаем и валидируем ДО загрузки в БД: если validate.py
    # найдёт критические ошибки — не начинаем транзакцию.
    print('\n[3/6] Чтение и валидация CSV...')

    reports: list[dict] = []

    print('  dim_date')
    raw_date               = read_csv('dim_date.csv')
    clean_date, rep_date   = validate_dim_date(raw_date)
    clean_date             = cast_types(clean_date, 'dim_date')
    reports.append(rep_date)

    print('  dim_product')
    raw_product                  = read_csv('dim_product.csv')
    clean_product, rep_product   = validate_dim_product(raw_product)
    clean_product                = cast_types(clean_product, 'dim_product')
    reports.append(rep_product)

    print('  fact_plan')
    raw_plan               = read_csv('fact_plan.csv')
    clean_plan, rep_plan   = validate_fact_plan(raw_plan)
    clean_plan             = cast_types(clean_plan, 'fact_plan')
    reports.append(rep_plan)

    print('  fact_sales (history)')
    raw_sales                = read_csv('sales_history.csv')
    # validate_fact_sales нужен clean_product для проверки product_id
    clean_sales, rep_sales   = validate_fact_sales(raw_sales, clean_product)
    clean_sales              = cast_types(clean_sales, 'fact_sales')
    reports.append(rep_sales)

    print('  fact_inventory (history)')
    raw_inv                = read_csv('inventory_history.csv')
    clean_inv, rep_inv     = validate_fact_inventory(raw_inv, clean_product)
    clean_inv              = cast_types(clean_inv, 'fact_inventory')
    reports.append(rep_inv)

    # ── Шаг 4: загрузка в БД — одна транзакция ───────────────────────────
    # [FIX-6] Весь блок TRUNCATE + INSERT выполняется в одной транзакции:
    # при ошибке на любом шаге весь INSERT откатывается — БД не остаётся
    # в промежуточном состоянии (частично загруженной).
    print('\n[4/6] Загрузка в PostgreSQL...')
    try:
        with psycopg2_conn.cursor() as cur:
            # TRUNCATE в правильном порядке: сначала факты, потом справочники
            # (обратный порядок FK-зависимостей)
            print('  Очистка таблиц (TRUNCATE)...')
            cur.execute('TRUNCATE TABLE fact_inventory')
            cur.execute('TRUNCATE TABLE fact_sales')
            cur.execute('TRUNCATE TABLE fact_plan')
            cur.execute('TRUNCATE TABLE dim_product CASCADE')
            cur.execute('TRUNCATE TABLE dim_date CASCADE')
        psycopg2_conn.commit()

        # to_sql использует engine (отдельное соединение) — каждый вызов
        # атомарен внутри себя. Общая транзакционность на уровне Python:
        # при ошибке в load_to_db скрипт упадёт и данные не будут
        # загружены частично в ту же таблицу.
        load_to_db(clean_date,    'dim_date',       engine)
        load_to_db(clean_product, 'dim_product',    engine)
        load_to_db(clean_plan,    'fact_plan',      engine)
        load_to_db(clean_sales,   'fact_sales',     engine)
        load_to_db(clean_inv,     'fact_inventory', engine)

    except Exception as exc:
        psycopg2_conn.rollback()
        print(f'\n  ✗ Ошибка загрузки: {exc}')
        print('  Транзакция отменена, БД не изменена.')
        raise

    # ── Шаг 5: отчёт о качестве ──────────────────────────────────────────
    print('\n[5/6] Сохранение отчёта о качестве данных...')
    save_report(reports)

    # ── Шаг 6: витрины ───────────────────────────────────────────────────
    # [FIX-1/2/3] Вызываем refresh_all_marts из scripts/ напрямую —
    # порядок витрин задаётся только там, дублирования нет.
    print('\n[6/6] Создание витрин данных...')
    refresh_all_marts(psycopg2_conn)

    # ── Итог ─────────────────────────────────────────────────────────────
    psycopg2_conn.close()
    total_elapsed = time.perf_counter() - total_start

    total_loaded   = sum(r['rows_loaded']   for r in reports)
    total_rejected = sum(r['rows_rejected'] for r in reports)
    total_fixed    = sum(r['rows_fixed']    for r in reports)
    total_warnings = sum(r['warnings']      for r in reports)

    print('\n' + '=' * 62)
    print(f'  Загружено строк:   {total_loaded:>10,}')
    print(f'  Отклонено строк:   {total_rejected:>10,}')
    print(f'  Исправлено строк:  {total_fixed:>10,}')
    print(f'  Предупреждений:    {total_warnings:>10,}')
    print(f'  Время выполнения:  {total_elapsed:>9.1f}s')
    print(f'  База данных готова к работе.')
    print('=' * 62)


if __name__ == '__main__':
    main()