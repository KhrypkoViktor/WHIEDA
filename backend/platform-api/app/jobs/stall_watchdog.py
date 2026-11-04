"""Цикл воркера встал — сигнал владельцу и перезапуск контейнера.

Шаги worker_loop идут по очереди и без общего срока, поэтому один зависший await останавливает
все рассылки. 04.11.2026 так ушли без сообщений напоминания эфира ALMIRA и никто об этом не знал
(воркер стоял около двух часов на оборванном соединении с базой).

Сторож — отдельный поток: ему не мешает ни зависший await, ни заблокированный цикл событий.
Базу он не трогает: при зависании она первая под подозрением. Перезапуск делает Docker
(restart: unless-stopped).
"""

from __future__ import annotations

import logging
import os
import threading
import time

import httpx

from app.settings import get_settings
from app.telegram.api_base import telegram_bot_api_url

logger = logging.getLogger(__name__)

# Самый долгий законный шаг много короче: уведомления ограничены DUE_PASS_BUDGET_SEC, очередь и письма
# партнёрам отмечаются на каждой строке (process_pending_outbox, run_nudge), перекодирование идёт фоном.
STALL_LIMIT_SEC = 300.0
CHECK_INTERVAL_SEC = 15.0
ALERT_TIMEOUT_SEC = 10.0
ALERT_WAIT_SEC = 20.0

_last_beat = time.monotonic()


def beat() -> None:
    """Цикл движется: вызывается перед каждым шагом worker_loop и на каждой строке очереди."""
    global _last_beat
    _last_beat = time.monotonic()


def _alert_owner(silent_sec: float) -> None:
    """Только с боевого воркера. В журнале воркера виден лишь текст сообщения (без extra), поэтому причина
    в самом сообщении — без текста исключения: в нём адрес с токеном бота."""
    settings = get_settings()
    if settings.environment != "production":
        return
    owner, token = settings.platform_billing_owner_telegram_id, settings.telegram_bot_token
    if not owner or not token:
        logger.error("worker_stall_alert_failed: no owner id or bot token in settings")
        return
    text = (
        f"Воркер ядра завис: {round(silent_sec / 60)} мин без движения, рассылки и напоминания ботов стояли. "
        "Перезапускаю его. Проверьте, не пропали ли напоминания."
    )
    try:
        response = httpx.post(
            telegram_bot_api_url(token, "sendMessage"),
            json={"chat_id": owner, "text": text},
            timeout=ALERT_TIMEOUT_SEC,
        )
        if response.status_code != 200:
            logger.error("worker_stall_alert_failed: telegram status %s", response.status_code)
    except Exception as exc:
        logger.error("worker_stall_alert_failed: %s", type(exc).__name__)


def check_stall() -> None:
    silent_sec = time.monotonic() - _last_beat
    if silent_sec < STALL_LIMIT_SEC:
        return
    logger.critical("worker_stalled_restarting: silent for %d sec", silent_sec)
    # Сигнал — в своём потоке: при обрыве сети зависнуть может и он (у DNS нет срока), перезапуск важнее.
    alert = threading.Thread(target=_alert_owner, args=(silent_sec,), daemon=True)
    alert.start()
    alert.join(ALERT_WAIT_SEC)
    # sys.exit завершил бы только этот поток, а обычный выход упёрся бы в тот самый зависший цикл.
    os._exit(1)


def _watch() -> None:
    while True:
        time.sleep(CHECK_INTERVAL_SEC)
        check_stall()


def start_stall_watchdog() -> None:
    beat()
    threading.Thread(target=_watch, name="worker-stall-watchdog", daemon=True).start()
