#!/usr/bin/env python3
"""Runnable onboarding example. Replace run() with your existing agent callable."""
import platform

def run(payload):
    return {'agent':'Python infrastructure agent','host':platform.node(),'request':payload,'summary':'Existing Python callable executed successfully.'}
