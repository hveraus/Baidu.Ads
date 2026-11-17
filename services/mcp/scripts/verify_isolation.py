"""Repeatable local-only isolation acceptance; never injects production faults."""
import argparse
from datetime import datetime,timezone
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import unittest
import warnings

ROOT=Path(__file__).resolve().parents[3]


class RecordingResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.checks=[]
    def addSuccess(self,test):
        super().addSuccess(test);self.checks.append({'test':test.id(),'passed':True})
    def addFailure(self,test,err):
        super().addFailure(test,err);self.checks.append({'test':test.id(),'passed':False,'error_type':err[0].__name__})
    def addError(self,test,err):
        super().addError(test,err);self.checks.append({'test':test.id(),'passed':False,'error_type':err[0].__name__})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'work/mcp-verification/isolation-summary.json')
    args=parser.parse_args()
    logging.disable(logging.CRITICAL);warnings.filterwarnings('ignore')
    sys.path.insert(0,str(ROOT/'services/mcp/tests'))
    suite=unittest.defaultTestLoader.discover(str(ROOT/'services/mcp/tests'),pattern='test_fault_isolation.py')
    with open(os.devnull,'w') as quiet:
        result=unittest.TextTestRunner(stream=quiet,verbosity=0,resultclass=RecordingResult).run(suite)
    report={'schema_version':'1','verified_at':datetime.now(timezone.utc).isoformat(),
        'scope':'local_synthetic_fault_injection_only','production_fault_injection':False,
        'status':'PASS' if result.wasSuccessful() else 'FAIL','checks':result.checks,
        'proof_limits':[
            'Core synthetic storage and read probes remained unchanged; this is not a real dashboard/login/collection availability baseline.',
            'Actual local MCP process was initialized then forcibly terminated; reader stop/slow behavior used an injected HTTP transport.',
            'Core imports, requirements, core PowerShell setup tasks and core publishing workflows were statically checked for MCP dependencies.',
        ],
        'requires_preproduction_acceptance':[
            'Separate MCP host power loss/outage and core host independence under actual Windows deployment.',
            'OS-enforced CPU, memory, disk quotas and actual resource exhaustion; Windows ACL denial of writes to core DB/reports/task configuration.',
            'Core login, all dashboards, collection start/end and publication correctness across one complete daily collection cycle.',
            'Comparable load baseline and repeated P95 measurements: core regression <=5%, 10 concurrent MCP queries and cache P95 target.',
            'Private ingress/TLS failures, Windows DPAPI bootstrap identity, service-account privilege boundaries and reader stop under live traffic.',
            'Actual Baidu/provider throttling and shared quota reservation; unverified upstream write capabilities must remain disabled.',
            'Real client OAuth login/refresh/revocation and identity-source flooding under deployment rate limits.',
        ]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as target:json.dump(report,target,ensure_ascii=False,indent=2)
    args.output.chmod(0o600)
    print(f"{report['status']}: {sum(c['passed'] for c in result.checks)}/{len(result.checks)} local fault checks; no production services touched.")
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__=='__main__':main()
