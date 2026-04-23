"""
scripts/alerts.py

Ежедневные email-уведомления об остатках.

Порядок работы:
    1. Читает настройки из scripts/alerts_config.yml
    2. Подключается к PostgreSQL и читает 03_mart_inventory_alerts
    3. Если товаров ниже порога нет — завершает работу без отправки
    4. Формирует HTML-письмо с таблицей товаров, сгруппированных по уровню тревоги
    5. Отправляет письмо каждому получателю из конфига, фильтруя по его roles
    6. Записывает результат в лог-файл reports/alerts_log.jsonl

Запуск из корня проекта:
    python scripts/alerts.py

Для запуска по расписанию (Windows Task Scheduler):
    Программа:  python
    Аргументы:  scripts/alerts.py
    Папка:      C:\\path\\to\\bookstore-plan-fact-monitoring

Переменные окружения (задаются в .env):
    DB_HOST        — хост PostgreSQL        (по умолчанию: localhost)
    DB_PORT        — порт                   (по умолчанию: 5432)
    DB_NAME        — имя базы данных        (по умолчанию: bookstore_ods)
    DB_USER        — пользователь           (по умолчанию: postgres)
    DB_PASSWORD    — пароль
    ALERT_SENDER   — Gmail-адрес отправителя  (например: bot@gmail.com)
    ALERT_PASSWORD — пароль приложения Gmail  (App Password, 16 символов)

Зависимости (только стандартная библиотека + уже установленные пакеты):
    pip install pandas sqlalchemy psycopg2-binary python-dotenv pyyaml
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import sys
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# ─── пути ────────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / 'scripts' / 'alerts_config.yml'
REPORTS_DIR = ROOT / 'reports'
LOG_FILE    = REPORTS_DIR / 'alerts_log.jsonl'

sys.path.insert(0, str(ROOT / 'scripts'))

# ─── логирование в консоль ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
log = logging.getLogger(__name__)

# Порядок уровней по убыванию серьёзности
LEVEL_ORDER = ['critical', 'urgent', 'warning']

# Цвета для HTML-таблицы
LEVEL_STYLE: dict[str, dict[str, str]] = {
    'critical': {'bg': '#FFDEDE', 'badge_bg': '#C0392B', 'badge_fg': '#FFFFFF', 'label': '🔴 Critical'},
    'urgent':   {'bg': '#FFE8CC', 'badge_bg': '#E67E22', 'badge_fg': '#FFFFFF', 'label': '🟠 Urgent'},
    'warning':  {'bg': '#FFFBCC', 'badge_bg': '#F1C40F', 'badge_fg': '#333333', 'label': '🟡 Warning'},
    'ok':       {'bg': '#FFFFFF', 'badge_bg': '#27AE60', 'badge_fg': '#FFFFFF', 'label': '🟢 OK'},
}


# ═════════════════════════════════════════════════════════════════════════════
# 1. Конфигурация
# ═════════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    """Читает alerts_config.yml."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f'Файл конфигурации не найден: {CONFIG_PATH}\n'
            'Скопируйте alerts_config.yml в scripts/ и заполните получателей.'
        )
    with CONFIG_PATH.open(encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    log.info('Конфиг загружен: %s', CONFIG_PATH.name)
    return cfg


# ═════════════════════════════════════════════════════════════════════════════
# 2. Подключение к БД
# ═════════════════════════════════════════════════════════════════════════════

def get_engine():
    load_dotenv(ROOT / '.env')
    p = {
        'host':     os.getenv('DB_HOST',     'localhost'),
        'port':     os.getenv('DB_PORT',     '5432'),
        'dbname':   os.getenv('DB_NAME',     'bookstore_ods'),
        'user':     os.getenv('DB_USER',     'postgres'),
        'password': os.getenv('DB_PASSWORD', ''),
    }
    url = (
        f"postgresql+psycopg2://{p['user']}:{p['password']}"
        f"@{p['host']}:{p['port']}/{p['dbname']}"
    )
    engine = create_engine(url, echo=False)
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))
    log.info('БД: %s@%s:%s', p['dbname'], p['host'], p['port'])
    return engine


# ═════════════════════════════════════════════════════════════════════════════
# 3. Чтение витрины остатков
# ═════════════════════════════════════════════════════════════════════════════

