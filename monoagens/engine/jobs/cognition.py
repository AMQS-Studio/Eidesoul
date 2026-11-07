"""ADR-0003 cognition worker: one job table, two runners, the claim and the reaper.

Worker C owns `capture`, `consolidate`, `reflect`, and `distill`; the slow lane owns
`backfill` and `audit`. Every job is claimed with `FOR UPDATE SKIP LOCKED` plus a lease, a
failed attempt backs off, and `dead_letter_after` moves it to `dead`. The intake transaction
writes the `capture` job on the same connection as the message, the turn, and the journal, so
the four rows commit or roll back together (ADR-0001 §4.1).

The queue, the scheduling ticks, the handlers, and the ladder live in sibling modules; this
module is the public surface the rest of the engine imports.
"""

from .cognition_queue import DAY as DAY
from .cognition_queue import KINDS as KINDS
from .cognition_queue import SECONDS as SECONDS
from .cognition_queue import TOOL as TOOL
from .cognition_queue import CognitionQueue as CognitionQueue
from .cognition_queue import Job as Job
from .cognition_worker import CognitionWorker as CognitionWorker

__all__ = [
    "DAY",
    "KINDS",
    "SECONDS",
    "TOOL",
    "CognitionQueue",
    "CognitionWorker",
    "Job",
]
