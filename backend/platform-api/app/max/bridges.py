"""Пары групп «Telegram ↔ Max» (мост V27, 10.10.2026). Только данные — логика в app/max/bridge.py.

Владелец (10.10.2026): «из группы 1 потока перепощивать мои сообщения в Max, а оттуда
от людей — в нашу группу»; куратор Самцова уходит в Max, только если отвечает на
сообщение бота (то есть на сообщение, пришедшее из Max).
Клуб (11.10.2026): «там тоже нужно двустороннее общение» — все участники в обе стороны.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ChatBridge:
    title: str
    tg_chat_id: int
    max_chat_id: int
    # «owner» — из Telegram в Max только владелец (и куратор ответом боту); «all» — все участники.
    mode: str = "owner"
    # Чем проверяется право быть в группе: «course» — оплаченный курс, «club» — активный клуб.
    access: str = "course"
    # Курс, оплата которого пускает в группу (shop_access.item_code).
    course_item_code: str = ""
    # Кто, кроме владельца, может попасть в Max — только ответом на сообщение из Max: id → подпись.
    curators: dict[int, str] = field(default_factory=dict)
    # Аккаунты Max владельца и команды: кого они добавили в Max-группу — о том не спрашиваем.
    max_staff: frozenset[int] = frozenset()


BRIDGES: tuple[ChatBridge, ...] = (
    ChatBridge(
        title="WWC : AI \\ SMM 1й поток",
        tg_chat_id=-1004497305833,
        max_chat_id=-79965253305729,
        course_item_code="kurs-online-start",
        curators={525317405: "Ольга Самцова"},
        max_staff=frozenset({482284673}),  # Max-аккаунт владельца: он добавил бота в обе группы Max
    ),
    # Порядок не менять: номер пары стоит в кнопках «Удалить» у владельца.
    ChatBridge(
        title="WWC Leader CLUB",
        tg_chat_id=-1004338290116,
        max_chat_id=-79980845696385,
        mode="all",
        access="club",
        max_staff=frozenset({482284673}),
    ),
)


def bridge_for_telegram(chat_id: int | str | None) -> ChatBridge | None:
    try:
        key = int(chat_id) if chat_id is not None else None
    except (TypeError, ValueError):
        return None
    return next((b for b in BRIDGES if b.tg_chat_id == key), None)


def bridge_for_max(chat_id: int | str | None) -> ChatBridge | None:
    try:
        key = int(chat_id) if chat_id is not None else None
    except (TypeError, ValueError):
        return None
    return next((b for b in BRIDGES if b.max_chat_id == key), None)


def bridged_max_chat_ids() -> set[int]:
    return {b.max_chat_id for b in BRIDGES}