def fetch_alerts(engine, max_rows: int) -> pd.DataFrame:
    """
    Читает 03_mart_inventory_alerts.
    Возвращает не более max_rows строк, отсортированных по:
      1. Уровень тревоги (critical → urgent → warning)
      2. closing_stock ASC (самые критические — вверху внутри уровня)
    """
    level_order_sql = "CASE alert_level WHEN 'critical' THEN 1 WHEN 'urgent' THEN 2 ELSE 3 END"
    query = f"""
        SELECT
            product_id,
            title,
            format,
            genre,
            publisher,
            closing_stock,
            avg_daily_sales_30d,
            days_until_stockout,
            recommended_order_qty,
            alert_level,
            stock_date
        FROM 03_mart_inventory_alerts
        ORDER BY {level_order_sql}, closing_stock ASC
        LIMIT {max_rows}
    """
    df = pd.read_sql(query, con=engine)
    log.info('03_mart_inventory_alerts: %d строк (лимит %d)', len(df), max_rows)
    return df


# ═════════════════════════════════════════════════════════════════════════════
# 4. Фильтрация по уровню (для конкретного получателя)
# ═════════════════════════════════════════════════════════════════════════════

def _min_level_index(cfg: dict) -> int:
    """
    Индекс минимального уровня тревоги для отправки письма.
    warning=0, urgent=1, critical=2 в порядке убывания серьёзности.
    """
    min_level = cfg.get('behavior', {}).get('min_level_to_send', 'warning')
    try:
        return LEVEL_ORDER.index(min_level)
    except ValueError:
        return 0   # по умолчанию — warning


def filter_for_recipient(df: pd.DataFrame, roles: list[str]) -> pd.DataFrame:
    """Оставляет только строки с уровнями тревоги из списка roles получателя."""
    return df[df['alert_level'].isin(roles)].copy()


# ═════════════════════════════════════════════════════════════════════════════
# 5. Формирование HTML-письма
# ═════════════════════════════════════════════════════════════════════════════

def _level_badge(level: str) -> str:
    s = LEVEL_STYLE.get(level, LEVEL_STYLE['warning'])
    return (
        f'<span style="background:{s["badge_bg"]};color:{s["badge_fg"]};'
        f'padding:2px 8px;border-radius:4px;font-size:12px;font-weight:bold;">'
        f'{s["label"]}</span>'
    )


def _days_cell(days) -> str:
    """Форматирует дни до нуля с цветовой индикацией."""
    if days is None or (isinstance(days, float) and pd.isna(days)):
        return '<td style="text-align:center;color:#999;">—</td>'
    days = int(days)
    if days == 0:
        color = '#C0392B'
    elif days <= 7:
        color = '#E67E22'
    elif days <= 30:
        color = '#F39C12'
    else:
        color = '#27AE60'
    return f'<td style="text-align:center;color:{color};font-weight:bold;">{days}</td>'


def _format_val(v: Any, align: str = 'left') -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return f'<td style="text-align:{align};color:#999;">—</td>'
    return f'<td style="text-align:{align};">{v}</td>'


def _summary_row(level: str, count: int) -> str:
    s = LEVEL_STYLE.get(level, LEVEL_STYLE['warning'])
    return (
        f'<tr style="background:{s["bg"]};">'
        f'<td>{_level_badge(level)}</td>'
        f'<td style="text-align:center;font-weight:bold;">{count}</td>'
        f'</tr>'
    )


