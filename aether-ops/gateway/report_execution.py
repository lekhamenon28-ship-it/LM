#!/usr/bin/env python3
"""Report completed executions from an existing UI/backend; never executes agents."""
import argparse,json,os,sys,urllib.request,urllib.parse

def report_execution(site,token,agent_ref,execution_id,payload):
    if urllib.parse.urlparse(site).scheme!='https' and os.environ.get('AETHER_GATEWAY_ALLOW_LOCAL_HTTP')!='1':
        raise ValueError('Report destination must use HTTPS')
    data={**payload,'agentRef':agent_ref,'executionId':execution_id}
    request=urllib.request.Request(site.rstrip('/')+'/api/gateway/python/record',data=json.dumps(data,allow_nan=False).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},method='POST')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args):raise ValueError('Report destination redirected')
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=25) as response:
        return json.load(response)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--site',required=True);parser.add_argument('--agent',required=True);parser.add_argument('--execution-id',required=True);args=parser.parse_args()
    print(json.dumps(report_execution(args.site,os.environ['AETHER_GATEWAY_TOKEN'],args.agent,args.execution_id,json.load(sys.stdin))))
if __name__=='__main__':main()
