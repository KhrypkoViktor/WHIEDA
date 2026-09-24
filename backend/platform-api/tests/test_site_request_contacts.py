"""Шаг «контакты» в анкете на сайт (V12, 24.09.2026).

Партнёры присылают контакты одним сообщением — бот раскладывает их по полям,
а владелец получает и исходник, и разбор.
"""
import asyncio

from app.site_requests.contacts import contacts_summary, normalize_phone, parse_contacts
from app.telegram import site_requests as sr
from app.telegram.update_parser import TelegramMessage


def test_one_number_for_everything_named_in_the_line():
    # Рагият, 24.09: «Телефон: 8 928 672 92 88 Ватцап : 8 928 672 92 88».
    parsed = parse_contacts("Телефон: 8 928 672 92 88 Ватцап : 8 928 672 92 88")
    assert parsed == {"phone": ["+79286729288"], "whatsapp": ["+79286729288"]}


def test_real_message_with_links_handles_and_phone():
    # Фёдоров, 24.09 — как прислал владелец.
    parsed = parse_contacts(
        "ВК: https://vk.ru/pfedorov1973\n"
        "ВК на массаж: https://vk.ru/pavel_bem37\n"
        "Ватсап: @pavel_bem37\n"
        "Инстаграм: https://www.instagram.com/fiodorov.pavel73?igsi=dnZsb3lpZ3ZxbDZt&utm_source=qr\n"
        "Телефон: +79187570330"
    )
    assert parsed["vk"] == ["https://vk.ru/pfedorov1973", "https://vk.ru/pavel_bem37"]
    assert parsed["whatsapp"] == ["@pavel_bem37"]
    assert parsed["instagram"] == ["https://www.instagram.com/fiodorov.pavel73"]  # без utm
    assert parsed["phone"] == ["+79187570330"]


def test_all_messengers_on_one_number_and_email():
    parsed = parse_contacts("тг ватсап макс и телефон +7 995 551-58-17, почта svetlana.esenova.08@mail.ru")
    assert parsed["email"] == ["svetlana.esenova.08@mail.ru"]
    for key in ("phone", "whatsapp", "max", "telegram"):
        assert parsed[key] == ["+79955515817"]


def test_channels_and_groups_go_to_their_own_field():
    parsed = parse_contacts("Канал: https://t.me/lillianna1\nгруппа вк https://vk.com/club12345\nt.me/+AbCdEf")
    assert parsed == {"channels": ["https://t.me/lillianna1", "https://vk.com/club12345", "https://t.me/+AbCdEf"]}


def test_belarus_number_and_max_link():
    parsed = parse_contacts("WhatsApp +375 29 123-45-67\nMAX https://max.ru/u/f9LHodD0c")
    assert parsed == {"whatsapp": ["+375291234567"], "max": ["https://max.ru/u/f9LHodD0c"]}


def test_no_is_an_answer():
    assert parse_contacts("нет") == {}
    assert parse_contacts("  —  ") == {}


def test_phone_normalization():
    assert normalize_phone("8 (928) 672-92-88") == "+79286729288"
    assert normalize_phone("9286729288") == "+79286729288"
    assert normalize_phone("12345") is None


def test_owner_sees_raw_text_and_what_the_bot_understood(monkeypatch):
    sent = []

    class S:
        platform_billing_owner_telegram_id = "999"

    async def deliver(chat_id, text, *, reply_markup=None):
        sent.append(text)

    monkeypatch.setattr(sr, "get_settings", lambda: S())
    monkeypatch.setattr(sr, "_deliver", deliver)
    msg = TelegramMessage(
        chat_id=111, user_id=111, message_id=5, text="Телефон и WhatsApp: +7 900 123-45-67",
        chat_type="private", file_id=None, raw={}, username="partner",
    )
    request = {
        "requested_subdomain": "olga",
        "status": "awaiting_plan",
        "contacts_text": msg.text,
        "contacts": parse_contacts(msg.text),
    }
    asyncio.run(sr._notify_owner_step(msg, request, done="контакты"))
    text = sent[0]
    assert "Телефон и WhatsApp: +7 900 123-45-67" in text
    assert "Бот разобрал:" in text
    assert "WhatsApp: +79001234567" in text
    assert "Дальше: выбор пакета." in text


def test_summary_is_empty_for_empty_contacts():
    assert contacts_summary({}) == []