def build_html(df: pd.DataFrame, report_date: date, recipient_name: str) -> str:
    """
    Строит HTML-письмо.
    Структура: заголовок → сводная таблица по уровням → детальная таблица.
    """
    counts = {lvl: int((df['alert_level'] == lvl).sum()) for lvl in LEVEL_ORDER}
    total  = len(df)

    # ── Сводка по уровням ──────────────────────────────────────────────
    summary_rows = ''.join(
        _summary_row(lvl, counts[lvl])
        for lvl in LEVEL_ORDER
        if counts[lvl] > 0
    )
    summary_table = f"""
    <table style="border-collapse:collapse;margin-bottom:24px;min-width:260px;">
      <thead>
        <tr style="background:#F5F5F5;">
          <th style="padding:8px 16px;text-align:left;border-bottom:2px solid #DDD;">Уровень</th>
          <th style="padding:8px 16px;text-align:center;border-bottom:2px solid #DDD;">Товаров</th>
        </tr>
      </thead>
      <tbody>
        {summary_rows}
        <tr style="background:#F5F5F5;font-weight:bold;">
          <td style="padding:6px 16px;">Итого</td>
          <td style="text-align:center;padding:6px 16px;">{total}</td>
        </tr>
      </tbody>
    </table>
    """

    # ── Детальная таблица ──────────────────────────────────────────────
    detail_rows = ''
    for _, row in df.iterrows():
        s   = LEVEL_STYLE.get(str(row['alert_level']), LEVEL_STYLE['warning'])
        detail_rows += (
            f'<tr style="background:{s["bg"]};">'
            f'{_format_val(row["title"])}'
            f'{_format_val(row["format"], "center")}'
            f'{_format_val(row["genre"], "center")}'
            f'<td style="text-align:center;font-weight:bold;">{int(row["closing_stock"])}</td>'
            f'{_format_val(None if pd.isna(row["avg_daily_sales_30d"]) else "{:.1f}".format(float(row["avg_daily_sales_30d"])), "center")}'
            f'{_days_cell(row["days_until_stockout"])}'
            f'{_format_val(int(row["recommended_order_qty"]) if not pd.isna(row["recommended_order_qty"]) else None, "center")}'
            f'<td style="text-align:center;">{_level_badge(str(row["alert_level"]))}</td>'
            f'</tr>\n'
        )

    detail_table = f"""
    <table style="border-collapse:collapse;width:100%;font-size:13px;">
      <thead>
        <tr style="background:#37474F;color:#FFFFFF;">
          <th style="padding:8px 12px;text-align:left;">Название</th>
          <th style="padding:8px 12px;text-align:center;">Формат</th>
          <th style="padding:8px 12px;text-align:center;">Жанр</th>
          <th style="padding:8px 12px;text-align:center;">Остаток</th>
          <th style="padding:8px 12px;text-align:center;">Прод./день</th>
          <th style="padding:8px 12px;text-align:center;">Дней до 0</th>
          <th style="padding:8px 12px;text-align:center;">Рекомендуемый заказ</th>
          <th style="padding:8px 12px;text-align:center;">Уровень</th>
        </tr>
      </thead>
      <tbody>
        {detail_rows}
      </tbody>
    </table>
    """

    # ── Итоговый HTML ──────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Уведомление об остатках</title>
</head>
<body style="font-family:Arial,sans-serif;font-size:14px;color:#333;max-width:960px;margin:0 auto;padding:24px;">

  <h2 style="color:#C0392B;margin-bottom:4px;">⚠ Уведомление об остатках</h2>
  <p style="color:#666;margin-top:0;">
    {recipient_name} &nbsp;|&nbsp; {report_date.strftime('%d.%m.%Y')}
  </p>

  <h3 style="margin-top:28px;margin-bottom:8px;">Сводка по уровням тревоги</h3>
  {summary_table}

  <h3 style="margin-top:28px;margin-bottom:8px;">Товары ниже порога остатка</h3>
  {detail_table}

  <p style="margin-top:32px;font-size:12px;color:#999;border-top:1px solid #EEE;padding-top:12px;">
    Сформировано автоматически · scripts/alerts.py ·
    Витрина: 03_mart_inventory_alerts · {datetime.now().strftime('%Y-%m-%d %H:%M')}
  </p>

