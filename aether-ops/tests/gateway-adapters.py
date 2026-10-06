import importlib.util, os
spec=importlib.util.spec_from_file_location('gateway','gateway/agent.py');g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
os.environ['TEST_PASSWORD']='secret';os.environ['TEST_TOKEN']='token'
for product,response,auth in [('netapp',{'records':[{'uuid':'v1','name':'volume'}]},'basic'),('kubernetes',{'items':[{'metadata':{'uid':'n1','name':'node'}}]},'bearer'),('servicenow',{'result':[{'sys_id':'c1','name':'server'}]},'basic'),('dynatrace',{'entities':[{'entityId':'h1','displayName':'host'}]},'api-token'),('vmware',[{'vm':'vm1','name':'vm'}],'basic')]:
    calls=[]
    def fake(url,method='GET',headers=None,data=None,context=None):
        calls.append((url,method,headers))
        if method=='POST': return 'session-token'
        if method=='DELETE': return {}
        return response
    g.http=fake
    profile={'product':product,'endpoint':'https://private.example','auth':auth,'username':'reader','passwordEnv':'TEST_PASSWORD','tokenEnv':'TEST_TOKEN'}
    result=g.collect({'product':product,'credentialRef':'prod','operation':'inventory'},{'prod':profile})
    assert len(result)==1 and result[0]['name']
    if product=='vmware': assert calls[0][1]=='POST' and calls[-1][1]=='DELETE' and calls[1][2]['vmware-api-session-id']=='session-token'
    else: assert calls[0][1]=='GET' and 'Authorization' in calls[0][2]
print('PASS five REST adapters map inventory and authentication; vCenter session is closed')
