"""The engine, mapped by domain (ADR-0031 §4.3).

Top level holds the handle and the seams; every other module lives in the folder that owns its
reason to change::

    agent.py          the handle: door, identity, lifecycle; the turn phases live in turn/
    inbound.py        the body seam: Percept, Action, the protocols, refusal
    plugins.py        plugin loader (manifest, digest, one import)
    registry.py       Handler and Registry: the discovery contract
    llm.py            the model client, tool loop, cost rows
    config.py  db.py  schema.py  soul.py     configuration, storage, soul
    embed.py  i18n.py  io.py  runtime.py  self_knowledge.py

    turn/       one message becomes one reply: context, decision, assembly, salience,
                prompts, reply, research, observability, plus the phases loop (the turn state
                machine), intake (identity and watchdog), delivery (the outbound path),
                commands (command dispatch), steps (step bookkeeping), and reload (validating
                the operator files and swapping the hot runtime)
    character/  character polish and detector lexicon
    access/     who may do what: roles, permissions, identity, privacy, approvals,
                relay, target
    jobs/       background work and lifecycle: cognition, automation, supervisor,
                lease, ambient, mcp
    memory/  stores/  tools/  commands/  guard/     fixed contracts, unchanged

A module has one reason to change and stays at or below 800 lines (ADR-0031 §4.1); the growth
budget in §4.5 says a new engine module replaces one, splits an oversized one, or the ADR that
introduces it names the reason.
"""
