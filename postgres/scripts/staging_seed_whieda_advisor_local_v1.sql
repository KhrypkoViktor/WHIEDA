-- Synthetic local Core E2E fixture. Values and URLs are not production data.
begin;
insert into advisor_structured_products (client_id,sku,canonical_name,retail_price_byn,partner_price_byn,partner_w,partner_points) values
('whieda','LOCAL-ACT','Активатор клеток',1750,1050,300,300),
('whieda','LOCAL-PRO','Активатор клеток PRO',2275,1400,400,400),
('whieda','LOCAL-WEN','Вэнтун',7000,5000,2000,2000),
('whieda','LOCAL-BEM','Magic Foherb',2275,1500,650,650),
('whieda','LOCAL-BAG','Ба-Гуа',1750,1050,500,500)
on conflict (client_id,sku) do update set canonical_name=excluded.canonical_name, retail_price_byn=excluded.retail_price_byn, partner_price_byn=excluded.partner_price_byn, partner_w=excluded.partner_w, partner_points=excluded.partner_points;
insert into advisor_structured_aliases (client_id,alias,canonical_sku,canonical_name,priority,active) values
('whieda','активатор клеток','LOCAL-ACT','Активатор клеток',100,true),('whieda','активатор','LOCAL-ACT','Активатор клеток',90,true),('whieda','ативатор','LOCAL-ACT','Активатор клеток',80,true),
('whieda','активатор клеток pro','LOCAL-PRO','Активатор клеток PRO',110,true),('whieda','активатор pro','LOCAL-PRO','Активатор клеток PRO',110,true),
('whieda','вэнтун','LOCAL-WEN','Вэнтун',100,true),('whieda','вентун','LOCAL-WEN','Вэнтун',90,true),
('whieda','бэм','LOCAL-BEM','Magic Foherb',100,true),('whieda','magic foherb','LOCAL-BEM','Magic Foherb',100,true),
('whieda','ба-гуа','LOCAL-BAG','Ба-Гуа',100,true),('whieda','ба гуа','LOCAL-BAG','Ба-Гуа',100,true),('whieda','сауна','LOCAL-BAG','Ба-Гуа',70,true)
on conflict (client_id,alias) do update set canonical_sku=excluded.canonical_sku,canonical_name=excluded.canonical_name,priority=excluded.priority,active=true;
insert into advisor_structured_product_cards (client_id,sku,canonical_name,what_it_is,who_asks_about_it,common_use_cases,how_to_use_short,primary_image_url) values
('whieda','LOCAL-ACT','Активатор клеток','Активатор клеток — локальный тестовый прибор для проверки Core.','Тестовый сценарий Core.','Проверка карточки.','По инструкции.','https://example.invalid/activator.jpg'),
('whieda','LOCAL-PRO','Активатор клеток PRO','Активатор клеток PRO — тестовая PRO-версия для проверки Core.','Тестовый сценарий Core.','Проверка карточки.','По инструкции.','https://example.invalid/activator-pro.jpg'),
('whieda','LOCAL-WEN','Вэнтун','Вэнтун — тестовый флагманский прибор для проверки Core.','Тестовый сценарий Core.','Проверка карточки.','По инструкции.','https://example.invalid/wentong.jpg'),
('whieda','LOCAL-BEM','Magic Foherb','Magic Foherb — тестовый прибор для проверки Core.','Тестовый сценарий Core.','Проверка карточки.','По инструкции.','https://example.invalid/magic.jpg'),
('whieda','LOCAL-BAG','Ба-Гуа','Ба-Гуа — тестовая минисауна для проверки Core.','Тестовый сценарий Core.','Проверка карточки.','По инструкции.','https://example.invalid/bagua.jpg')
on conflict (client_id,sku) do update set what_it_is=excluded.what_it_is,primary_image_url=excluded.primary_image_url;
insert into advisor_structured_resources (client_id,resource_id,sku,canonical_name,resource_type,title,url,priority,active) values
('whieda','L-ACT-VIDEO','LOCAL-ACT','Активатор клеток','video','Видео Активатор','https://example.invalid/activator-video',10,true),
('whieda','L-ACT-CERT','LOCAL-ACT','Активатор клеток','certificate','Сертификат Активатор','https://example.invalid/activator-cert.pdf',10,true),
('whieda','L-WEN-CERT','LOCAL-WEN','Вэнтун','certificate','Сертификат Вэнтун','https://example.invalid/wentong-cert.pdf',10,true)
on conflict (client_id,resource_id) do update set url=excluded.url,title=excluded.title,active=true;
insert into advisor_structured_product_comparisons (client_id,comparison_id,title,answer_text,left_sku,right_sku,priority,active) values
('whieda','L-ACT-PRO','Активатор и PRO','Активатор клеток PRO — тестовое сравнение с базовой версией.','LOCAL-ACT','LOCAL-PRO',10,true)
on conflict (client_id,comparison_id) do update set answer_text=excluded.answer_text,active=true;
insert into advisor_structured_clarification_prompts (client_id,clarification_key,prompt_text,enabled) values
('whieda','price_product_unknown','Уточните, пожалуйста, название товара или артикул.',true),
('whieda','product_ambiguity_general','Уточните, пожалуйста, какой именно товар вы имеете в виду.',true),
('whieda','knowledge_gap_generic','Нет подтверждённого ответа в локальном тестовом каталоге.',true)
on conflict (client_id,clarification_key) do update set prompt_text=excluded.prompt_text,enabled=true;
commit;