</body>
</html>"""


# ═════════════════════════════════════════════════════════════════════════════
# 6. Тема письма
# ═════════════════════════════════════════════════════════════════════════════

def build_subject(cfg: dict, df: pd.DataFrame, report_date: date) -> str:
    """
    Пример: [Bookstore] ⚠ Низкие остатки: 2 critical, 5 urgent, 11 warning — 15.04.2026
    """
    prefix  = cfg.get('email', {}).get('subject_prefix', '[Bookstore]')
    counts  = {lvl: int((df['alert_level'] == lvl).sum()) for lvl in LEVEL_ORDER}
    parts   = [f"{counts[lvl]} {lvl}" for lvl in LEVEL_ORDER if counts[lvl] > 0]
    summary = ', '.join(parts)
    return f"{prefix} ⚠ Низкие остатки: {summary} — {report_date.strftime('%d.%m.%Y')}"


# ═════════════════════════════════════════════════════════════════════════════
# 7. Отправка письма
# ═════════════════════════════════════════════════════════════════════════════

def send_email(
    sender:    str,
    password:  str,
    recipient: dict,
    subject:   str,
    html_body: str,
    smtp_cfg:  dict,
) -> None:
    """
    Отправляет одно HTML-письмо через Gmail SMTP (STARTTLS, порт 587).
    Для SSL (порт 465) используйте smtplib.SMTP_SSL вместо SMTP.
    """
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = sender
    msg['To']      = f"{recipient['name']} <{recipient['email']}>"
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    host     = smtp_cfg.get('host', 'smtp.gmail.com')
    port     = int(smtp_cfg.get('port', 587))
    use_tls  = smtp_cfg.get('use_tls', True)

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.ehlo()
        if use_tls:
            server.starttls()
            server.ehlo()
        server.login(sender, password)
        server.sendmail(sender, recipient['email'], msg.as_string())


# ═════════════════════════════════════════════════════════════════════════════
# 8. Лог отправок
# ═════════════════════════════════════════════════════════════════════════════

def write_log(
    report_date: date,
    recipient:   dict,
    df_sent:     pd.DataFrame,
    success:     bool,
    error:       str | None = None,
) -> None:
    """
    Дописывает одну JSON-строку в reports/alerts_log.jsonl.
    JSONL (JSON Lines) — каждая строка файла — валидный JSON-объект.
    Файл никогда не перезаписывается: каждый запуск дописывает в конец.
    """
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    entry = {
        'ts':           datetime.now().isoformat(timespec='seconds'),
        'report_date':  str(report_date),
        'recipient':    recipient['email'],
        'rows_sent':    len(df_sent),
        'critical':     int((df_sent['alert_level'] == 'critical').sum()),
        'urgent':       int((df_sent['alert_level'] == 'urgent').sum()),
        'warning':      int((df_sent['alert_level'] == 'warning').sum()),
        'success':      success,
        'error':        error,
    }
    with LOG_FILE.open('a', encoding='utf-8') as f:
        f.write(json.dumps(entry, ensure_ascii=False) + '\n')


# ═════════════════════════════════════════════════════════════════════════════
# 9. Главная функция
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    log.info('=' * 56)
    log.info('alerts.py — уведомления об остатках')
    log.info('=' * 56)

    # ── Конфиг и подключение ──────────────────────────────────────────
    cfg    = load_config()
    engine = get_engine()

    load_dotenv(ROOT / '.env')
    sender   = os.getenv('ALERT_SENDER',   '')
    password = os.getenv('ALERT_PASSWORD', '')
    if not sender or not password:
        log.error(
            'Не заданы ALERT_SENDER и/или ALERT_PASSWORD в .env\n'
            'Добавьте:\n  ALERT_SENDER=bot@gmail.com\n  ALERT_PASSWORD=xxxx xxxx xxxx xxxx'
        )
        sys.exit(1)

    # ── Читаем витрину ────────────────────────────────────────────────
    max_rows    = cfg.get('behavior', {}).get('max_rows_in_email', 50)
    df_all      = fetch_alerts(engine, max_rows)
    report_date = date.today()

    if df_all.empty:
        log.info('Товаров ниже порога нет — письма не отправляются.')
        return

    # Проверяем, достаточно ли серьёзный уровень для отправки
    min_idx         = _min_level_index(cfg)
    relevant_levels = set(LEVEL_ORDER[: min_idx + 1])
    if not df_all['alert_level'].isin(relevant_levels).any():
        log.info(
            'Нет товаров с уровнем >= %s — письма не отправляются.',
            cfg['behavior']['min_level_to_send'],
        )
        return

    smtp_cfg = cfg.get('smtp', {})
    subject  = build_subject(cfg, df_all, report_date)

    # ── Рассылка ──────────────────────────────────────────────────────
    recipients = cfg.get('recipients', [])
    if not recipients:
        log.warning('Список получателей пуст — проверь alerts_config.yml')
        return

    sent_count  = 0
    error_count = 0

    for recipient in recipients:
        email = recipient.get('email', '')
        name  = recipient.get('name',  email)
        roles = recipient.get('roles', LEVEL_ORDER)

        # Фильтруем данные по ролям получателя
        df_recipient = filter_for_recipient(df_all, roles)
        if df_recipient.empty:
            log.info('  %s (%s): нет товаров для его уровней %s — пропускаем',
                     name, email, roles)
            continue

        html_body = build_html(df_recipient, report_date, name)

        try:
            send_email(sender, password, recipient, subject, html_body, smtp_cfg)
            log.info('  ✓ Отправлено → %s (%s): %d товаров',
                     name, email, len(df_recipient))
            write_log(report_date, recipient, df_recipient, success=True)
            sent_count += 1
        except Exception as exc:
            log.error('  ✗ Ошибка отправки → %s (%s): %s', name, email, exc)
            write_log(report_date, recipient, df_recipient, success=False, error=str(exc))
            error_count += 1

    # ── Итог ──────────────────────────────────────────────────────────
    log.info('─' * 56)
    log.info(
        'Готово: %d писем отправлено, %d ошибок. Лог: %s',
        sent_count, error_count, LOG_FILE.relative_to(ROOT),
    )
    if error_count:
        sys.exit(1)   # ненулевой код — сигнал для Task Scheduler об ошибке


if __name__ == '__main__':
    main()