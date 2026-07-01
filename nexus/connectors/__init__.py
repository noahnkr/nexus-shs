"""EXTERNAL (§4) + INGRESS (§5) LAYERS.

`ingress/` is the reusable front door (domain-neutral). Each `<source>/` package is a
connector to one external system. The ingress layer and agent loop never import a specific
connector — they depend only on the contract (Stimulus + the registry maps).
"""
