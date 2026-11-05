"""OpenAI-compatible LLM loop with tool calling and a work timeout.

The prompts and payload helpers live in `support`; this module is the public surface so
`from .llm import LLM` and `from .llm import DeadlineExceeded` keep working.
"""

from .client import LLM as LLM
from .support import BUDGET_TEXT as BUDGET_TEXT
from .support import DIGEST_PROMPT as DIGEST_PROMPT
from .support import IMAGE_LOST as IMAGE_LOST
from .support import MEMORY_KINDS as MEMORY_KINDS
from .support import RESEARCH_PROMPT as RESEARCH_PROMPT
from .support import USER_AGENT as USER_AGENT
from .support import DeadlineExceeded as DeadlineExceeded
from .support import parse_digest as parse_digest
from .support import prompt_prefix_hash as prompt_prefix_hash

__all__ = [
    "BUDGET_TEXT",
    "DIGEST_PROMPT",
    "IMAGE_LOST",
    "LLM",
    "MEMORY_KINDS",
    "RESEARCH_PROMPT",
    "USER_AGENT",
    "DeadlineExceeded",
    "parse_digest",
    "prompt_prefix_hash",
]
