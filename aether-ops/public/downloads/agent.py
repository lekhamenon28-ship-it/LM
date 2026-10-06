#!/usr/bin/env python3
"""Outbound-only infrastructure and Python agent gateway. Python 3.10+, standard library."""
import argparse, base64, json, os, ssl, subprocess, tempfile, time, urllib.request, urllib.parse

def http(url, method='GET', headers=None, data=None, context=None):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            raise ValueError('Redirect refused: configure the final endpoint')
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=context or ssl.create_default_context()))
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, method=method, headers=headers or {})
    with opener.open(req, timeout=25) as response:
        raw=response.read(1024*1024+1)
        if len(raw)>1024*1024: raise ValueError('Response exceeds 1 MiB; reduce the API page size')
        return json.loads(raw) if raw else {}

def collect(job, profiles):
    p=profiles[job['credentialRef']]
    if p['product']!=job['product']: raise ValueError('Profile product mismatch')
    if 'command' in p:
        # Commands are administrator-installed local adapters, never supplied by cloud jobs.
        if not isinstance(p['command'], list) or not all(isinstance(x,str) for x in p['command']): raise ValueError('command must be an argv array')
        result=subprocess.run(p['command'], timeout=40, capture_output=True, check=True)
        if len(result.stdout)>1024*1024: raise ValueError('Adapter response exceeds 1 MiB')
        items=json.loads(result.stdout)
        if not isinstance(items,list): raise ValueError('Adapter must output a JSON array')
        return items[:50]
    base=p['endpoint'].rstrip('/')
    url=urllib.parse.urlparse(base)
    if url.scheme!='https' or not url.hostname or url.username: raise ValueError('Endpoint must be HTTPS without credentials in URL')
    context=ssl.create_default_context(cafile=p.get('caFile'))
    headers={'Accept':'application/json'}
    mode=p.get('auth','none')
    if mode=='basic':
        secret=os.environ[p['passwordEnv']]
        headers['Authorization']='Basic '+base64.b64encode((p['username']+':'+secret).encode()).decode()
    elif mode in ('bearer','api-token'):
        headers['Authorization']=('Bearer ' if mode=='bearer' else 'Api-Token ')+os.environ[p['tokenEnv']]
    elif mode!='none': raise ValueError('Unsupported authentication mode')
    product=p['product']; session=None
    paths={'netapp':'/api/storage/volumes?max_records=50','kubernetes':'/api/v1/nodes?limit=50','servicenow':'/api/now/table/cmdb_ci?sysparm_limit=50&sysparm_fields=sys_id,name,sys_class_name,operational_status','dynatrace':'/api/v2/entities?entitySelector=type%28%22HOST%22%29&pageSize=50','vmware':'/api/vcenter/vm'}
    if product=='vmware':
        session=http(base+'/api/session','POST',headers,context=context)
        headers={'Accept':'application/json','vmware-api-session-id':session}
    path=p.get('inventoryPath',paths.get(product))
    if not path or not path.startswith('/') or path.startswith('//') or '#' in path: raise ValueError('Configure inventoryPath or a local command adapter for this product')
    try:
        data=http(base+path,headers=headers,context=context)
    finally:
        if session:
            try: http(base+'/api/session','DELETE',headers,context=context)
            except Exception: pass
    key=p.get('itemsKey',{'netapp':'records','kubernetes':'items','servicenow':'result','dynatrace':'entities'}.get(product))
    if key:
        for part in key.split('.'): data=data[part]
    if not isinstance(data,list): raise ValueError('inventory response must be an array; configure itemsKey')
    output=[]
    for item in data[:50]:
        meta=item.get('metadata',{})
        output.append({'id':str(item.get('id') or item.get('uuid') or item.get('sys_id') or item.get('entityId') or item.get('vm') or meta.get('uid') or item.get('name','')),'name':str(item.get('name') or item.get('displayName') or meta.get('name') or item.get('id','resource')),'type':str(item.get('sys_class_name') or product),'status':str(item.get('power_state') or item.get('state') or item.get('operational_status') or 'discovered')})
    return output

def execute_python(job, agents):
    p=agents.get(job['agentRef'])
    if not p: return {'ok':False,'error':'not-configured'}
    if p.get('mode','observe')!=job['mode'] or job['mode']=='change' and p.get('allowChanges') is not True:
        return {'ok':False,'error':'mode-mismatch'}
    command=p.get('command')
    if not isinstance(command,list) or not command or not all(isinstance(x,str) for x in command):
        return {'ok':False,'error':'not-configured'}
    try:
        # Local allowlisted argv only. Cloud input is JSON on stdin, never shell text.
        env=os.environ.copy();env.pop('AETHER_GATEWAY_TOKEN',None)
        with tempfile.TemporaryFile() as output:
            completed=subprocess.run(command,input=json.dumps(job['input']).encode(),stdout=output,stderr=subprocess.DEVNULL,cwd=p.get('cwd'),timeout=60,env=env)
            if completed.returncode!=0: return {'ok':False,'error':'execution-failed'}
            output.seek(0);raw=output.read(16001)
            if len(raw)>16000: return {'ok':False,'error':'invalid-output'}
            try: value=json.loads(raw)
            except (ValueError,UnicodeError): return {'ok':False,'error':'invalid-output'}
            # Avoid nonstandard NaN/Infinity output and account for UTF-8 JSON expansion.
            if len(json.dumps(value,allow_nan=False))>16000: return {'ok':False,'error':'invalid-output'}
            return {'ok':True,'output':value}
    except subprocess.TimeoutExpired: return {'ok':False,'error':'timeout'}
    except Exception: return {'ok':False,'error':'execution-failed'}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);args=parser.parse_args()
    with open(args.config) as f: config=json.load(f)
    site=config['site'].rstrip('/')
    if urllib.parse.urlparse(site).scheme!='https' and os.environ.get('AETHER_GATEWAY_ALLOW_LOCAL_HTTP')!='1': raise ValueError('Hosted site must use HTTPS')
    headers={'Authorization':'Bearer '+os.environ['AETHER_GATEWAY_TOKEN'],'Content-Type':'application/json'}
    print('Gateway started; outbound polling only. Press Ctrl+C to stop.',flush=True)
    while True:
        try:
            job=http(site+'/api/gateway/poll','POST',headers,{'capabilities':['python-agents']})['job']
            if job and job.get('kind')=='python-agent':
                started=time.monotonic()
                result=execute_python(job,config.get('agents',{}))
                result['durationMs']=round((time.monotonic()-started)*1000)
                if result.get('ok') and isinstance(result.get('output'),dict) and '_aether_telemetry' in result['output']:
                    result['usage']=result['output']['_aether_telemetry']
                http(site+'/api/gateway/python/result','POST',headers,{'jobId':job['id'],**result})
            elif job:
                try: items=collect(job,config['profiles']); ok=True
                except Exception as exc:
                    # Avoid printing vendor responses, command output, URLs, or secrets.
                    print('Local adapter failed:',type(exc).__name__, 'profile:',job['credentialRef'],flush=True);items=[];ok=False
                http(site+'/api/gateway/result','POST',headers,{'jobId':job['id'],'ok':ok,'items':items})
        except KeyboardInterrupt: break
        except Exception as exc: print('Gateway communication failed:',type(exc).__name__,flush=True)
        time.sleep(5)
if __name__=='__main__': main()
