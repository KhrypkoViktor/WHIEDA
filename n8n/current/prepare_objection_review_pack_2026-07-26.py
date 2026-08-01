"""Create a role-based review pack from raw objection candidates; never publish."""
import csv, json, re
from collections import Counter
from pathlib import Path

ROOT = Path(r"D:\Projects\WHIEDA")
SRC = ROOT / "RAG" / "1 компиляция. диалоги с врачами" / "batch_objections.jsonl"
OUT = ROOT / "n8n" / "live-exports" / "2026-07-26" / "WHIEDA_objection_review_pack_2026-07-26.csv"
MED = re.compile(
    r"здоров|вред|безопас|врач|подолог|диокс|sl[sс]|состав|беремен|ребен|ребён|"
    r"леч|болезн|эффект|графен|кровоточ|пародонт|кист|тромб|зуд|онкол|рак|"
    r"воспален|боль|орган|мышц|физиотерап|амплипульс|растворя|клетк|"
    r"излучен|противопоказ|дозиров|анализ|водородн|заявлен.*результат",
    re.I,
)
OPS = re.compile(r"поддел|маркетплейс|wildberries|ozon|достав|налич|заказ|оригинал", re.I)
QUALITY = re.compile(r"слом|ржав|качеств|формул|состав|упаков|брак|дужк", re.I)

rows=[]
for line in SRC.read_text(encoding='utf-8').splitlines():
    item=json.loads(line); text=item.get('objection','')
    if MED.search(text): owner, lane='medical_reviewer','medical_or_claim_review'
    elif OPS.search(text): owner, lane='admin','operations_or_brand_fact_check'
    elif QUALITY.search(text): owner, lane='product_owner','product_quality_fact_check'
    else: owner, lane='business_leader','business_voice_review'
    rows.append({'source_id':item.get('source_id',''),'category':item.get('category',''),'objection':text,'raw_quote':item.get('raw_quote',''),'owner':owner,'review_lane':lane,'publication':'do_not_publish_without_approval'})

with OUT.open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
print(json.dumps({'total':len(rows),'by_owner':Counter(x['owner'] for x in rows),'path':str(OUT)},ensure_ascii=False))
