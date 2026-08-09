-- NOT PRODUCTION DATA — synthetic local Core advisor parity fixtures only.
begin;

-- === products ===
insert into advisor_structured_products (
  client_id, sku, canonical_name, retail_price_rub, retail_price_byn,
  partner_price_rub, partner_price_byn, partner_w, partner_points
) values
('whieda','LOCAL-ACT','Активатор клеток',50000,1750,30000,1050,300,300),
('whieda','LOCAL-PRO','Активатор клеток PRO',65000,2275,40000,1400,400,400),
('whieda','LOCAL-WEN','Вэнтун',200000,7000,150000,5000,2000,2000),
('whieda','LOCAL-BEM','Magic Foherb',65000,2275,43000,1500,650,650),
('whieda','LOCAL-BAG','Ба-Гуа',50000,1750,30000,1050,500,500),
('whieda','LOCAL-PASTE-A','Паста с экстрактом полыни',12000,420,8000,280,120,120),
('whieda','LOCAL-PASTE-B','Паста Цинфэн',14000,490,9000,315,140,140),
('whieda','F001-02','Эликсир Фохоу',18000,630,12000,420,180,180),
('whieda','F002-02','Эликсир 3 Драгоценности',19000,665,12500,437,190,190),
('whieda','F003-02','Эликсир Саньцин',17000,595,11500,402,170,170),
('whieda','T003','Магнитный пояс',25000,875,16000,560,250,250),
('whieda','LOCAL-NOPRICE','Товар без цены (тест)',null,null,null,null,null,null),
('whieda','LOCAL-NOPHOTO','Товар без фото (тест)',15000,525,9000,315,150,150),
('whieda','LOCAL-NOCERT','Товар без сертификата (тест)',16000,560,9500,332,160,160),
('whieda','F038-00','Соевый пептид',22000,770,14000,490,220,220),
('test-acme','LOCAL-ACT-ACME','Acme Test Activator',10000,350,6000,210,100,100)
on conflict (client_id, sku) do update set
  canonical_name=excluded.canonical_name,
  retail_price_rub=excluded.retail_price_rub, retail_price_byn=excluded.retail_price_byn,
  partner_price_rub=excluded.partner_price_rub, partner_price_byn=excluded.partner_price_byn,
  partner_w=excluded.partner_w, partner_points=excluded.partner_points;

-- === aliases ===
insert into advisor_structured_aliases (client_id,alias,canonical_sku,canonical_name,priority,active) values
('whieda','активатор клеток','LOCAL-ACT','Активатор клеток',100,true),
('whieda','активатор','LOCAL-ACT','Активатор клеток',90,true),
('whieda','активатора','LOCAL-ACT','Активатор клеток',90,true),
('whieda','ативатор','LOCAL-ACT','Активатор клеток',80,true),
('whieda','активатор клеток pro','LOCAL-PRO','Активатор клеток PRO',110,true),
('whieda','активатор pro','LOCAL-PRO','Активатор клеток PRO',110,true),
('whieda','вэнтун','LOCAL-WEN','Вэнтун',100,true),
('whieda','вентун','LOCAL-WEN','Вэнтун',90,true),
('whieda','бэм','LOCAL-BEM','Magic Foherb',100,true),
('whieda','magic foherb','LOCAL-BEM','Magic Foherb',100,true),
('whieda','ба-гуа','LOCAL-BAG','Ба-Гуа',100,true),
('whieda','ба гуа','LOCAL-BAG','Ба-Гуа',100,true),
('whieda','сауна','LOCAL-BAG','Ба-Гуа',70,true),
('whieda','сауны','LOCAL-BAG','Ба-Гуа',70,true),
('whieda','паста','LOCAL-PASTE-A','Паста с экстрактом полыни',50,true),
('whieda','пояс','T003','Магнитный пояс',50,true),
('whieda','красный','F001-02','Эликсир Фохоу',40,true),
('whieda','красный эликсир','F001-02','Эликсир Фохоу',95,true),
('whieda','красного эликсира','F001-02','Эликсир Фохоу',95,true),
('whieda','красный эликсир фохоу','F001-02','Эликсир Фохоу',100,true),
('whieda','зеленый эликсир','F003-02','Эликсир Саньцин',95,true),
('whieda','синий эликсир','F002-02','Эликсир 3 Драгоценности',95,true),
('whieda','магнитный пояс','T003','Магнитный пояс',100,true),
('whieda','товар без цены','LOCAL-NOPRICE','Товар без цены (тест)',100,true),
('whieda','товара без цены','LOCAL-NOPRICE','Товар без цены (тест)',100,true),
('whieda','товар без сертификата','LOCAL-NOCERT','Товар без сертификата (тест)',100,true),
('whieda','товара без сертификата','LOCAL-NOCERT','Товар без сертификата (тест)',100,true),
('whieda','товар без фото','LOCAL-NOPHOTO','Товар без фото (тест)',100,true),
('whieda','товара без фото','LOCAL-NOPHOTO','Товар без фото (тест)',100,true),
('whieda','зелёный эликсир','F003-02','Эликсир Саньцин',95,true),
('whieda','пептид','F038-00','Соевый пептид',60,true),
('whieda','соевый пептид','F038-00','Соевый пептид',100,true),
('test-acme','активатор','LOCAL-ACT-ACME','Acme Test Activator',100,true)
on conflict (client_id,alias) do update set
  canonical_sku=excluded.canonical_sku, canonical_name=excluded.canonical_name,
  priority=excluded.priority, active=true;

