"""Add /lead L-... сумма 123 BYN to the existing live lead queue."""
import importlib.util, json
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent

def helper():
    p = BASE / 'publish_and_run_whieda_sync_2026-07-13.py'
    s = importlib.util.spec_from_file_location('w', p); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m

def main():
    h = helper(); q = h.login_session(); wid = 'advisor-whieda-phase1'
    w = q.get(f'{h.BASE_URL}/rest/workflows/{wid}', verify=False, timeout=60).json()['data']
    b = BASE.parent / 'backups'; b.mkdir(parents=True, exist_ok=True)
    bp = b / f'advisor-whieda-before-lead-amount-{datetime.now():%Y%m%d-%H%M%S}.json'; bp.write_text(json.dumps(w, ensure_ascii=False, indent=2), encoding='utf-8')
    ns = {n['name']: n for n in w['nodes']}; r = ns['Code: Route /leads']; c = r['parameters']['jsCode']
    marker = "const detailMatch = text.match(/^\\/lead(?:@\\w+)?\\s+(L-[A-Z0-9]{6,24})\\s*$/i);"
    add = "const amountMatch = text.match(/^\\/lead(?:@\\w+)?\\s+(L-[A-Z0-9]{6,24})\\s+(?:сумма|sum)\\s+(\\d+(?:[.,]\\d{1,2})?)\\s*(BYN|RUB|W\\$)\\s*$/i);"
    if add not in c: c = c.replace(marker, marker + '\\n' + add)
    c = c.replace("is_lead_action: Boolean(actionMatch),\\n  is_lead_detail: Boolean(detailMatch),\\n  is_leads_control: isLeads || isReport || Boolean(actionMatch) || Boolean(detailMatch),", "is_lead_action: Boolean(actionMatch),\\n  is_lead_amount: Boolean(amountMatch),\\n  is_lead_detail: Boolean(detailMatch),\\n  is_leads_control: isLeads || isReport || Boolean(actionMatch) || Boolean(amountMatch) || Boolean(detailMatch),")
    c = c.replace("leads_access: Boolean((isLeads || isReport || actionMatch || detailMatch) && hasAccess),", "leads_access: Boolean((isLeads || isReport || actionMatch || amountMatch || detailMatch) && hasAccess),")
    c = c.replace("lead_public_id: actionMatch ? actionMatch[1].toUpperCase() : (detailMatch ? detailMatch[1].toUpperCase() : ''),\\n  lead_target_status: actionMatch ? actionMatch[2].toLowerCase() : '',", "lead_public_id: actionMatch ? actionMatch[1].toUpperCase() : (amountMatch ? amountMatch[1].toUpperCase() : (detailMatch ? detailMatch[1].toUpperCase() : '')),\\n  lead_target_status: actionMatch ? actionMatch[2].toLowerCase() : '',\\n  lead_amount_value: amountMatch ? Number(String(amountMatch[2]).replace(',', '.')) : null,\\n  lead_amount_currency: amountMatch ? String(amountMatch[3]).toUpperCase() : '',")
    r['parameters']['jsCode'] = c
    if 'IF: Is lead amount action?' not in ns:
        cred = ns['Postgres: Update lead status']['credentials']
        w['nodes'] += [
          {'id':'lead-amount-if','name':'IF: Is lead amount action?','type':'n8n-nodes-base.if','typeVersion':2.2,'position':[4500,760],'parameters':{'conditions':{'conditions':[{'leftValue':'={{ $json.is_lead_amount }}','operator':{'type':'boolean','operation':'true','singleValue':True},'rightValue':True}],'combinator':'and','options':{'version':2,'typeValidation':'strict'}}}},
          {'id':'lead-amount-sql','name':'Postgres: Update lead amount','type':'n8n-nodes-base.postgres','typeVersion':2.6,'position':[4720,680],'credentials':cred,'parameters':{'operation':'executeQuery','query':"UPDATE website_leads SET metadata=COALESCE(metadata, '{}'::jsonb)||jsonb_build_object('recorded_amount', {{ Number($json.lead_amount_value || 0) }}, 'recorded_currency', '{{ String($json.lead_amount_currency || '').replace(/'/g, '') }}', 'recorded_amount_updated_at', now()::text), updated_at=now(), last_touch_at=now() WHERE tenant_id='whieda' AND public_id='{{ String($json.lead_public_id).replace(/'/g, '') }}' AND ('{{ String($json.lead_actor_id).replace(/'/g, '') }}'='viktor' OR assigned_owner_id='{{ String($json.lead_actor_id).replace(/'/g, '') }}') RETURNING public_id, metadata->>'recorded_amount' AS recorded_amount, metadata->>'recorded_currency' AS recorded_currency;",'options':{}}},
          {'id':'lead-amount-format','name':'Code: Format lead amount','type':'n8n-nodes-base.code','typeVersion':2,'position':[4940,680],'parameters':{'jsCode':"const ctx=$('Code: Route /leads').first().json; const x=$input.first()?.json; const text=x?.public_id ? `В заявке <b>${x.public_id}</b> записана сумма: <b>${x.recorded_amount} ${x.recorded_currency}</b>.` : 'Не удалось записать сумму: заявка не найдена или недоступна вам.'; return [{json:{external_chat_id:ctx.external_chat_id,telegram_text:text,reply_markup:{inline_keyboard:[]}}}];"}},
        ]
        con = w['connections']; con['IF: Is /report?']['main'][1] = [{'node':'IF: Is lead amount action?','type':'main','index':0}]
        con['IF: Is lead amount action?'] = {'main':[[{'node':'Postgres: Update lead amount','type':'main','index':0}],[{'node':'IF: Is /lead detail?','type':'main','index':0}]]}
        con['Postgres: Update lead amount'] = {'main':[[{'node':'Code: Format lead amount','type':'main','index':0}]]}
        con['Code: Format lead amount'] = {'main':[[{'node':'Telegram: Send /leads','type':'main','index':0}]]}
    saved=q.patch(f'{h.BASE_URL}/rest/workflows/{wid}',json=w,verify=False,timeout=120); saved.raise_for_status(); v=saved.json().get('data',saved.json()).get('versionId')
    q.post(f'{h.BASE_URL}/rest/workflows/{wid}/activate',json={'versionId':v},verify=False,timeout=60).raise_for_status(); h.ssh_run(f'docker exec n8n-n8n-1 n8n publish:workflow --id={wid}'); h.ssh_run('docker restart n8n-n8n-1'); h.wait_for_n8n_ready()
    print(json.dumps({'version_id':v,'backup':str(bp)},ensure_ascii=False))

if __name__ == '__main__': main()
