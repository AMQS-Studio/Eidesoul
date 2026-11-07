"""Discord gateway: a `discord.py` body behind a narrow, fakeable transport seam.

ADR-0010 §4.4. The SDK is imported lazily inside `transport.py`, so importing this package (for
its settings, its role defaults, or its tests) never requires the `discord` extra. Everything
that decides what a user sees is pure and lives in `render.py` and `ingress.py`.
"""
