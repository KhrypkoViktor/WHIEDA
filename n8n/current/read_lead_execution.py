import importlib.util, json, sys, os
os.chdir(r"D:\Projects\WHIEDA\n8n\current")
spec=importlib.util.spec_from_file_location('h','publish_and_run_whieda_sync_2026-07-13.py'); h=importlib.util.module_from_spec(spec); spec.loader.exec_module(h)
s=h.login_session()
r=s.get(f"{h.BASE_URL}/rest/executions", params={"filter":json.dumps({"workflowId":"wwc-website-leads-p0"}),"limit":1}, verify=False, timeout=30).json()['data']
execs=r['results'] if 'results' in r else r
eid=sys.argv[1] if len(sys.argv)>1 else execs[0]['id']
d=s.get(f"{h.BASE_URL}/rest/executions/{eid}", verify=False, timeout=30).json()['data']
flat=json.loads(d['data'])
def un(x):
    if isinstance(x,str) and x.isdigit() and int(x)<len(flat): return un(flat[int(x)])
    if isinstance(x,list): return [un(i) for i in x]
    if isinstance(x,dict): return {k:un(v) for k,v in x.items()}
    return x
run=un(flat[0])['resultData']['runData']
def out(n):
    try: return run[n][0]['data']['main'][0][0]['json']
    except Exception as ex: return {'_err':str(ex)}
v=out('Code: validate and assign owner'); sv=out('Postgres: save lead and audit'); pr=out('Postgres: prepare lead delivery')
print(json.dumps({'execution':eid,'started':d.get('startedAt'),'name':v.get('name'),
 'refs':{k:v.get(k) for k in ('first_ref_code','active_ref_code','host_ref_code')},
 'routing_version':(v.get('metadata') or {}).get('routing_version'),
 'saved':{k:sv.get(k) for k in ('public_id','assigned_owner_id','created','_err')},
 'delivery':{k:pr.get(k) for k in ('actor_id','telegram_chat_id','kind','_err')}},ensure_ascii=False,indent=1))