-- === cards ===
insert into advisor_structured_product_cards (
  client_id,sku,canonical_name,what_it_is,who_asks_about_it,common_use_cases,how_to_use_short,primary_image_url
) values
('whieda','LOCAL-ACT','Активатор клеток','Локальный тестовый прибор Активатор клеток.','Core parity lab.','Карточка.','По инструкции.','https://example.invalid/local/activator.jpg'),
('whieda','LOCAL-PRO','Активатор клеток PRO','Локальный тест PRO-версии.','Core parity lab.','Карточка.','По инструкции.','https://example.invalid/local/activator-pro.jpg'),
('whieda','LOCAL-WEN','Вэнтун','Локальный тест Вэнтун.','Core parity lab.','Карточка.','По инструкции.','https://example.invalid/local/wentong.jpg'),
('whieda','LOCAL-BEM','Magic Foherb','Локальный тест БЭМ.','Core parity lab.','Карточка.','По инструкции.','https://example.invalid/local/bem.jpg'),
('whieda','LOCAL-BAG','Ба-Гуа','Локальная тестовая минисауна.','Core parity lab.','Карточка.','По инструкции.','https://example.invalid/local/bagua.jpg'),
('whieda','LOCAL-PASTE-A','Паста с экстрактом полыни','Локальная тестовая паста A.','Core parity lab.','Карточка.','По инструкции.','https://example.invalid/local/paste-a.jpg'),
('whieda','LOCAL-PASTE-B','Паста Цинфэн','Локальная тестовая паста B.','Core parity lab.','Карточка.','По инструкции.','https://example.invalid/local/paste-b.jpg'),
('whieda','F001-02','Эликсир Фохоу','Локальный красный эликсир (тест).','Core parity lab.','Карточка.','По инструкции.','https://example.invalid/local/elixir-red.jpg'),
('whieda','F002-02','Эликсир 3 Драгоценности','Локальный синий эликсир (тест).','Core parity lab.','Карточка.','По инструкции.','https://example.invalid/local/elixir-blue.jpg'),
('whieda','F003-02','Эликсир Саньцин','Локальный зелёный эликсир (тест).','Core parity lab.','Карточка.','По инструкции.','https://example.invalid/local/elixir-green.jpg'),
('whieda','T003','Магнитный пояс','Локальный магнитный пояс (тест).','Core parity lab.','Карточка.','По инструкции.','https://example.invalid/local/belt.jpg'),
('whieda','LOCAL-NOPHOTO','Товар без фото (тест)','Товар для проверки отсутствия фото.','Core parity lab.','Карточка.','По инструкции.',null),
('whieda','LOCAL-NOCERT','Товар без сертификата (тест)','Товар для проверки отсутствия сертификата.','Core parity lab.','Карточка.','По инструкции.','https://example.invalid/local/nocert.jpg'),
('whieda','LOCAL-NOPRICE','Товар без цены (тест)','Товар для проверки честного отсутствия цены.','Core parity lab.','Карточка.','По инструкции.','https://example.invalid/local/noprice.jpg'),
('whieda','F038-00','Соевый пептид','Локальный соевый пептид (тест).','Core parity lab.','Карточка.','По инструкции.','https://example.invalid/local/peptide.jpg'),
('test-acme','LOCAL-ACT-ACME','Acme Test Activator','Acme tenant synthetic product.','Tenant isolation test.','Карточка.','По инструкции.','https://example.invalid/acme/activator.jpg')
on conflict (client_id,sku) do update set
  what_it_is=excluded.what_it_is, primary_image_url=excluded.primary_image_url;

