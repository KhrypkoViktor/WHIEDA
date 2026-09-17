-- Игорь Ефименко (igoref): оплата первого потока «Платформа + Клуб» 105 W$
-- (17.09.2026, владелец подтвердил). Записано по образцу kira/zinaida:
-- две строки в ledger (PRO 30 W$ + клуб 75 W$), подписка до 21.12.2026.
--
-- Плюс коды приглашения для партнёров, у которых их ещё нет: кнопка
-- «Хочу такой же сайт» на сайте ведёт в бот как ?start=ref_<код>
-- (формат Core app/referral_bonus, код 16 символов url-safe).
--
-- Запуск: python wwc_sql.py --file igoref_platform_club_2026-09-17.sql

BEGIN;

INSERT INTO partner_payment_ledger
  (tenant_id, ref_code, amount_minor, currency, access_months, period_start, period_end, previous_paid_until, source, telegram_chat_id, telegram_message_id, telegram_user_id, product_code, promo_note)
VALUES
  ('whieda', 'igoref', 3000, 'WUSD', 3, now(), '2026-12-21 20:59:59+00', '2026-09-21 21:00:00+00', 'telegram_manual', '688931415', '-2026091701', '688931415', 'platform_subscription', 'пакет PRO + клуб 105 WWC$ (первый поток)'),
  ('whieda', 'igoref', 7500, 'WUSD', 3, now(), '2026-12-21 20:59:59+00', '2026-09-21 21:00:00+00', 'telegram_manual', '688931415', '-2026091702', '688931415', 'club_subscription', 'пакет PRO + клуб 105 WWC$ (первый поток)');

UPDATE partner_subscriptions
SET paid_until = '2026-12-21 20:59:59+00', updated_at = now()
WHERE tenant_id = 'whieda' AND ref_code = 'igoref';

INSERT INTO referral_invite_codes (tenant_id, invite_code, inviter_actor_id)
VALUES
  ('whieda', 'igR7fQ2mWx9pLv4K', 'igoref'),
  ('whieda', 'mkA3nH8sQe6tYb1Z', 'makarova'),
  ('whieda', 'ntL5wC2vRj8kPd7M', 'natali'),
  ('whieda', 'hrD9xT4bNq2mFs6W', 'harold'),
  ('whieda', 'ldK2pV7cXw5nQa9R', 'ladnaya'),
  ('whieda', 'ptW6mJ3fZy8hLc4T', 'petrovna'),
  ('whieda', 'prB4qN9dHv2sKf7X', 'profit'),
  ('whieda', 'sfM8cR5tWk3pJd6Q', 'sofiya'),
  ('whieda', 'zbT3vL7nQm9xHw2P', 'zubkovaludmila')
ON CONFLICT DO NOTHING;

COMMIT;

SELECT p.ref_code, s.paid_until::text, c.invite_code
FROM referral_profiles p
LEFT JOIN partner_subscriptions s ON s.ref_code = p.ref_code
LEFT JOIN referral_invite_codes c ON c.inviter_actor_id = p.owner_id
WHERE p.tenant_id = 'whieda' AND p.enabled
ORDER BY p.ref_code;
