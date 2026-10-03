-- Мастерская WWC (03.10.2026): витрина услуг владельца, платных курсов Академии,
-- цифровых материалов и внешних инструментов. Additive, idempotent.
-- No dollar signs in this file: production applies SQL through n8n.
--
--   shop_items  — каталог: цены и тексты — поля, не код; статус draft | pilot |
--                 published | archived (pilot видят только аккаунты владельца).
--   shop_orders — заказ из бота: заявка в форуме владельца (канал shop), чек,
--                 «Оплачено» владельца, доставка. Несколько заказов в одной заявке.
--   shop_access — что человек получил: файл (digital) или курс (course).
--
-- Решения (владелец, 03.10.2026): подтверждает только владелец; доли партнёру нет —
-- partner_ref_code в заказе только для статистики «кто привёл», без начислений.
-- Продажи Gemini остаются в service_sales (unique по ticket_id не трогаем); его
-- карточка в витрине ведёт на свой поток (?start=gemini), заказов Мастерской нет.
-- Медиа (обложка, файл) — academy_media по id; без внешнего ключа: проверяет код.
begin;

set local lock_timeout = '5s';

create table if not exists shop_items (
  tenant_id text not null references tenants (tenant_id),
  code text not null check (code ~ '^[a-z0-9]+(-[a-z0-9]+)*\Z' and length(code) <= 40),
  kind text not null check (kind in ('service', 'course', 'digital', 'external')),
  category text not null check (category in ('services', 'courses', 'materials', 'tools')),
  title text not null check (length(btrim(title)) between 1 and 200),
  subtitle text check (subtitle is null or length(subtitle) <= 300),
  description_md text not null default '' check (length(description_md) <= 20000),
  -- markdown → html (markdown + nh3, как Академия); пусто — код рендерит description_md
  description_html text not null default '',
  -- цены в сотых: WUSD (1 WUSD = 100 ₽ = 3,5 BYN); null у rub/byn — считать из WUSD
  price_wusd_minor bigint not null check (price_wusd_minor >= 0),
  price_rub_minor bigint check (price_rub_minor is null or price_rub_minor >= 0),
  price_byn_minor bigint check (price_byn_minor is null or price_byn_minor >= 0),
  -- цена словами вместо одной суммы (Gemini: два срока)
  price_text text check (price_text is null or length(price_text) <= 120),
  cover_media_id uuid,
  course_slug text check (course_slug is null or course_slug ~ '^[a-z0-9]+(-[a-z0-9]+)*\Z'),
  file_media_id uuid,
  external_url text check (external_url is null or length(external_url) <= 500),
  -- в v1 у всех 0 и не начисляется (владелец, 03.10.2026); доля Gemini — в service_tariffs
  partner_share_wusd_minor bigint not null default 0 check (partner_share_wusd_minor >= 0),
  confirmer text not null default 'owner' check (confirmer in ('owner', 'services_admin')),
  status text not null default 'draft' check (status in ('draft', 'pilot', 'published', 'archived')),
  sort_order int not null default 100,
  -- что написать клиенту после заказа (к реквизитам) и после «Оплачено»
  requisites_note text check (requisites_note is null or length(requisites_note) <= 1000),
  delivery_note text check (delivery_note is null or length(delivery_note) <= 1000),
  updated_by_telegram_user_id bigint,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, code),
  check (kind <> 'external' or external_url is not null)
);

create index if not exists idx_shop_items_status
  on shop_items (tenant_id, status, sort_order);

create table if not exists shop_orders (
  order_id uuid primary key default gen_random_uuid(),
  tenant_id text not null references tenants (tenant_id),
  item_code text not null,
  -- снимок названия на момент заказа: каталог могут переименовать
  item_title text not null,
  telegram_user_id bigint not null,
  ticket_id uuid references support_tickets (ticket_id),
  -- кто привёл: код из ссылки shop_<code>_<ref> или первое касание; без денег
  partner_ref_code text,
  partner_ref_source text check (partner_ref_source is null or partner_ref_source in ('link', 'attribution')),
  country_code text check (country_code is null or country_code in ('RU', 'BY')),
  currency text not null check (currency in ('RUB', 'WUSD')),
  amount_minor bigint not null check (amount_minor >= 0),
  status text not null default 'new' check (status in ('new', 'receipt', 'paid', 'delivered', 'cancelled')),
  receipt_file_id text,
  receipt_at timestamptz,
  paid_at timestamptz,
  paid_by bigint,
  delivered_at timestamptz,
  cancelled_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  foreign key (tenant_id, item_code) references shop_items (tenant_id, code)
);

-- Повторное «Купить» того же товара до оплаты — тот же заказ, не второй.
create unique index if not exists uq_shop_orders_open_item
  on shop_orders (tenant_id, telegram_user_id, item_code)
  where status in ('new', 'receipt');
create index if not exists idx_shop_orders_buyer
  on shop_orders (tenant_id, telegram_user_id, created_at desc);
create index if not exists idx_shop_orders_ticket
  on shop_orders (tenant_id, ticket_id)
  where ticket_id is not null;