-- === resources (video/pdf/certificate) ===
insert into advisor_structured_resources (
  client_id,resource_id,sku,canonical_name,resource_type,title,url,priority,active
) values
('whieda','L-ACT-VIDEO','LOCAL-ACT','Активатор клеток','video','Видео Активатор','https://example.invalid/local/activator-video.mp4',10,true),
('whieda','L-ACT-CERT','LOCAL-ACT','Активатор клеток','certificate','Сертификат Активатор','https://example.invalid/local/activator-cert.pdf',10,true),
('whieda','L-WEN-CERT','LOCAL-WEN','Вэнтун','certificate','Сертификат Вэнтун','https://example.invalid/local/wentong-cert.pdf',10,true),
('whieda','L-ACT-PDF','LOCAL-ACT','Активатор клеток','document','PDF Активатор','https://example.invalid/local/activator-doc.pdf',5,true)
on conflict (client_id,resource_id) do update set url=excluded.url, title=excluded.title, active=true;

-- === comparisons ===
insert into advisor_structured_product_comparisons (
  client_id,comparison_id,title,answer_text,left_sku,right_sku,priority,active
) values
('whieda','L-ACT-PRO','Активатор и PRO','PRO — расширенная тестовая версия с дополнительными режимами.','LOCAL-ACT','LOCAL-PRO',10,true)
on conflict (client_id,comparison_id) do update set answer_text=excluded.answer_text, active=true;

-- === capability / clarification ===
insert into advisor_structured_capability_responses (client_id,response_id,intent_id,answer_text,enabled) values
('whieda','L-GREET','greeting','Здравствуйте! Локальный Core parity lab.',true),
('whieda','L-CAP','capabilities','Могу: цена, PV, карточка, фото, видео, сертификат, сравнение, корзина.',true),
('whieda','L-HELP','help','Напишите товар, цену, «фото …», «сравни … и …» или вопрос про PV.',true)
on conflict (client_id,response_id) do update set answer_text=excluded.answer_text, enabled=true;

insert into advisor_structured_clarification_prompts (client_id,clarification_key,prompt_text,enabled) values
('whieda','price_product_unknown','Уточните название товара или артикул.',true),
('whieda','product_ambiguity_general','Уточните, какой именно товар вы имеете в виду.',true),
('whieda','product_ambiguity_paste','Вы про зубную пасту с экстрактом полыни или Пасту Цинфэн?',true),
('whieda','product_ambiguity_activator','Вы про Активатор клеток или Активатор клеток PRO?',true),
('whieda','product_ambiguity_belt','Вы про Магнитный пояс? Нужна цена, описание или применение?',true),
('whieda','product_ambiguity_color_красн','Вы про красный эликсир Фохоу? Нужна цена, описание или применение?',true),
('whieda','knowledge_gap_generic','Пока нет подтверждённого ответа в базе WHIEDA. Уточните название товара или артикул.',true),
('whieda','safety_clarification','Уточните симптомы и обратитесь к специалисту — локальный тест без медицинских claims.',true)
on conflict (client_id,clarification_key) do update set prompt_text=excluded.prompt_text, enabled=true;

