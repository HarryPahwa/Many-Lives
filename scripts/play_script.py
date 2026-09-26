"""Scripted play driver (TDD §20.4, §32.2).

Sends a fixed list of fast-path commands against the API to build the demo
campaign deterministically and to smoke-test after every merge. A failure
blocks further merges until fixed.
Scaffold only.
"""
