"""The OpenAI-compatible loop: tools, budget, retries, digests, and research summaries."""




import json
import logging
import time

from openai import APIError, APITimeoutError, AsyncOpenAI

from ...providers import (
    ProviderError,
    ProviderResponse,
    build_client,
    message_dict,
    normalize_response,
)
from .. import io
from ..config import Config
from ..db import Database
from ..i18n import t
from ..inbound import bind_tool_action, reset_tool_action
from ..registry import Handler, allows
from ..schema import llm_calls
from ..tools import ToolContext, execute, resolve_spec
from ..turn import observability
from .support import (
    BUDGET_TEXT,
    DIGEST_PROMPT,
    RESEARCH_PROMPT,
    USER_AGENT,
    DeadlineExceeded,
    _client_error,
    _emit,
    _has_images,
    _strip_images,
    parse_digest,
    prompt_prefix_hash,
)

log = logging.getLogger(__name__)


class LLM:
    def __init__(self, config: Config, db: Database | None = None):
        self.config = config
        self.db = db
        self.prefix_hash = ""
        if config.llm.native:
            self.client = build_client(
                config.llm.wire,
                config.llm.base_url,
                config.api_key(),
                timeout=config.llm.timeout_seconds,
            )
        else:
            self.client = AsyncOpenAI(
                base_url=config.llm.base_url,
                api_key=config.api_key(),
                timeout=config.llm.timeout_seconds,
                default_headers={"User-Agent": USER_AGENT},
            )

    def _tool_specs(self, specs: list[Handler]) -> list[dict] | None:
        if not specs:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in specs
        ]

    def _effort(self) -> dict[str, str]:
        """The configured reasoning effort, or nothing so the provider default applies."""
        effort = self.config.llm.reasoning_effort
        return {"reasoning_effort": effort} if effort else {}

    def _request_client(self, deadline: float | None, min_call_seconds: float):
        """Per-call layer: `min(llm.timeout_seconds, remaining)` and the turn retry count."""
        if deadline is None:
            return self.client, None
        remaining = float(deadline) - time.monotonic()
        if remaining < float(min_call_seconds):
            raise DeadlineExceeded(
                f"remaining {remaining:.1f}s is below min_call_seconds "
                f"{float(min_call_seconds):.1f}s"
            )
        timeout = min(float(self.config.llm.timeout_seconds), max(0.05, remaining))
        attempts = max(1, int(self.config.llm.retry.turn_attempts))
        configured = (
            getattr(self.client, "with_options", None)
            if self.config.llm.wire == "openai"
            else None
        )
        client = (
            configured(timeout=timeout, max_retries=attempts - 1)
            if callable(configured)
            else self.client
        )
        return client, timeout

    def _deadline_request_failed(self, deadline: float | None, min_call_seconds: float) -> bool:
        if deadline is None:
            return False
        return float(deadline) - time.monotonic() < float(min_call_seconds)

    def _system_messages(self, system) -> list[dict]:
        """Two system messages when the prompt is sectioned and caching is on (ADR-0025 §4.1)."""
        stable = getattr(system, "stable", "")
        volatile = getattr(system, "volatile", "")
        if stable and volatile and self.config.llm.prompt_cache:
            return [
                {"role": "system", "content": stable},
                {"role": "system", "content": volatile},
            ]
        return [{"role": "system", "content": str(system)}]

    def _merge_system(self, messages: list[dict], exc: Exception) -> bool:
        """A provider that refuses two system messages gets the merged form (cache lost)."""
        if (
            len(messages) >= 2
            and messages[0].get("role") == "system"
            and messages[1].get("role") == "system"
            and _client_error(exc)
        ):
            merged = f"{messages[0]['content']}\n\n{messages[1]['content']}"
            messages[:2] = [{"role": "system", "content": merged}]
            log.warning("provider rejected two system messages; merged with a fixed separator")
            return True
        return False

    def _active_specs(self, base: list[Handler], ctx: ToolContext) -> list[Handler]:
        """ADR-0015 §4.4: base specs plus what `info` activated this turn; additive only."""
        active = list(base)
        known = {spec.name for spec in active}
        for name in sorted(ctx.activated):
            if name in known:
                continue
            spec = None
            agent = ctx.agent
            index = getattr(agent, "tool_index", None) if agent is not None else None
            gateway = getattr(getattr(ctx, "gateway", None), "name", "") or ""
            if index is not None:
                spec = index.get(name, gateway)
            hub = getattr(agent, "hub", None) if agent is not None else None
            if spec is None and hub is not None:
                spec = next((item for item in hub.specs(ctx.roles) if item.name == name), None)
            if spec is not None and allows(ctx.allowed, spec.name):
                active.append(spec)
                known.add(spec.name)
        return active

    def _purpose(self, purpose: str, session: str) -> str:
        if purpose:
            return purpose
        text = str(session or "").lower()
        if "character" in text:
            return "polish"
        for key in ("digest", "research", "decision", "extract", "reflect", "label"):
            if key in text:
                return key
        return "complete"

    async def _call(
        self,
        client,
        kwargs: dict,
        *,
        purpose: str,
        turn_id: str = "",
        job_id: int | None = None,
        run_id: str = "",
    ):
        """One provider call, always recorded (success or failure) in `llm_calls`."""
        started = time.monotonic()
        try:
            raw = (
                await client.create(**kwargs)
                if self.config.llm.native
                else await client.chat.completions.create(**kwargs)
            )
            response = normalize_response(raw)
        except Exception:
            await self._record(
                purpose,
                kwargs.get("model", ""),
                None,
                started,
                False,
                turn_id=turn_id,
                job_id=job_id,
                run_id=run_id,
            )
            raise
        await self._record(
            purpose,
            kwargs.get("model", ""),
            response.usage,
            started,
            True,
            turn_id=turn_id,
            job_id=job_id,
            run_id=run_id,
        )
        return response

    async def _record(
        self,
        purpose: str,
        model: str,
        usage,
        started: float,
        ok: bool,
        *,
        turn_id: str = "",
        job_id: int | None = None,
        run_id: str = "",
    ) -> None:
        if self.db is None:
            return
        # Correlation ids default to the current context (ADR-0019 §4.2); an explicit argument
        # wins, and a background task never inherits a finished turn's id.
        turn_id = turn_id or observability.TURN_ID.get()
        if job_id is None:
            job_id = observability.JOB_ID.get()
        run_id = run_id or observability.RUN_ID.get()
        row = {
            "ts": time.time(),
            "turn_id": turn_id or None,
            "job_id": job_id,
            "run_id": run_id or None,
            "purpose": str(purpose or "complete")[:40],
            "model": str(model or "")[:120],
            "tokens_in": getattr(usage, "prompt_tokens", None) if usage else None,
            "tokens_out": getattr(usage, "completion_tokens", None) if usage else None,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "ok": bool(ok),
        }
        try:
            await io.run(self._insert_call, row)
        except Exception:  # metrics never break a turn
            log.debug("llm_calls write failed", exc_info=True)

    def _insert_call(self, row: dict) -> None:
        if self.db is None:
            return
        with self.db.connect() as conn:
            conn.execute(llm_calls.insert().values(row))

    def _sampling_kwargs(self, temperature: float | None, sampling=None) -> dict:
        """Only the sampling keys the provider wire carries (ADR-0044 §4.6).

        A reasoning model rejects `temperature` with a 400; the capability list on the
        provider defaults is the one place that decision is made.
        """
        supported = set(self.config.llm.sampling_supported)
        values: dict = {}
        if temperature is not None and "temperature" in supported:
            values["temperature"] = float(temperature)
        for key in ("top_p", "top_k", "min_p"):
            value = getattr(sampling, key, None) if sampling is not None else None
            if value is not None and key in supported:
                values[key] = value
        return values

    def _request_kwargs(
        self,
        messages,
        session,
        active,
        wrap,
        *,
        temperature: float,
        sampling=None,
    ):
        kwargs = {
            "model": self.config.llm.model,
            "messages": messages,
            "extra_headers": self.config.llm.session_headers(session or USER_AGENT),
            **self._effort(),
            **self._sampling_kwargs(temperature, sampling),
        }
        if self.config.llm.native:
            kwargs["safety"] = self.config.soul.voice.safety
        tools = None if wrap else self._tool_specs(active)
        if tools:
            kwargs["tools"] = tools
        return kwargs

    def _failed(self, exc: Exception | None, deadline, min_call: float) -> str:
        if self._deadline_request_failed(deadline, min_call):
            raise DeadlineExceeded("request consumed the turn deadline") from exc
        log.exception("LLM failed")
        return t("agent.api_error", self.config.soul.language)

    async def _final(
        self,
        response: ProviderResponse,
        messages: list[dict],
        ctx: ToolContext,
        *,
        attempts: int,
        max_calls: int,
        deadline,
        min_call: float,
        session: str,
        purpose: str,
        turn_id: str,
        last_text: str,
        temperature: float,
        sampling=None,
    ) -> str:
        text = response.text
        if not ctx.sent or attempts >= max_calls:
            return text or last_text
        sent_note = "\n".join(
            f"- {entry['tool']} to {entry['target']}: {entry['kind']} {entry['summary']}"
            for entry in ctx.sent
        )
        message = message_dict(response.message) if response.message is not None else {
            "role": "assistant",
            "content": response.text,
        }
        confirm_messages = [
            *messages,
            message,
            {
                "role": "user",
                "content": (
                    "[Already sent via tool; do not resend:\n"
                    f"{sent_note}\nWrite your final answer; briefly confirm what was sent.]"
                ),
            },
        ]
        try:
            client, _ = self._request_client(deadline, min_call)
            confirm = await self._call(
                client,
                self._request_kwargs(
                    confirm_messages, session, [], False,
                    temperature=temperature, sampling=sampling,
                ),
                purpose=purpose,
                turn_id=turn_id,
            )
        except DeadlineExceeded:
            raise
        except (APIError, APITimeoutError, ProviderError, TimeoutError):
            log.warning("send confirmation failed, keeping the original answer")
            return text
        return confirm.text or text

    def _budget_spent(self, start: float, budget: float, min_call: float) -> bool:
        """True when the next round cannot fit the minimum call window (ADR-0003 §4.5)."""
        remaining = budget - (time.monotonic() - start)
        if remaining <= 0.0 or remaining < max(0.0, float(min_call)):
            log.warning("run budget exhausted, %.1fs left", remaining)
            return True
        return False

    def _retry_vision(
        self, exc: Exception, images: bool, messages: list[dict], user_content, wrap: bool,
        attempts: int,
    ) -> tuple[bool, int]:
        """A vision rejection on any round retries once with text-only parts (ADR-0003 §4.5)."""
        if not images or not _client_error(exc):
            return False, attempts
        log.warning(
            "model rejected the images on round %d, retrying without them: %s", attempts, exc
        )
        _strip_images(messages, user_content)
        if wrap and messages and messages[-1].get("content") == BUDGET_TEXT:
            messages.pop()
        return True, max(0, attempts - 1)  # a rejected request is not a round

    async def _progress(
        self, response: ProviderResponse, *, interim: bool, send_thinking, send_interim
    ) -> None:
        """Reasoning and transitional prose are progress, never answer text (ADR-0003 §4.5)."""
        if response.thinking:
            await _emit(send_thinking, response.thinking)
        if interim and response.text.strip():
            await _emit(send_interim, response.text.strip())

    async def _tools(
        self,
        calls,
        by_name: dict[str, Handler],
        ctx: ToolContext,
        messages: list[dict],
        last_sig,
        repeat: int,
        on_step,
        lookup_step,
        send_step=None,
        turn_id: str = "",
    ):
        for call in calls:
            # ADR-0044 §4.2: the loop consumes ProviderToolCall, already normalized. Parsing the
            # raw wire shape here would blank the name and arguments.
            call_id, name = str(call.id or ""), str(call.name or "")
            raw_arguments = str(call.arguments or "{}")
            log.info("tool: %s", name)
            arguments: dict = {}
            status = "failed"
            shown = name
            try:
                args = json.loads(raw_arguments or "{}")
            except json.JSONDecodeError:
                last_sig, repeat = None, 0
                result = "error: invalid JSON arguments"
            else:
                arguments = args if isinstance(args, dict) else {}
                # ADR-0003 §4.3: resolve the alias before the cache, the step log, and the
                # non-idempotent check; an alias never gets a row of its own.
                spec = resolve_spec(by_name, name)
                canonical = str(spec.name) if spec is not None else name
                shown = canonical
                target = str(spec.target) if spec is not None else ""
                approval_class = str(spec.approval_class) if spec is not None else "none"
                sig = (canonical, json.dumps(arguments, sort_keys=True, default=str))
                repeat = repeat + 1 if sig == last_sig else 1
                last_sig = sig
                if repeat >= 3:
                    status = "refused"
                    result = (
                        "error: repeated call, change the arguments or answer "
                        "from the existing result"
                    )
                else:
                    # ADR-0017 §4.1: the step cache is a synchronous DB read, never inline.
                    result = (
                        await io.run(lookup_step, canonical, arguments)
                        if lookup_step is not None
                        else None
                    )
                    if result is None:
                        started = time.monotonic()
                        index = -1
                        begin = getattr(on_step, "begin", None)
                        if begin is not None:
                            # ADR-0003 §4.3: the row is written `running` before the call, so a
                            # crash mid-execution is evidence, never a silent gap.
                            index = await io.run(
                                begin,
                                canonical,
                                arguments,
                                target=target,
                                approval_class=approval_class,
                            )
                        token = (
                            bind_tool_action(f"turn:{turn_id}:tool:{index}")
                            if index >= 0 and turn_id
                            else None
                        )
                        try:
                            result = await execute(by_name, canonical, arguments, ctx)
                        finally:
                            if token is not None:
                                reset_tool_action(token)
                        status = "failed" if str(result).startswith("error:") else "executed"
                        if on_step is not None:
                            # ADR-0017 §4.1: the recorder finalizes `turn_steps` synchronously.
                            await io.run(
                                on_step,
                                canonical,
                                arguments,
                                result,
                                started,
                                time.monotonic(),
                                # ADR-0020 §4.4: a tool error is a failed step, so the episode
                                # can tell a repair from a clean run.
                                status,
                                target,
                                approval_class,
                            )
                    else:
                        status = "cached"
            await _emit(
                send_step,
                {
                    "tool": shown,
                    "arguments": arguments,
                    "result": str(result),
                    "status": status,
                },
            )
            messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
        return last_sig, repeat

    def _base_specs(self, specs: list[Handler]) -> list[Handler]:
        """ADR-0015 §4.4: only hot schemas ride along; the rest is activated through info."""
        base = list(specs)
        if not self.config.tools.activate_on_demand:
            return base
        hot = set(self.config.tools.hot)
        filtered = [spec for spec in base if spec.name in hot]
        return filtered or base

    def _set_prefix_hash(self, messages: list[dict], base_specs, ctx: ToolContext) -> None:
        if not self.config.prompt.prefix_hash:
            return
        active = self._active_specs(base_specs, ctx)
        self.prefix_hash = prompt_prefix_hash(messages[0]["content"], active)
        log.debug("prompt prefix hash %s", self.prefix_hash)

    def _temperature(self, temperature: float | None) -> float:
        if temperature is not None:
            return float(temperature)
        return float(self.config.soul.voice.sampling.daily_temperature)

    async def run(
        self,
        system: str,
        user_content: str | list[dict],
        specs: list[Handler],
        ctx: ToolContext,
        session: str = "",
        *,
        deadline: float | None = None,
        min_call_seconds: float = 0.0,
        on_step=None,
        lookup_step=None,
        send_interim=None,
        send_thinking=None,
        send_step=None,
        turn_id: str = "",
        purpose: str = "reply",
        temperature: float | None = None,
        sampling=None,
    ) -> str:
        temperature = self._temperature(temperature)
        messages: list[dict] = [
            *self._system_messages(system),
            {"role": "user", "content": user_content},
        ]
        images = _has_images(user_content)
        base_specs = self._base_specs(specs)
        self._set_prefix_hash(messages, base_specs, ctx)
        max_calls = max(1, int(self.config.llm.max_calls_per_turn))
        budget = max(0.0, float(self.config.llm.run_budget_seconds))
        attempts = 0
        start = time.monotonic()
        last_text = ""
        last_sig: tuple[str, str] | None = None
        repeat = 0
        while True:
            if attempts >= max_calls:
                return last_text or t("agent.budget_notice", self.config.soul.language)
            # ADR-0003 §4.5: a round that cannot fit the minimum call window is not started,
            # so the turn ends with the best text already produced instead of a hard cutoff.
            if self._budget_spent(start, budget, min_call_seconds):
                return last_text or t("agent.budget_notice", self.config.soul.language)
            client, _timeout = self._request_client(deadline, min_call_seconds)
            wrap = attempts + 1 >= max_calls
            active = self._active_specs(base_specs, ctx)
            by_name = {spec.name: spec for spec in active}
            if wrap:
                messages.append({"role": "system", "content": BUDGET_TEXT})
            kwargs = self._request_kwargs(
                messages, session, active, wrap,
                temperature=temperature, sampling=sampling,
            )
            attempts += 1
            try:
                response = await self._call(
                    client, kwargs, purpose=purpose, turn_id=turn_id
                )
            except (APIError, ProviderError) as exc:
                retry, attempts = self._retry_vision(
                    exc, images, messages, user_content, wrap, attempts
                )
                if retry:  # model without vision, any round
                    images = False
                    continue
                if self._merge_system(messages, exc):
                    continue
                return self._failed(exc, deadline, min_call_seconds)
            except (APITimeoutError, TimeoutError) as exc:
                return self._failed(exc, deadline, min_call_seconds)
            await self._progress(
                response,
                interim=bool(response.tool_calls) and not wrap,
                send_thinking=send_thinking,
                send_interim=send_interim,
            )
            if response.blocked:
                log.warning(
                    "provider blocked the response (finish=%s native=%s)",
                    response.finish_reason,
                    response.native_finish_reason,
                )
                return response.text or t("agent.provider_blocked", self.config.soul.language)
            if wrap:
                return (
                    response.text
                    or last_text
                    or t("agent.budget_notice", self.config.soul.language)
                )
            calls = response.tool_calls
            interim = (
                bool(calls)
                and not wrap
                and bool(send_interim)
                and bool(response.text.strip())
            )
            last_text = _round_text(response.text, last_text, interim=interim)
            if not calls:
                return await self._final(
                    response,
                    messages,
                    ctx,
                    attempts=attempts,
                    max_calls=max_calls,
                    deadline=deadline,
                    min_call=min_call_seconds,
                    session=session,
                    purpose=purpose,
                    turn_id=turn_id,
                    last_text=last_text,
                    temperature=temperature,
                    sampling=sampling,
                )
            messages.append(
                message_dict(response.message)
                if response.message is not None
                else {"role": "assistant", "content": response.text}
            )
            last_sig, repeat = await self._tools(
                calls,
                by_name,
                ctx,
                messages,
                last_sig,
                repeat,
                on_step,
                lookup_step,
                send_step,
                turn_id,
            )

    async def complete(
        self,
        system: str,
        user: str | list[dict],
        *,
        max_tokens: int = 300,
        temperature: float | None = None,
        session: str = "",
        model: str = "",
        deadline: float | None = None,
        min_call_seconds: float = 0.0,
        purpose: str = "",
        turn_id: str = "",
        job_id: int | None = None,
        run_id: str = "",
    ) -> str:
        """One tool-free call for decisions/summaries; failure yields an empty string."""
        temperature = (
            float(temperature)
            if temperature is not None
            else float(self.config.llm.utility_temperature)
        )
        client, _timeout = self._request_client(deadline, min_call_seconds)
        messages = [
            {"role": "system", "content": str(system or "")},
            {"role": "user", "content": user if isinstance(user, list) else str(user or "")},
        ]
        kwargs: dict = {
            "model": model or self.config.llm.model,
            "messages": messages,
            "max_tokens": max(1, int(max_tokens)),
            "extra_headers": self.config.llm.session_headers(session or f"{USER_AGENT}-bg"),
            **self._effort(),
            **self._sampling_kwargs(temperature),
        }
        try:
            response = await self._call(
                client,
                kwargs,
                purpose=self._purpose(purpose, session),
                turn_id=turn_id,
                job_id=job_id,
                run_id=run_id,
            )
        except DeadlineExceeded:
            raise
        except (
            APIError,
            APITimeoutError,
            ProviderError,
            TimeoutError,
            OSError,
        ) as exc:  # background is best-effort
            if self._deadline_request_failed(deadline, min_call_seconds):
                raise DeadlineExceeded("request consumed the turn deadline") from exc
            log.warning("background LLM call failed: %s", exc)
            return ""
        text = response.text
        if not text:  # a reasoning model can spend the budget before writing the answer
            log.warning(
                "background LLM returned nothing (max_tokens=%s finish=%s session=%s)",
                max_tokens,
                response.finish_reason or response.native_finish_reason,
                session or "bg",
            )
        return text

    async def digest(self, thread_name: str, transcript: str, topic: str, people: str) -> dict:
        """Summarise the topic and extract explicit facts from the last 30 messages."""
        user = "\n\n".join(
            part
            for part in (
                f"Thread: {thread_name or '-'}",
                f"Current topic: {topic}" if topic.strip() else "",
                f"Known people: {people}" if people.strip() else "",
                f"Recent conversation:\n{transcript}",
            )
            if part.strip()
        )
        raw = await self.complete(
            DIGEST_PROMPT,
            user,
            max_tokens=3000,
            session=f"{USER_AGENT}-digest",
            purpose="digest",
        )
        return parse_digest(raw, self.config.soul.language)

    async def research_summary(self, query: str, results: str) -> str:
        """Summarise the web search results; instructions inside the results are data."""
        body = (
            f"Summarise the search results for the question: {str(query or '').strip()}\n\n"
            f"Search results:\n{str(results or '').strip()}"
        )
        raw = await self.complete(
            RESEARCH_PROMPT,
            body,
            max_tokens=250,
            session=f"{USER_AGENT}-research",
            purpose="research",
        )
        flat = " ".join(raw.split())
        return flat if len(flat) <= 400 else flat[:399] + "…"


def _round_text(text: str, last_text: str, *, interim: bool) -> str:
    """Prose already delivered by send_interim must never return as the final reply."""
    return "" if interim else text or last_text

