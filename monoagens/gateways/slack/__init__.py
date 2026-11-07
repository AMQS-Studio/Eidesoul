"""Slack gateway: Socket Mode or Events API behind a narrow, fakeable transport seam.

ADR-0010 §4.5. `slack-bolt`/`slack-sdk` are imported lazily inside `transport.py`, so importing
this package never requires the `slack` extra. Block Kit rendering and event normalization are
pure and live in `render.py` and `ingress.py`.
"""
