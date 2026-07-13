"""AGENTIC LAYER — the cognition.

Three agents, one loop. The conversational agent is the daily interface; the reactive
agent wakes on events; the scheduled agent runs on a clock. They differ only in trigger,
prompt weight, and output channel — the cognitive engine (loop.py) is identical. They are
NOT separate deployments: async functions in one service, each invoked with a different
system prompt against the same vault, tools, and loop.
"""
