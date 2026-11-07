"""Telegram gateway: a pure-`asyncio` Bot API body (ADR-0010 §4.3).

The transport is the HTTP Bot API over `httpx`, so the body needs no third-party SDK and no
Node.js runtime. Everything that decides what a user sees lives in `render.py`; everything that
turns an update into a `Percept` lives in `ingress.py`; the wire lives in `transport.py`.
"""
