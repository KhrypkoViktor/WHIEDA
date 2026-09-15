-- Closed awards/patents pack for product cards (Telegram-gated, same flow as reviews).
-- Additive and idempotent. Depends on platform_content_access_v1.sql.

begin;

insert into content_access_materials (
  tenant_id, content_key, scope, status, kind, title, body_html
) values (
  'whieda',
  'product/evidence/awards-patents',
  'telegram_verified',
  'published',
  'article',
  'Награды, медали и патенты',
  $awards$
<p>Один пакет документов и ссылок: стельки и эликсиры Фохоу. Открытые сертификаты остаются в блоке «Сертификаты» на карточке.</p>

<h3>Стельки с анионами</h3>
<p><strong>Гонконгская золотая медаль.</strong> Коррекционные стельки Гучжэнцзи / стельки с анионами получили золотую медаль в Гонконге на выставке инноваций. Номер стенда выставки в открытых каталогах не опубликован.</p>
<ul>
<li><a href="https://whieda.su/produkciya/korrekcionnye-stelki/" target="_blank" rel="noopener noreferrer">WHIEDA: коррекционные стельки Гучжэнцзи — золотая медаль в Гонконге на выставке инноваций</a></li>
<li><a href="https://whiedarussia.ru/korrekciya_stop/" target="_blank" rel="noopener noreferrer">WHIEDA: коррекция стоп — золотая медаль на международной выставке изобретений и патент ZL 2013 30414992.9</a></li>
<li><a href="https://whieda.wtwd.ru/product/korrekcionnye-stelki/" target="_blank" rel="noopener noreferrer">Карточка стелек: запатентованный сплав и золотая медаль на Гонконгской выставке инноваций</a></li>
<li><a href="https://gradmsk.ru/video/282c54dd7cd647fdd0b0c37dd8f918c2/" target="_blank" rel="noopener noreferrer">Видео-материал: стельки Гучжэнцзи, патент на сплав и золотая медаль в Гонконге</a></li>
</ul>
<p><strong>Патент Китая ZL 2013 30414992.9</strong> — патент коррекционных стелек. Источник с номером: <a href="https://whiedarussia.ru/korrekciya_stop/" target="_blank" rel="noopener noreferrer">whiedarussia.ru/korrekciya_stop</a>. Поиск по номеру: <a href="https://patents.google.com/?q=201330414992" target="_blank" rel="noopener noreferrer">Google Patents</a>.</p>
<p>Патент РФ № 232093 уже лежит в открытых сертификатах карточки стелек.</p>

<h3>Эликсир Фохоу</h3>
<p>Награды эликсира Фохоу (форма выпуска — оральная жидкость / флаконы). Китайский лист состава уже в открытых сертификатах карточки.</p>
<p><strong>2023 — «Новая экономическая доска почёта · выдающийся продукт по ценности».</strong> Награда эликсира Фохоу (赛普曼®灵芝香菇蝙蝠蛾拟青霉口服液).</p>
<p><a href="https://www.foherb.cn/index.php?a=cdetail&amp;c=main&amp;id=356" target="_blank" rel="noopener noreferrer">Статья: эликсир получил «2023新经济风云榜 · 杰出价值产品»</a></p>
<p><strong>2020 — «Знаковый продукт отрасли за 30 лет».</strong> Награда эликсира Фохоу.</p>
<p><a href="http://www.cndsn.com.cn/company/2020/1130/113166.html" target="_blank" rel="noopener noreferrer">Статья: эликсир получил «2020新经济风云榜中国直销30年行业标志产品»</a></p>
<p><strong>2019 — «Годовой инновационный продукт» форума Боао New Retail.</strong> Награда эликсира Фохоу.</p>
<p><a href="http://www.cndsn.com.cn/company/2019/1122/103880.html" target="_blank" rel="noopener noreferrer">Статья: эликсир получил «2019博鳌新零售高峰论坛 · 年度产品创新力»</a></p>
$awards$
)
on conflict (tenant_id, content_key) do update
set scope = excluded.scope,
    status = excluded.status,
    kind = excluded.kind,
    title = excluded.title,
    body_html = excluded.body_html,
    updated_at = now();

commit;
