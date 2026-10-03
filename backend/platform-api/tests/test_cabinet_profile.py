"""Профиль сайта из кабинета /me/: проверка полей, «было → стало», запись в public_profile."""

from __future__ import annotations

import pytest

from app.cabinet.profile import (
    BIO_MAX,
    ProfileValidationError,
    apply_profile_changes,
    changed_fields,
    clean_bio,
    clean_phone,
    clean_url,
    current_profile_fields,
    diff_profile,
    media_id_from_url,
    media_id_in_url,
    media_public_url,
    normalize_profile_input,
    overlay_changes,
    profile_complete,
)

BASE = "https://wwc.best"
MEDIA = "3f1c2a9e-0b7d-4c55-9a1e-2d3f4b5c6d7e"


def _error(call) -> ProfileValidationError:
    with pytest.raises(ProfileValidationError) as caught:
        call()
    return caught.value


def test_phones_become_e164_and_garbage_is_refused():
    assert clean_phone("8 928 672-92-88", "phone") == "+79286729288"
    assert clean_phone("+375 (29) 172-76-41", "viber") == "+375291727641"
    assert clean_phone("＋７ ９１６ １２３-４５-６７", "whatsapp") == "+79161234567"
    assert clean_phone("", "phone") is None
    for bad in ("12-34", "позвоните мне 89286729288", "+7 928 672 92 88 доб. 5", "1" * 40):
        assert _error(lambda bad=bad: clean_phone(bad, "phone")).code == "invalid_phone"


def test_links_are_https_and_only_to_their_own_network():
    assert clean_url("https://t.me/dohod_dla_vsex", "telegram_channel_url") == "https://t.me/dohod_dla_vsex"
    assert clean_url("https://m.vk.com/id1", "vk_url") == "https://m.vk.com/id1"
    assert clean_url("https://www.instagram.com/maramakarovaa/", "instagram_url")
    assert clean_url("https://max.ru/u/f9LHodD0c", "max_url")
    assert _error(lambda: clean_url("http://t.me/x", "telegram_channel_url")).code == "https_required"
    assert _error(lambda: clean_url("javascript:alert(1)", "vk_url")).code == "https_required"
    assert _error(lambda: clean_url("https://evil.example/vk.com", "vk_url")).code == "wrong_site"
    assert _error(lambda: clean_url("https://vk.com.evil.example/x", "vk_url")).code == "wrong_site"
    assert _error(lambda: clean_url("https://user@vk.com/x", "vk_url")).code == "invalid_url"
    assert _error(lambda: clean_url("https://vk.com/", "vk_url")).code == "invalid_url"
    assert _error(lambda: clean_url('https://vk.com/x"onclick=1', "vk_url")).code == "invalid_url"


def test_bio_is_plain_text_up_to_600_characters():
    assert clean_bio("  Привет!\r\n\r\n\r\n\r\nЯ  партнёр.  ") == "Привет!\n\nЯ партнёр."
    assert clean_bio("   ") is None
    assert clean_bio("я" * BIO_MAX) == "я" * BIO_MAX
    assert _error(lambda: clean_bio("я" * (BIO_MAX + 1))).code == "too_long"
    assert _error(lambda: clean_bio("Мой <b>сайт</b>")).code == "html_not_allowed"
    assert _error(lambda: clean_bio("<script>alert(1)</script>")).code == "html_not_allowed"
    assert _error(lambda: clean_bio("пробел&nbsp;тут")).code == "html_not_allowed"
    assert clean_bio("Сердце <3 и R&D") == "Сердце <3 и R&D"


def test_normalize_input_checks_every_field_and_keeps_only_sent_ones():
    data = normalize_profile_input(
        {
            "display_name": "  Марина   Макарова ",
            "bio": "",
            "contacts": {"phone": "8 968 060-58-88", "maxUrl": "https://max.ru/u/abc", "email": "Alex@Mail.RU"},
            "socials": {"telegramChannelUrl": "https://t.me/dohod_dla_vsex", "vk_url": None},
        }
    )
    assert data == {
        "display_name": "Марина Макарова",
        "bio": None,
        "contacts": {"phone": "+79680605888", "max_url": "https://max.ru/u/abc", "email": "Alex@mail.ru"},
        "socials": {"telegram_channel_url": "https://t.me/dohod_dla_vsex", "vk_url": None},
    }
    assert normalize_profile_input({"photo_url": ""}) == {}  # фото не убирается
    for body, code, field in (
        ({"display_name": " "}, "required", "display_name"),
        ({"display_name": "Я"}, "too_short", "display_name"),
        ({"display_name": "<i>Ольга</i>"}, "html_not_allowed", "display_name"),
        ({"display_name": 'Ольга" onload="x'}, "invalid_characters", "display_name"),
        ({"contacts": {"address": "Москва `x`"}}, "invalid_characters", "address"),
        ({"contacts": {"telegram": "@x"}}, "unknown_field", "telegram"),
        ({"contacts": "+7 928"}, "invalid_value", "contacts"),
        ({"contacts": {"email": "not-an-email"}}, "invalid_email", "email"),
        ({"contacts": {"address": "a" * 201}}, "too_long", "address"),
        ({"price": 1}, "unknown_field", "price"),
    ):
        error = _error(lambda body=body: normalize_profile_input(body))
        assert (error.code, error.field) == (code, field), body