-- === business FAQ / objections ===
insert into advisor_structured_business_faq (client_id,faq_id,title,answer_text,aliases,priority,active) values
('whieda','L-FAQ-PV','Что такое PV','PV — локальный тестовый ответ про личный объём WHIEDA.','pv|баллы|личный объём',100,true),
('whieda','L-FAQ-REP','Повторные покупки','Повторка — заказы по партнёрской цене после регистрации (тест).','повторка|повторные',90,true),
('whieda','L-FAQ-STEP','Step бонус','Step — тестовое описание ступенчатого бонуса WHIEDA.','step|ступен',80,true),
('whieda','L-FAQ-BIN','Бинарный бонус','Бинар — тестовое описание бинарного бонуса WHIEDA.','бинар|бинарный',80,true)
on conflict (client_id,faq_id) do update set answer_text=excluded.answer_text, active=true;

insert into advisor_structured_business_objections (
  client_id,objection_id,title,aliases,first_reply,clarify,next_step,do_not_say,priority,active
) values
('whieda','L-OBJ-MLM','MLM возражение','mlm|сетевой|пирамида',
 'WHIEDA — компания с продуктами и партнёрской программой (локальный тест).',
 'Что именно смущает?', 'Могу рассказать про продукты.', 'пирамида',100,true)
on conflict (client_id,objection_id) do update set first_reply=excluded.first_reply, active=true;

-- === promotions / events / community / basket ===
insert into advisor_promotions (
  client_id,promotion_id,tenant_id,title,short_text,starts_at,ends_at,status,priority,benefit_text,country
) values
('whieda','L-PROMO-1','by','Локальная тестовая акция','Скидка на Активатор в parity lab.',
 now() - interval '1 day', now() + interval '30 days', 'active', 100, 'Тестовая выгода 10%', 'BY')
on conflict (client_id,promotion_id) do update set status='active', short_text=excluded.short_text;

insert into advisor_whieda_events (
  client_id,event_id,tenant_id,title,description,starts_at,ends_at,status,city,country
) values
('whieda','L-EVENT-1','by','Локальный тестовый эфир','Synthetic event for parity lab.',
 now() + interval '2 days', now() + interval '2 days 2 hours', 'active', 'Минск', 'BY')
on conflict (client_id,event_id) do update set status='active', title=excluded.title;

insert into advisor_whieda_community_resources (
  client_id,resource_id,tenant_id,title,description,url,status,priority,platform
) values
('whieda','L-COMM-1','by','Локальный тестовый канал','Synthetic community link.',
 'https://example.invalid/local/community', 'active', 100, 'telegram')
on conflict (client_id,resource_id) do update set status='active', url=excluded.url;

insert into advisor_starter_basket_templates (
  client_id,template_id,tenant_id,title,goal,preferred_product_ids,status,priority,description
) values
('whieda','L-BASKET-500','by','Старт 500 PV','pv_target','LOCAL-ACT|LOCAL-BAG|LOCAL-BEM','active',100,
 'Локальный шаблон стартовой корзины на ~500 PV.')
on conflict (client_id,template_id) do update set status='active', preferred_product_ids=excluded.preferred_product_ids;

insert into advisor_product_recommendation_rules (
  client_id,product_id,tenant_id,registration_enabled,availability_status,business_priority,
  universality_score,popularity_score,reason_short,status
) values
('whieda','LOCAL-ACT','by',true,'available',100,90,80,'Базовый тестовый вход','active'),
('whieda','LOCAL-BAG','by',true,'available',90,85,70,'Популярная сауна (тест)','active'),
('whieda','LOCAL-BEM','by',true,'available',85,80,75,'Демо-прибор (тест)','active'),
('whieda','LOCAL-WEN','by',true,'available',80,70,60,'Флагман (тест)','active')
on conflict (client_id,product_id) do update set status='active', business_priority=excluded.business_priority;

commit;
