"""Repair the literal newline escape introduced in the lead command router."""
import importlib.util, json
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
def main():
    p=BASE/'publish_and_run_whieda_sync_2026-07-13.py'; s=importlib.util.spec_from_file_location('w',p); h=importlib.util.module_from_spec(s); s.loader.exec_module(h)
    q=h.login_session(); wid='advisor-whieda-phase1'; w=q.get(f'{h.BASE_URL}/rest/workflows/{wid}',verify=False,timeout=60).json()['data']
    b=BASE.parent/'backups'; b.mkdir(parents=True,exist_ok=True); bp=b/f'advisor-whieda-before-lead-syntax-repair-{datetime.now():%Y%m%d-%H%M%S}.json'; bp.write_text(json.dumps(w,ensure_ascii=False,indent=2),encoding='utf-8')
    r=next(n for n in w['nodes'] if n['name']=='Code: Route /leads'); c=r['parameters']['jsCode']
    c=c.replace(';\\nconst amountMatch', ';\nconst amountMatch')
    c=c.replace("is_lead_action: Boolean(actionMatch),\n  is_lead_detail: Boolean(detailMatch),\n  is_leads_control: isLeads || isReport || Boolean(actionMatch) || Boolean(detailMatch),", "is_lead_action: Boolean(actionMatch),\n  is_lead_amount: Boolean(amountMatch),\n  is_lead_detail: Boolean(detailMatch),\n  is_leads_control: isLeads || isReport || Boolean(actionMatch) || Boolean(amountMatch) || Boolean(detailMatch),")
    c=c.replace("lead_public_id: actionMatch ? actionMatch[1].toUpperCase() : (detailMatch ? detailMatch[1].toUpperCase() : ''),\n  lead_target_status: actionMatch ? actionMatch[2].toLowerCase() : '',", "lead_public_id: actionMatch ? actionMatch[1].toUpperCase() : (amountMatch ? amountMatch[1].toUpperCase() : (detailMatch ? detailMatch[1].toUpperCase() : '')),\n  lead_target_status: actionMatch ? actionMatch[2].toLowerCase() : '',\n  lead_amount_value: amountMatch ? Number(String(amountMatch[2]).replace(',', '.')) : null,\n  lead_amount_currency: amountMatch ? String(amountMatch[3]).toUpperCase() : '',")
    r['parameters']['jsCode']=c
    saved=q.patch(f'{h.BASE_URL}/rest/workflows/{wid}',json=w,verify=False,timeout=120); saved.raise_for_status(); v=saved.json().get('data',saved.json()).get('versionId')
    q.post(f'{h.BASE_URL}/rest/workflows/{wid}/activate',json={'versionId':v},verify=False,timeout=60).raise_for_status(); h.ssh_run(f'docker exec n8n-n8n-1 n8n publish:workflow --id={wid}'); h.ssh_run('docker restart n8n-n8n-1'); h.wait_for_n8n_ready(); print(json.dumps({'version_id':v,'backup':str(bp)},ensure_ascii=False))
if __name__=='__main__': main()
