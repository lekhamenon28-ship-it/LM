import importlib.util,json,os,subprocess,sys,tempfile
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('gateway','gateway/agent.py');g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
job={'agentRef':'existing','mode':'observe','input':{'task':'check'}}
profiles={'existing':{'mode':'observe','command':[sys.executable,'gateway/python_entrypoint.py','--module','example_agent']}}
result=g.execute_python(job,profiles);assert result['ok'];assert result['output']['request']==job['input']
assert g.execute_python(job,{})['error']=='not-configured'
assert g.execute_python({**job,'mode':'change'},profiles)['error']=='mode-mismatch'
with tempfile.TemporaryDirectory() as d:
    script=os.path.join(d,'test_agent.py')
    def check(source):
        with open(script,'w') as f:f.write(source)
        return g.execute_python(job,{'existing':{'command':[sys.executable,script]}})
    assert check('print("invalid JSON")')['error']=='invalid-output'
    assert check('raise SystemExit(1)')['error']=='execution-failed'
    assert check('print("\\\""+"a"*17000+"\\\"")')['error']=='invalid-output'
    os.environ['AETHER_GATEWAY_TOKEN']='secret-gateway'
    assert check('import os,json; print(json.dumps({"token_present":"AETHER_GATEWAY_TOKEN" in os.environ}))')['output']['token_present'] is False
    module=os.path.join(d,'async_agent.py')
    with open(module,'w') as f:f.write('async def run(payload):\n print("agent log")\n return {"async":True,"input":payload}\n')
    output=subprocess.run([sys.executable,os.path.abspath('gateway/python_entrypoint.py'),'--module','async_agent'],cwd=d,env={**os.environ,'PYTHONPATH':d},input=b'{"task":"check"}',capture_output=True,check=True)
    assert json.loads(output.stdout)['async'];assert b'agent log' in output.stderr
with patch.object(g.subprocess,'run',side_effect=subprocess.TimeoutExpired('agent',60)):
    assert g.execute_python(job,profiles)['error']=='timeout'
print('PASS actual callable execution, JSON stdin/stdout, missing aliases, modes, invalid/large output, nonzero exits, token isolation, async callables and timeout handling')
