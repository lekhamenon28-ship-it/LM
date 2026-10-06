#!/usr/bin/env python3
"""Example read-only local Linux inventory adapter (no remote SSH)."""
import json, platform, shutil
usage=shutil.disk_usage('/')
print(json.dumps([{'id':platform.node(),'name':platform.node(),'type':'Linux host','status':platform.release()}, {'id':'root-filesystem','name':'Root filesystem','type':'Filesystem','status':str(round(100*usage.used/usage.total))+'% used'}]))
