"""One door: addresses, receipts, the ledger, refusals, and approvals (ADR-0032 §4.4)."""


import asyncio
from dataclasses import replace

from support import FakeGateway, inbound_message, make_agent, role

from monoagens.engine.access.approvals import ApprovalStore
from monoagens.engine.config import ApprovalConfig
from monoagens.engine.inbound import Action, Address, Receipt, action_from_json, action_json


class RecordingGateway(FakeGateway):
    """A body that records every dispatched action and never raises."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.actions: list[Action] = []

    async def dispatch(self, action: Action) -> str:
        self.actions.append(action)
        return await super().dispatch(action)


def send(agent, address, text, *, subject="628111", origin=None, action_id="a-1"):
    action = Action(address=address, capability="send_text", payload=text, id=action_id)
    return asyncio.run(
        agent.dispatch(
            subject, address, action, origin=origin or address, reason="test"
        )
    )


def test_a_same_thread_send_is_sent_once_and_ledgered(tmp_path) -> None:
    gateway = RecordingGateway()
    env = make_agent(tmp_path, gateway=gateway)
    address = Address("fake", "chat")

    receipt = send(env.agent, address, "halo")

    assert receipt == Receipt("a-1", "sent", "msg-1")
    assert len(gateway.actions) == 1
    assert gateway.actions[0].approval_class == "consent"
    assert env.agent.outbox.status("a-1", 0) == "sent"
    assert env.agent.outbox.reference("a-1", 0) == "msg-1"


def test_the_same_id_is_dispatched_once(tmp_path) -> None:
    gateway = RecordingGateway()
    env = make_agent(tmp_path, gateway=gateway)
    address = Address("fake", "chat")

    first = send(env.agent, address, "halo")
    second = send(env.agent, address, "halo")

    assert first.state == second.state == "sent"
    assert second.reference == first.reference
    assert len(gateway.actions) == 1


def test_an_unknown_body_is_refused_and_ledger_free(tmp_path) -> None:
    env = make_agent(tmp_path)

    receipt = send(env.agent, Address("nobody", "chat"), "halo")

    assert receipt.state == "refused"
    assert "unknown body" in receipt.reason
    assert env.agent.outbox.status("a-1", 0) is None


def test_a_cross_body_send_by_a_non_member_is_refused(tmp_path) -> None:
    from support import DirectoryFakeGateway

    gateway = DirectoryFakeGateway(members={"someone-else"})
    env = make_agent(tmp_path, gateway=gateway)
    origin = Address("fake", "chat")

    receipt = send(env.agent, Address("fake", "elsewhere"), "rahasia", origin=origin)

    assert receipt.state == "refused"
    assert "not a participant" in receipt.reason
    assert env.agent.outbox.status("a-1", 0) is None


def test_a_body_without_a_directory_refuses_a_cross_body_send(tmp_path) -> None:
    gateway = RecordingGateway()
    env = make_agent(tmp_path, gateway=gateway)

    receipt = send(
        env.agent, Address("fake", "elsewhere"), "rahasia", origin=Address("fake", "chat")
    )

    assert receipt.state == "refused"
    assert "no directory" in receipt.reason
    assert gateway.actions == []


def test_a_cross_body_send_needs_a_reason(tmp_path) -> None:
    from support import DirectoryFakeGateway

    gateway = DirectoryFakeGateway(members={"per_budi"})
    env = make_agent(tmp_path, gateway=gateway)
    address = Address("fake", "other")
    action = Action(address=address, capability="send_text", payload="x", id="a-2")

    receipt = asyncio.run(
        env.agent.dispatch(
            "per_budi", address, action, origin=Address("fake", "chat"), reason=""
        )
    )

    assert receipt.state == "refused"
    assert "reason" in receipt.reason


def test_address_find_returns_one_address_or_the_candidates(tmp_path) -> None:
    from support import DirectoryFakeGateway

    gateway = DirectoryFakeGateway(
        targets={"group b": ("b@g.us", "Group B")},
        members={"628111"},
    )

    found, reason = Address.find("group b", gateway=gateway)
    assert reason == ""
    assert found == Address("fake", "b@g.us", "gateway")

    missing, reason = Address.find("nobody", gateway=gateway)
    assert missing is None
    assert "not in the archive" in reason

    ambiguous, reason = Address.find("group", gateway=gateway)
    assert ambiguous is None

    found, reason = Address.find("group b", gateway=FakeGateway())
    assert found is None
    assert "no directory" in reason


def test_an_action_round_trips_through_json() -> None:
    action = Action(
        address=Address("whatsapp", "111@g.us", "gateway"),
        capability="send_file",
        payload={"path": "/w/a.txt", "caption": "cap"},
        id="turn:1:0",
    )

    rebuilt = action_from_json(action_json(action))

    assert rebuilt == action


def test_strict_mode_creates_one_approval_and_sends_after_it(tmp_path) -> None:
    from monoagens.engine.guard import build_safety

    gateway = RecordingGateway()
    env = make_agent(tmp_path, gateway=gateway)
    env.agent.config = replace(env.agent.config, approval=ApprovalConfig(mode="strict"))
    env.agent.approvals.configure(env.agent.config.approval)
    env.agent.safety = build_safety(env.agent.config.approval)
    address = Address("fake", "chat")

    first = send(env.agent, address, "halo")

    assert first.state == "pending"
    row = env.agent.approvals.find_action("a-1")
    assert row is not None
    assert row.kind == "send"
    assert gateway.actions == []

    again = send(env.agent, address, "halo")
    assert again.state == "pending"
    assert len(env.agent.approvals.find_action("a-1").payload) == 1

    env.agent.approvals.mark(row.id, "approved", "628111")
    delivered = send(env.agent, address, "halo")

    assert delivered.state == "sent"
    assert len(gateway.actions) == 1


def test_a_send_row_records_its_receipt_state(tmp_path) -> None:
    gateway = RecordingGateway()
    env = make_agent(tmp_path, gateway=gateway)
    store = ApprovalStore(env.db, ApprovalConfig(mode="strict"))
    env.agent.approvals = store
    from monoagens.engine.guard import build_safety

    env.agent.safety = build_safety(store.config)
    address = Address("fake", "chat")

    settle = send(env.agent, address, "halo")
    row = store.find_action("a-1")
    assert settle.state == "pending"
    assert row.status == "pending"

    store.mark(row.id, "denied", "628111")
    assert send(env.agent, address, "halo").state == "refused"


def test_a_robot_body_with_only_dispatch_meets_the_core(tmp_path) -> None:
    from monoagens.engine.inbound import GatewayCore, missing_core

    class Robot:
        name = "robot"

        def tools(self):
            return []

        def commands(self):
            return []

        async def run(self, agent):
            return None

        async def stop(self):
            return None

        async def dispatch(self, action):
            return "moved"

        def prompt_section(self):
            return ""

        def self_handles(self):
            return set()

        def chat_name(self, thread):
            return thread

    robot = Robot()
    assert missing_core(robot) == []
    assert isinstance(robot, GatewayCore)


def test_owner_is_not_permission_for_a_cross_room_send(tmp_path) -> None:
    """An automation owned by a non-member is refused at the door, not at the transport."""
    gateway = RecordingGateway()
    env = make_agent(
        tmp_path,
        gateway=gateway,
        roles={"member": role("member", users=("628111",), tools=("*",), commands=("*",))},
    )
    env.history.intake(
        "other", inbound_message("halo", thread="other").payload, "999999", language="id"
    )

    receipt = send(
        env.agent,
        Address("fake", "other"),
        "halo",
        subject="999999",
        origin=Address("fake", "chat"),
    )

    assert receipt.state == "refused"
    assert gateway.actions == []
