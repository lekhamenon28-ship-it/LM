#!/usr/bin/env python3
"""Adapt an existing sync/async Python callable to JSON stdin/stdout."""
import argparse, asyncio, contextlib, importlib, inspect, json, sys

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--module',required=True);parser.add_argument('--function',default='run');args=parser.parse_args()
    payload=json.load(sys.stdin)
    # Existing agent print statements go to stderr, leaving the JSON result clean.
    with contextlib.redirect_stdout(sys.stderr):
        function=getattr(importlib.import_module(args.module),args.function)
        result=function(payload)
        if inspect.isawaitable(result): result=asyncio.run(result)
    json.dump(result,sys.stdout,allow_nan=False)
if __name__=='__main__': main()
