"""OpenAI wire: `POST {base_url}/chat/completions` with `Authorization: Bearer $api_key_env`.

Tools travel as `{"type": "function", "function": {...}}` and results come back as
`{"role": "tool", "tool_call_id": ...}`. Every entry here is a base URL that speaks that
shape and nothing else: no model id is pinned, the operator chooses one at init time.
"""


from .base import ProviderDefaults

OPENCODE = ProviderDefaults(
    "https://opencode.ai/inference/go/openai/v1",
    "OPENCODE_GO_API_KEY",
    session_header="x-opencode-session",
    models_url="https://opencode.ai/inference/v1/models",
)
OPENAI = ProviderDefaults("https://api.openai.com/v1", "OPENAI_API_KEY")
OPENROUTER = ProviderDefaults("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY")
GROQ = ProviderDefaults("https://api.groq.com/openai/v1", "GROQ_API_KEY")
DEEPSEEK = ProviderDefaults("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY")
OLLAMA = ProviderDefaults("http://127.0.0.1:11434/v1", "OLLAMA_API_KEY", api_key_optional=True)
CUSTOM = ProviderDefaults("", "")
