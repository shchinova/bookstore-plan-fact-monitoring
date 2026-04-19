"""
scripts/refresh_all_marts.py

Пересчитывает все витрины данных в правильном порядке.

Используется в:
    load_history.py  — после первичной загрузки данных
    daily_update.py  — после каждого ежедневного обновления

Порядок витрин фиксирован в константе MART_FILES.
sql/marts/refresh_all_marts.sql содержит только комментарии для документации
и ручного запуска через psql — он не парсится этим скриптом.

Запуск напрямую из корня проекта:
    python scripts/refresh_all_marts.py

Или из Python-кода:
    import psycopg2
    from scripts.refresh_all_marts import refresh_all_marts
    conn = psycopg2.connect(...)
    refresh_all_marts(conn)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

# Скрипт лежит в scripts/, корень проекта — на уровень выше
ROOT      = Path(__file__).resolve().parent.parent
MARTS_DIR = ROOT / 'sql' / 'marts'

# Порядок выполнения строго фиксирован:
# mart_daily_pulse и mart_plan_fact опираются на актуальные данные fact_sales,
# поэтому оперативные витрины идут первыми.
MART_FILES = [
    'mart_daily_pulse.sql',
    'mart_plan_fact.sql',
    'mart_inventory_alerts.sql',
    'mart_sales_trends.sql',
    'mart_abc.sql',
    'mart_margin.sql',
    'mart_subscriptions.sql',
]


def refresh_all_marts(conn: psycopg2.extensions.connection) -> None:
    """
    Последовательно исполняет все SQL-файлы витрин через переданное соединение.

    Каждый файл выполняется в отдельной транзакции: при ошибке в одном файле
    остальные витрины не затрагиваются, в лог выводится подробное сообщение.

    Args:
        conn: открытое psycopg2-соединение с базой bookstore_ods.
    """
    print(f"  {'─' * 54}")
    success_count = 0
    error_count   = 0

    for filename in MART_FILES:
        sql_path = MARTS_DIR / filename
        if not sql_path.exists():
            print(f'  [SKIP]  {filename} — файл не найден в {MARTS_DIR}')
            error_count += 1
            continue

        sql_text = sql_path.read_text(encoding='utf-8')
        print(f'  → {filename}', end=' ', flush=True)
        t0 = time.perf_counter()

        try:
            with conn.cursor() as cur:
                cur.execute(sql_text)
            conn.commit()
            elapsed = time.perf_counter() - t0
            print(f'✓ ({elapsed:.1f}s)')
            success_count += 1
        except Exception as exc:
            conn.rollback()
            elapsed = time.perf_counter() - t0
            print(f'✗ ({elapsed:.1f}s)')
            print(f'     Ошибка: {exc}')
            error_count += 1

    print(f"  {'─' * 54}")
    print(
        f'  Витрины: {success_count} обновлено'
        + (f', {error_count} ошибок' if error_count else '')
    )


# ─────────────────────────────────────────────────────────────────────────────
# Точка входа для прямого запуска
# ─────────────────────────────────────────────────────────────────────────────

def _get_conn() -> psycopg2.extensions.connection:
    load_dotenv(ROOT / '.env')
    return psycopg2.connect(
        host     = os.getenv('DB_HOST',     'localhost'),
        port     = os.getenv('DB_PORT',     '5432'),
        dbname   = os.getenv('DB_NAME',     'bookstore_ods'),
        user     = os.getenv('DB_USER',     'postgres'),
        password = os.getenv('DB_PASSWORD', ''),
    )


if __name__ == '__main__':
    conn = _get_conn()
    try:
        print('\nОбновление витрин данных...')
        refresh_all_marts(conn)
    finally:
        conn.close()