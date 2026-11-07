"""One MetaData for all soul state; Postgres types only (ADR-0002).

The table objects live in sibling modules by domain; this module is the public surface, so
`from .schema import <table>` keeps working exactly as it did when the definitions shared one
file. Every module mutates the same `metadata` instance.
"""

from .automation import AUTOMATION_RUN_STATUSES as AUTOMATION_RUN_STATUSES
from .automation import AUTOMATION_STATES as AUTOMATION_STATES
from .automation import AUTOMATION_STEP_KINDS as AUTOMATION_STEP_KINDS
from .automation import AUTOMATION_TERMINAL as AUTOMATION_TERMINAL
from .automation import automation_runs as automation_runs
from .automation import automation_state_log as automation_state_log
from .automation import automations as automations
from .base import MEMORY_DIM as MEMORY_DIM
from .base import MEMORY_TSV as MEMORY_TSV
from .base import Vector as Vector
from .base import actions as actions
from .base import approvals as approvals
from .base import attachments as attachments
from .base import documents as documents
from .base import llm_calls as llm_calls
from .base import message_media as message_media
from .base import messages as messages
from .base import metadata as metadata
from .base import relay_grants as relay_grants
from .base import schema_meta as schema_meta
from .base import state as state
from .base import threads as threads
from .cognition import COGNITION_KINDS as COGNITION_KINDS
from .cognition import COGNITION_STATUSES as COGNITION_STATUSES
from .cognition import EPISODE_SIGNALS as EPISODE_SIGNALS
from .cognition import EPISODE_STATUSES as EPISODE_STATUSES
from .cognition import cognition_jobs as cognition_jobs
from .cognition import episodes as episodes
from .memory import READABLE_VIEW as READABLE_VIEW
from .memory import READABLE_VIEW_SQL as READABLE_VIEW_SQL
from .memory import memories as memories
from .memory import memories_readable as memories_readable
from .memory import memory_events as memory_events
from .memory import memory_index as memory_index
from .memory import memory_tombstones as memory_tombstones
from .memory import memory_uses as memory_uses
from .people import config_changes as config_changes
from .people import drives as drives
from .people import engine_control as engine_control
from .people import engine_lease as engine_lease
from .people import people as people
from .people import person_link_codes as person_link_codes
from .people import person_links as person_links
from .people import recipes as recipes
from .people import skill_files as skill_files
from .people import skill_revisions as skill_revisions
from .people import skills as skills
from .people import thread_eras as thread_eras
from .people import thread_people as thread_people
from .people import topics as topics
from .turns import TURN_STATUSES as TURN_STATUSES
from .turns import TURN_STEP_STATUSES as TURN_STEP_STATUSES
from .turns import TURN_STEPS as TURN_STEPS
from .turns import TURN_TERMINAL as TURN_TERMINAL
from .turns import outbound_messages as outbound_messages
from .turns import turn_steps as turn_steps
from .turns import turns as turns

__all__ = [
    "AUTOMATION_RUN_STATUSES",
    "AUTOMATION_STATES",
    "AUTOMATION_STEP_KINDS",
    "AUTOMATION_TERMINAL",
    "COGNITION_KINDS",
    "COGNITION_STATUSES",
    "EPISODE_SIGNALS",
    "EPISODE_STATUSES",
    "MEMORY_DIM",
    "MEMORY_TSV",
    "READABLE_VIEW",
    "READABLE_VIEW_SQL",
    "TURN_STATUSES",
    "TURN_STEPS",
    "TURN_STEP_STATUSES",
    "TURN_TERMINAL",
    "Vector",
    "actions",
    "approvals",
    "attachments",
    "automation_runs",
    "automation_state_log",
    "automations",
    "cognition_jobs",
    "config_changes",
    "documents",
    "drives",
    "engine_control",
    "engine_lease",
    "episodes",
    "llm_calls",
    "memories",
    "memories_readable",
    "memory_events",
    "memory_index",
    "memory_tombstones",
    "memory_uses",
    "message_media",
    "messages",
    "metadata",
    "outbound_messages",
    "people",
    "person_link_codes",
    "person_links",
    "recipes",
    "relay_grants",
    "schema_meta",
    "skill_files",
    "skill_revisions",
    "skills",
    "state",
    "thread_eras",
    "thread_people",
    "threads",
    "topics",
    "turn_steps",
    "turns",
]
