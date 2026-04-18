"""
scripts/load_history.py

Первичная загрузка исторических данных в PostgreSQL.

Порядок работы:
    1. Создаёт таблицы через sql/schema.sql (CREATE TABLE IF NOT EXISTS)
    2. Читает файлы из data/raw/ (кроме sales_update.csv и inventory_update.csv)
    3. Проверяет и очищает данные через scripts/validate.py
    4. Сохраняет отчёт о качестве данных в reports/
    5. Загружает очищенные данные в PostgreSQL
    6. Создаёт витрины через sql/marts/*.sql

Запуск из корня проекта:
    python scripts/load_history.py

Переменные окружения (задаются в файле .env в корне проекта):
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
from datetime import datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# ─── пути ────────────────────────────────────────────────────────────────────
# Скрипт лежит в scripts/, корень проекта — на уровень выше
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))   # чтобы найти validate.py

from validate import (
    validate_dim_date,
    validate_dim_product,
    validate_fact_plan,
    validate_fact_sales,
    validate_fact_inventory,
    build_report_md,
)

RAW_DIR     = ROOT / 'data' / 'raw'
REPORTS_DIR = ROOT / 'reports'
SQL_DIR     = ROOT / 'sql'

# Файлы-обновления: исключаем из первичной загрузки
SKIP_FILES = {'sales_update.csv', 'inventory_update.csv'}

# Единая точка входа для витрин — порядок задаётся внутри этого файла
REFRESH_MARTS = SQL_DIR / 'marts' / 'refresh_all_marts.sql'


# ═════════════════════════════════════════════════════════════════════════════
# 1. ПОДКЛЮЧЕНИЕ К БД
# ═════════════════════════════════════════════════════════════════════════════

def get_engine():
    """Создаёт SQLAlchemy engine из переменных окружения или .env файла."""
    load_dotenv(ROOT / '.env')

    host     = os.getenv('DB_HOST',     'localhost')
    port     = os.getenv('DB_PORT',     '5432')
    dbname   = os.getenv('DB_NAME',     'bookstore_ods')
    user     = os.getenv('DB_USER',     'postgres')
    password = os.getenv('DB_PASSWORD', '')

    url = f'postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}'
    engine = create_engine(url, echo=False)

    # Проверяем соединение
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))

    print(f'✓ Подключение установлено: {dbname}@{host}:{port}')
    return engine


# ═════════════════════════════════════════════════════════════════════════════
# 2. DDL: создание таблиц
# ═════════════════════════════════════════════════════════════════════════════

def create_schema(engine) -> None:
    """
    Выполняет sql/schema.sql — создаёт таблицы если их нет.
    Разбивает файл на отдельные выражения, т.к. SQLAlchemy не принимает
    несколько выражений в одном execute().
    """
    schema_path = SQL_DIR / 'schema.sql'
    print(f'\n[1/6] Применяем схему из {schema_path.name}...')

    sql = schema_path.read_text(encoding='utf-8')

    # Убираем комментарии-строки (-- ...) и разбиваем по ';'
    lines = [
        line for line in sql.splitlines()
        if not line.strip().startswith('--')
    ]
    statements = [
        s.strip()
        for s in '\n'.join(lines).split(';')
        if s.strip()
    ]

    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))

    print(f'  ✓ Схема применена ({len(statements)} выражений)')


# ═════════════════════════════════════════════════════════════════════════════
# 3. ЧТЕНИЕ CSV
# ═════════════════════════════════════════════════════════════════════════════

def read_csv(filename: str) -> pd.DataFrame:
    """
    Читает CSV из data/raw/ с обработкой типичных проблем:
      - пробует UTF-8, при ошибке — cp1251 (аномалия G-02)
      - строки с неверным разделителем ';' помечаются, но читаются
        (pandas их не распознает корректно — они попадут в отчёт через validate)
    """
    path = RAW_DIR / filename
    try:
        df = pd.read_csv(path, encoding='utf-8', dtype=str, on_bad_lines='warn')
    except UnicodeDecodeError:
        print(f'  ⚠ {filename}: UTF-8 не распознан, пробуем cp1251')
        df = pd.read_csv(path, encoding='cp1251', dtype=str, on_bad_lines='warn')

    print(f'  ✓ {filename}: прочитано {len(df):,} строк')
    return df


# ═════════════════════════════════════════════════════════════════════════════
# 4. ЗАГРУЗКА ДАТАФРЕЙМА В БД
# ═════════════════════════════════════════════════════════════════════════════

def load_to_db(df: pd.DataFrame, table: str, engine, if_exists: str = 'append') -> None:
    """
    Загружает датафрейм в указанную таблицу PostgreSQL.

    if_exists='append' — добавляет строки к существующим данным.
    Таблица уже создана через schema.sql, поэтому 'replace' не используем
    (он пересоздал бы таблицу без индексов и CHECK-ограничений).
    """
    df.to_sql(
        name=table,
        con=engine,
        if_exists=if_exists,
        index=False,
        method='multi',   # батч-вставка — быстрее чем построчно
        chunksize=5000,
    )


# ═════════════════════════════════════════════════════════════════════════════
# 5. ВЫПОЛНЕНИЕ SQL-СКРИПТОВ ВИТРИН
# ═════════════════════════════════════════════════════════════════════════════

def _parse_mart_order(refresh_sql_path: Path) -> list[Path]:
    """
    Читает refresh_all_marts.sql и извлекает пути к скриптам витрин
    из строк вида:  \\i sql/marts/01_mart_daily_pulse.sql

    Так порядок выполнения задаётся только в refresh_all_marts.sql —
    Python-код его не дублирует.
    """
    scripts = []
    for line in refresh_sql_path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.startswith(r'\i'):
            # '\i sql/marts/01_mart_daily_pulse.sql' → относительный путь
            relative = line.split(maxsplit=1)[1]
            scripts.append(ROOT / relative)
    return scripts


def _exec_sql_file(engine, script_path: Path) -> None:
    """
    Выполняет один SQL-файл через SQLAlchemy.
    Разбивает содержимое по ';', пропуская строки-комментарии.
    """
    sql = script_path.read_text(encoding='utf-8')
    lines = [
        line for line in sql.splitlines()
        if not line.strip().startswith('--')
    ]
    statements = [
        s.strip()
        for s in '\n'.join(lines).split(';')
        if s.strip()
    ]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def run_mart_scripts(engine) -> None:
    """
    Выполняет скрипты витрин в порядке, заданном в refresh_all_marts.sql.
    Каждый скрипт: DROP TABLE IF EXISTS → CREATE TABLE AS → CREATE INDEX.
    """
    print('\n[6/6] Создаём витрины данных...')

    mart_scripts = _parse_mart_order(REFRESH_MARTS)

    if not mart_scripts:
        raise RuntimeError(
            f'Не найдено ни одного \\i-пути в {REFRESH_MARTS.name}. '
            'Проверь формат файла.'
        )

    for script_path in mart_scripts:
        print(f'  → {script_path.name}', end='', flush=True)
        _exec_sql_file(engine, script_path)
        print(' ✓')

    print(f'  Все витрины созданы ({len(mart_scripts)} скриптов).')


# ═════════════════════════════════════════════════════════════════════════════
# 6. СОХРАНЕНИЕ ОТЧЁТА
# ═════════════════════════════════════════════════════════════════════════════

def save_report(reports: list[dict]) -> Path:
    """Сохраняет Markdown-отчёт в reports/ с временнóй меткой в имени файла."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M')
    report_path = REPORTS_DIR / f'quality_report_{timestamp}.md'
    report_md = build_report_md(reports, script_name='load_history.py')
    report_path.write_text(report_md, encoding='utf-8')
    print(f'  ✓ Отчёт сохранён: {report_path.relative_to(ROOT)}')
    return report_path


