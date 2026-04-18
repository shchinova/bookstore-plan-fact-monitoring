"""
scripts/refresh_all_marts.py

Пересчитывает все витрины данных в правильном порядке.

Используется в:
    load_history.py  — после первичной загрузки данных
    daily_update.py  — после каждого ежедневного обновления

ВАЖНО: psql-команда \i не работает при запуске SQL через psycopg2 или
sqlalchemy, поэтому refresh_all_marts.sql содержит только комментарии.
Этот Python-скрипт читает каждый файл и исполняет его через psycopg2.

Запуск напрямую:
    python scripts/refresh_all_marts.py

Или из Python-кода:
    from refresh_all_marts import refresh_all_marts
    refresh_all_marts(conn)
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import psycopg2

# Порядок выполнения фиксирован: витрины с зависимостями от fact_sales
# идут первыми, независимые агрегаты — последними.
MART_FILES = [
    "01_mart_daily_pulse.sql",
    "02_mart_plan_fact.sql",
    "03_mart_inventory_alerts.sql",
    "04_mart_sales_trends.sql",
    "05_mart_abc.sql",
    "06_mart_margin.sql",
    "07_mart_subscriptions.sql",
]

# Директория с SQL-файлами витрин (относительно этого скрипта)
MARTS_DIR = Path(__file__).parent


def refresh_all_marts(conn: psycopg2.extensions.connection) -> None:
    """
    Последовательно исполняет все SQL-файлы витрин через переданное соединение.

    Каждый файл выполняется в отдельной транзакции: при ошибке в одном файле
    остальные витрины не затрагиваются, а в лог выводится подробное сообщение.

    Args:
        conn: открытое соединение psycopg2 с базой bookstore_ods.
    """
    print(f"\n{'='*60}")
    print("Обновление витрин данных")
    print(f"{'='*60}")

    total_start = time.perf_counter()
    success_count = 0
    error_count = 0

    for filename in MART_FILES:
        sql_path = MARTS_DIR / filename
        if not sql_path.exists():
            print(f"  [SKIP]  {filename} — файл не найден")
            error_count += 1
            continue

        sql_text = sql_path.read_text(encoding="utf-8")
        print(f"  Запуск: {filename} ...", end=" ", flush=True)
        t0 = time.perf_counter()

        try:
            with conn.cursor() as cur:
                cur.execute(sql_text)
            conn.commit()
            elapsed = time.perf_counter() - t0
            print(f"OK ({elapsed:.1f}s)")
            success_count += 1
        except Exception as exc:
            conn.rollback()
            elapsed = time.perf_counter() - t0
            print(f"ОШИБКА ({elapsed:.1f}s)")
            print(f"         {exc}")
            error_count += 1

    total_elapsed = time.perf_counter() - total_start
    print(f"{'='*60}")
    print(
        f"Готово: {success_count} витрин обновлено, "
        f"{error_count} ошибок — {total_elapsed:.1f}s"
    )
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# Точка входа для прямого запуска скрипта
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Обновить все витрины данных")
    parser.add_argument("--host",   default=os.getenv("DB_HOST",     "localhost"))
    parser.add_argument("--port",   default=os.getenv("DB_PORT",     "5432"))
    parser.add_argument("--dbname", default=os.getenv("DB_NAME",     "bookstore_ods"))
    parser.add_argument("--user",   default=os.getenv("DB_USER",     "postgres"))
    parser.add_argument("--password", default=os.getenv("DB_PASSWORD", ""))
    args = parser.parse_args()

    conn = psycopg2.connect(
        host=args.host,
        port=args.port,
        dbname=args.dbname,
        user=args.user,
        password=args.password,
    )
    try:
        refresh_all_marts(conn)
    finally:
        conn.close()