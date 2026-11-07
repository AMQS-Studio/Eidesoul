"""The tool index is per body: names are scoped, the prompt lists one body (ADR-0004 §4.6)."""


from support import FakeGateway, make_agent

from monoagens.engine.registry import Handler, RegistryError


def handler(name: str, description: str = "test") -> Handler:
    async def fn(args, ctx):
        return name

    return Handler(
        name=name,
        description=description,
        fn=fn,
        family="read",
        parameters={"type": "object", "properties": {}},
    )


def test_two_bodies_may_declare_the_same_name(tmp_path) -> None:
    first = FakeGateway(name="a", tools=[handler("send", "from a")])
    second = FakeGateway(name="b", tools=[handler("send", "from b")])
    env = make_agent(tmp_path, gateways=[first, second])
    index = env.agent.tool_index

    assert index.specs("a")["send"].description == "from a"
    assert index.specs("b")["send"].description == "from b"
    assert index.other_bodies("a") == ("b",)
    assert index.other_bodies("b") == ("a",)


def test_a_qualified_name_reaches_another_body(tmp_path) -> None:
    first = FakeGateway(name="a", tools=[handler("speak")])
    second = FakeGateway(name="b", tools=[handler("move")])
    env = make_agent(tmp_path, gateways=[first, second])
    index = env.agent.tool_index

    found = index.resolve("b__move", "a")
    assert found is not None
    assert found[0] == "move"
    assert index.resolve("b__speak", "a") is None
    assert index.resolve("nowhere__move", "a") is None


def test_a_bare_name_resolves_this_body_then_core(tmp_path) -> None:
    gateway = FakeGateway(name="a", tools=[handler("read", "body read")])
    env = make_agent(tmp_path, gateway=gateway)
    index = env.agent.tool_index

    assert index.get("read", "a").description == "body read"
    assert index.get("read", "b").description != "body read"
    assert index.get("grep", "a") is not None
    assert index.get("nope", "a") is None


def test_a_body_tool_is_not_in_the_core_registry(tmp_path) -> None:
    gateway = FakeGateway(name="a", tools=[handler("probe_tool")])
    env = make_agent(tmp_path, gateway=gateway)

    assert env.agent.tools.get("probe_tool") is None
    assert "probe_tool" in env.agent.tool_index.names("a")
    assert "probe_tool" not in env.agent.tool_index.names("b")


def test_a_duplicate_inside_one_body_is_a_boot_failure(tmp_path) -> None:
    import pytest

    gateway = FakeGateway(name="a", tools=[handler("clash"), handler("clash")])
    with pytest.raises(RegistryError):
        make_agent(tmp_path, gateway=gateway)


def test_the_prompt_names_the_other_bodies_once(tmp_path) -> None:
    from monoagens.engine.turn.prompts import PromptContext, system_prompt

    gateway = FakeGateway(name="a", tools=[handler("speak")])
    env = make_agent(tmp_path, gateway=gateway)
    roles = (env.config.roles.get("member"),)
    context = PromptContext(
        config=env.config,
        roles=roles,
        tools=env.agent.tool_index.registry("a"),
        commands=env.agent.commands,
        bodies=env.agent.tool_index.other_bodies("a"),
    )
    prompt = system_prompt(env.config, roles, context=context)

    assert "other bodies:" not in prompt
    index = env.agent.tool_index
    assert index.other_bodies("a") == ()
