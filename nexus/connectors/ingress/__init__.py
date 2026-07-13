"""INGRESS LAYER — the single front door.

One entry point. Everything arrives as a Stimulus. The layer authenticates, normalizes,
classifies risk deterministically, logs unconditionally, ACKs fast, and dispatches the
right agent — and nothing it does is domain-specific. Almost entirely reusable as-is.
"""