create table if not exists shop_access (
  tenant_id text not null references tenants (tenant_id),
  item_code text not null,
  telegram_user_id bigint not null,
  order_id uuid references shop_orders (order_id),
  granted_at timestamptz not null default now(),
  revoked_at timestamptz,
  primary key (tenant_id, item_code, telegram_user_id),
  foreign key (tenant_id, item_code) references shop_items (tenant_id, code)
);

-- Каталог v1 (цены владельца, 03.10.2026). Всё — pilot, кроме Gemini (published,
-- свой поток). Повторный запуск ничего не перезаписывает: правки владельца живут.
insert into shop_items (
  tenant_id, code, kind, category, title, subtitle, description_md,
  price_wusd_minor, price_rub_minor, price_text, course_slug, external_url,
  confirmer, status, sort_order, requisites_note, delivery_note
) values
  (
    'whieda', 'konsultaciya', 'service', 'services',
    'Консультация с Виктором',
    'Маркетинг, воронки, автоматизация, техника — 1,5 часа онлайн',
    'Разбираем вашу задачу: маркетинг, воронки продаж, автоматизация, техническая настройка.' || chr(10) || chr(10)
      || '1,5 часа онлайн. Время согласуем в заявке после оплаты.',
    10000, 1000000, null, null, null,
    'owner', 'pilot', 10,
    null,
    'Напишите здесь 2–3 удобных времени для встречи и коротко — с чем приходите. Виктор подтвердит время в этой заявке.'
  ),
  (
    'whieda', 'snyat-blok', 'service', 'services',
    'Сессия «Снять блок»',
    'Страхи, внутренние блоки, состояние — 1,5 часа онлайн',
    'Работа со страхами, внутренними блоками и состоянием через образы и ощущения.' || chr(10) || chr(10)
      || '1,5 часа онлайн. Время согласуем в заявке после оплаты.',
    10000, 1000000, null, null, null,
    'owner', 'pilot', 20,
    null,
    'Напишите здесь 2–3 удобных времени для встречи. Виктор подтвердит время в этой заявке.'
  ),
  (
    'whieda', 'lending', 'service', 'services',
    'Продающий лендинг под заказ',
    'Одностраничный сайт на вашем поддомене wwc.best',
    'Лендинг под ваш продукт или услугу на вашем поддомене wwc.best.' || chr(10) || chr(10)
      || 'После оплаты ответите здесь на короткую анкету: о чём страница, для кого, какие контакты показать.',
    12000, 1200000, null, null, null,
    'owner', 'pilot', 30,
    null,
    'Ответьте здесь на анкету: 1) о чём лендинг и для кого; 2) какой поддомен; 3) какие контакты показать. Фото и тексты присылайте сюда же.'
  ),
  (
    'whieda', 'kurs-vozrazheniya', 'course', 'courses',
    'Курс «Мастерство работы с возражениями»',
    'Курс в Академии WWC',
    'Курс в Академии WWC: как слышать возражение, отвечать на него и продолжать разговор.' || chr(10) || chr(10)
      || 'Доступ откроется в Академии сразу после подтверждения оплаты.',
    2500, 250000, null, 'vozrazheniya', null,
    'owner', 'pilot', 40,
    null, null
  ),
  (
    'whieda', 'kurs-prodazhi', 'course', 'courses',
    'Курс «Продажи»',
    'Курс в Академии WWC',
    'Курс в Академии WWC о продажах. Доступ откроется в Академии после подтверждения оплаты.',
    5000, 500000, null, null, null,
    'owner', 'pilot', 50,
    null, null
  ),
  (
    'whieda', 'preza-vozrazheniya', 'digital', 'materials',
    'Презентация «Мастерство работы с возражениями»',
    'PDF, 21 слайд',
    'Презентация в PDF: 21 слайд о работе с возражениями. Файл появится в личном кабинете после подтверждения оплаты.',
    500, 50000, null, null, null,
    'owner', 'pilot', 60,
    null, null
  ),
  (
    'whieda', 'gemini', 'external', 'tools',
    'Gemini Pro — лицензия',
    'Gemini Pro, генерация картинок и видео, 2 ТБ Google Drive',
    'В подписку входит нейросеть Gemini Pro, Nanobanana (картинки), VEO3 (видео) и 2 ТБ Google Drive.' || chr(10) || chr(10)
      || 'Лицензия на 6 месяцев — 3 990 ₽, на 18 месяцев — 4 490 ₽. Подключение — через бота.',
    3990, 399000, '6 мес — 3 990 ₽ · 18 мес — 4 490 ₽', null, '?start=gemini',
    'services_admin', 'published', 70,
    null, null
  )
on conflict (tenant_id, code) do nothing;

alter table shop_items enable row level security;
drop policy if exists shop_items_tenant_isolation on shop_items;
create policy shop_items_tenant_isolation on shop_items
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

alter table shop_orders enable row level security;
drop policy if exists shop_orders_tenant_isolation on shop_orders;
create policy shop_orders_tenant_isolation on shop_orders
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

alter table shop_access enable row level security;
drop policy if exists shop_access_tenant_isolation on shop_access;
create policy shop_access_tenant_isolation on shop_access
  using (tenant_id = platform_current_tenant_id())
  with check (tenant_id = platform_current_tenant_id());

commit;