def test_current_fields_read_nested_first_then_legacy_top_level():
    current = current_profile_fields(
        {
            "display_name": "Ольга",
            "photo_url": "/media/partners/o.jpg",
            "telegram_channel_url": "https://t.me/old",
            "socials": {"telegram_channel_url": "https://t.me/new"},
            "youtube_url": "https://www.youtube.com/@olga",
            "contacts": {"phone": "+79991112233"},
        }
    )
    assert current["display_name"] == "Ольга" and current["bio"] is None
    assert current["socials"]["telegram_channel_url"] == "https://t.me/new"
    assert current["socials"]["youtube_url"] == "https://www.youtube.com/@olga"
    assert current["contacts"]["phone"] == "+79991112233" and current["contacts"]["viber"] is None
    # Контакты — только из вложенного contacts: «phone» наверху мог значить другое.
    assert current_profile_fields({"phone": "+70000000000"})["contacts"]["phone"] is None


def test_diff_keeps_only_real_changes_with_what_was_before():
    current = current_profile_fields({"display_name": "Ольга", "bio": "Старое", "contacts": {"phone": "+79991112233"}})
    wanted = {
        "display_name": "Ольга",  # без изменений
        "bio": "Новое",
        "contacts": {"phone": "+79991112233", "whatsapp": "+79991112233"},
        "socials": {"vk_url": None},  # и так пусто
    }
    changes, previous = diff_profile(current, wanted)
    assert changes == {"bio": "Новое", "contacts": {"whatsapp": "+79991112233"}}
    assert previous == {"bio": "Старое", "contacts": {"whatsapp": None}}
    assert changed_fields(changes) == ["bio", "whatsapp"]
    assert diff_profile(current, {"display_name": "Ольга"}) == ({}, {})


def test_new_request_replaces_pending_without_losing_untouched_fields():
    pending = {"photo_url": "https://wwc.best/p.jpg", "contacts": {"phone": "+79991112233"}}
    merged = overlay_changes(pending, {"bio": "Текст", "contacts": {"email": "a@b.ru"}})
    assert merged == {
        "photo_url": "https://wwc.best/p.jpg",
        "bio": "Текст",
        "contacts": {"phone": "+79991112233", "email": "a@b.ru"},
    }


def test_apply_writes_nested_objects_and_drops_legacy_keys():
    profile = {
        "display_name": "Ольга",
        "subdomain": "samtsova",
        "selected_theme_id": "sankofa",
        "vk_url": "https://vk.com/old",
        "phone": "+70000000000",
        "socials": {"youtube_url": "https://www.youtube.com/@olga"},
    }
    changes = {
        "display_name": "Ольга Самцова",
        "bio": "О себе",
        "photo_url": media_public_url(BASE, MEDIA),
        "contacts": {"phone": "+79991112233"},
        "socials": {"vk_url": None, "telegram_channel_url": "https://t.me/olga"},
    }
    result = apply_profile_changes(profile, changes)
    assert result["display_name"] == "Ольга Самцова" and result["bio"] == "О себе"
    assert result["photo_url"] == f"https://wwc.best/api/v1/content-access/partner-media/{MEDIA}.jpg"
    assert result["contacts"] == {"phone": "+79991112233"}
    assert result["socials"] == {"youtube_url": "https://www.youtube.com/@olga", "telegram_channel_url": "https://t.me/olga"}
    assert "vk_url" not in result  # старый ключ соцсети не вернёт убранное
    assert result["phone"] == "+70000000000"  # ключи верхнего уровня у контактов не трогаем
    assert result["subdomain"] == "samtsova" and result["selected_theme_id"] == "sankofa"
    assert profile["vk_url"] == "https://vk.com/old"  # исходный словарь не тронут
    cleared = apply_profile_changes(result, {"bio": None, "contacts": {"phone": None}})
    assert "bio" not in cleared and "contacts" not in cleared


def test_profile_complete_needs_photo_bio_and_a_contact():
    fields = current_profile_fields({"photo_url": "x", "bio": "y", "contacts": {"email": "a@b.ru"}})
    assert profile_complete(fields)
    assert not profile_complete(current_profile_fields({"photo_url": "x", "bio": "y"}))
    assert not profile_complete(current_profile_fields({"bio": "y", "contacts": {"email": "a@b.ru"}}))


def test_only_our_cabinet_photo_urls_are_recognised():
    url = media_public_url(BASE, MEDIA)
    assert media_id_from_url(url, BASE) == MEDIA
    assert media_id_from_url(url, "https://staging.wwc.best") is None
    # «Что не удалять» узнаёт своё фото с любым origin.
    assert media_id_in_url(f"https://staging.wwc.best/api/v1/content-access/partner-media/{MEDIA}.jpg") == MEDIA
    assert media_id_in_url("/media/partners/x.jpg") is None
    assert media_id_from_url("https://media.sysarch.pro/media/partners/x.jpg", BASE) is None
    assert media_id_from_url(f"{BASE}/api/v1/content-access/partner-media/../etc.jpg", BASE) is None