# ═════════════════════════════════════════════════════════════════════════════
# 7. ГЛАВНАЯ ФУНКЦИЯ
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    print('=' * 60)
    print('  load_history.py — первичная загрузка данных')
    print('=' * 60)

    # ── Подключение ───────────────────────────────────────────────
    engine = get_engine()

    # ── Шаг 1: схема БД ──────────────────────────────────────────
    create_schema(engine)

    # ── Шаг 2: чтение и валидация ─────────────────────────────────
    print('\n[2-4/6] Читаем, проверяем и загружаем данные...')

    reports = []   # отчёты validate.py по каждой таблице

    # ── dim_date ──────────────────────────────────────────────────
    print('\n  dim_date')
    raw_date = read_csv('dim_date.csv')
    clean_date, report_date = validate_dim_date(raw_date)
    reports.append(report_date)

    # Очищаем таблицу перед загрузкой справочников
    # (идемпотентность: повторный запуск не задвоит данные)
    with engine.begin() as conn:
        conn.execute(text('TRUNCATE TABLE dim_date CASCADE'))
    load_to_db(clean_date, 'dim_date', engine)
    print(f'  → загружено в БД: {len(clean_date):,} строк')

    # ── dim_product ───────────────────────────────────────────────
    print('\n  dim_product')
    raw_product = read_csv('dim_product.csv')
    clean_product, report_product = validate_dim_product(raw_product)
    reports.append(report_product)

    with engine.begin() as conn:
        conn.execute(text('TRUNCATE TABLE dim_product CASCADE'))
    load_to_db(clean_product, 'dim_product', engine)
    print(f'  → загружено в БД: {len(clean_product):,} строк')

    # ── fact_plan ─────────────────────────────────────────────────
    print('\n  fact_plan')
    raw_plan = read_csv('fact_plan.csv')
    clean_plan, report_plan = validate_fact_plan(raw_plan)
    reports.append(report_plan)

    with engine.begin() as conn:
        conn.execute(text('TRUNCATE TABLE fact_plan'))
    load_to_db(clean_plan, 'fact_plan', engine)
    print(f'  → загружено в БД: {len(clean_plan):,} строк')

    # ── fact_sales (history) ──────────────────────────────────────
    print('\n  fact_sales (history)')
    raw_sales = read_csv('sales_history.csv')
    clean_sales, report_sales = validate_fact_sales(raw_sales, clean_product)
    reports.append(report_sales)

    with engine.begin() as conn:
        conn.execute(text('TRUNCATE TABLE fact_sales'))
    load_to_db(clean_sales, 'fact_sales', engine)
    print(f'  → загружено в БД: {len(clean_sales):,} строк')

    # ── fact_inventory (history) ──────────────────────────────────
    print('\n  fact_inventory (history)')
    raw_inv = read_csv('inventory_history.csv')
    clean_inv, report_inv = validate_fact_inventory(raw_inv, clean_product)
    reports.append(report_inv)

    with engine.begin() as conn:
        conn.execute(text('TRUNCATE TABLE fact_inventory'))
    load_to_db(clean_inv, 'fact_inventory', engine)
    print(f'  → загружено в БД: {len(clean_inv):,} строк')

    # ── Шаг 4: сохраняем отчёт ───────────────────────────────────
    print('\n[5/6] Сохраняем отчёт о качестве данных...')
    save_report(reports)

    # ── Шаг 5: витрины ────────────────────────────────────────────
    run_mart_scripts(engine)

    # ── Итог ──────────────────────────────────────────────────────
    print('\n' + '=' * 60)
    total_loaded = sum(r['rows_loaded'] for r in reports)
    total_rejected = sum(r['rows_rejected'] for r in reports)
    print(f'  Загружено строк: {total_loaded:,}')
    print(f'  Отклонено строк: {total_rejected:,}')
    print(f'  База данных готова к работе.')
    print('=' * 60)


if __name__ == '__main__':
    main()