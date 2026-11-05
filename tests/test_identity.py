"""Identity contract: `per_…` ids, gateway links, and the `/link` codes."""


import threading

import pytest
from sqlalchemy import select
from support import database

from monoagens.engine.access.identity import (
    CODE_ATTEMPTS,
    CODE_TTL,
    ID_PATTERN,
    IdentityStore,
    is_person_id,
    mask_address,
)
from monoagens.engine.access.roles import Role, RoleRegistry
from monoagens.engine.schema import people, person_links

WHATSAPP = "6285111242960"


def store(tmp_path) -> IdentityStore:
    return IdentityStore(database())


def test_resolve_is_stable_and_per_gateway(tmp_path) -> None:
    identity = store(tmp_path)

    first = identity.resolve("whatsapp", WHATSAPP)
    second = identity.resolve("whatsapp", f" {WHATSAPP} ")
    other = identity.resolve("telegram", "local")

    assert ID_PATTERN.fullmatch(first)
    assert is_person_id(first)
    assert first == second
    assert first != other
    assert identity.person(first)["name"] == ""
    assert [item["gateway"] for item in identity.links(first)] == ["whatsapp"]


def test_resolve_rejects_an_empty_address(tmp_path) -> None:
    with pytest.raises(ValueError, match="address is empty"):
        store(tmp_path).resolve("whatsapp", "  ")


def test_resolve_keeps_an_existing_person_id(tmp_path) -> None:
    """ADR-0001 §4.1: a resume hands back `per_…`; it is kept, never linked to itself."""
    identity = store(tmp_path)
    person = identity.resolve("whatsapp", WHATSAPP)
    assert person.startswith("per_")

    assert identity.resolve("whatsapp", person) == person
    assert identity.resolve("telegram", person) == person

    with identity.db.connect() as conn:
        people_rows = conn.execute(select(people.c.person_id)).scalars().all()
        links = conn.execute(
            select(person_links.c.gateway, person_links.c.address, person_links.c.person_id)
        ).all()
    assert people_rows == [person]
    assert [(row.gateway, row.address, row.person_id) for row in links] == [
        ("whatsapp", WHATSAPP, person)
    ]


def test_a_code_only_works_on_the_gateway_that_requested_it(tmp_path) -> None:
    identity = store(tmp_path)
    person = identity.resolve("whatsapp", WHATSAPP)
    code = identity.start_code(person, "telegram")

    assert len(code) == 6
    assert identity.code_target(code) == person
    assert identity.finish_code(
        code, person_id=person, gateway="whatsapp", address=WHATSAPP
    ) == ("wrong_gateway", "")


def test_five_wrong_attempts_exhaust_the_code(tmp_path) -> None:
    identity = store(tmp_path)
    person = identity.resolve("whatsapp", WHATSAPP)
    code = identity.start_code(person, "telegram")

    for _ in range(CODE_ATTEMPTS):
        status, _ = identity.finish_code(
            code, person_id=person, gateway="whatsapp", address=WHATSAPP
        )
        assert status == "wrong_gateway"

    assert identity.finish_code(code, person_id=person, gateway="telegram", address="local") == (
        "too_many",
        "",
    )


def test_expired_code_is_refused(tmp_path) -> None:
    identity = store(tmp_path)
    person = identity.resolve("whatsapp", WHATSAPP)
    code = identity.start_code(person, "telegram", now=1_000.0)

    status, merged = identity.finish_code(
        code,
        person_id=person,
        gateway="telegram",
        address="local",
        now=1_000.0 + CODE_TTL + 1,
    )

    assert (status, merged) == ("expired", "")


def test_code_is_single_use(tmp_path) -> None:
    identity = store(tmp_path)
    person = identity.resolve("whatsapp", WHATSAPP)
    code = identity.start_code(person, "telegram")
    assert identity.finish_code(
        code, person_id=person, gateway="telegram", address="local"
    )[0] == "ok"

    assert identity.finish_code(code, person_id=person, gateway="telegram", address="local") == (
        "used",
        "",
    )


def test_an_established_identity_cannot_take_another_name_code(tmp_path) -> None:
    identity = store(tmp_path)
    target = identity.resolve("whatsapp", WHATSAPP)
    established = identity.resolve("telegram", "local")
    identity.link(established, "telegram", "555")
    code = identity.start_code(target, "telegram")

    assert identity.finish_code(
        code, person_id=established, gateway="telegram", address="555"
    ) == ("wrong_person", "")


def test_an_address_of_a_two_link_person_is_taken(tmp_path) -> None:
    identity = store(tmp_path)
    target = identity.resolve("whatsapp", WHATSAPP)
    owner = identity.resolve("telegram", "local")
    identity.link(owner, "telegram", "555")
    code = identity.start_code(target, "telegram")

    assert identity.finish_code(code, person_id=target, gateway="telegram", address="local") == (
        "taken",
        "",
    )


def test_a_provisional_identity_is_folded_in(tmp_path) -> None:
    identity = store(tmp_path)
    target = identity.resolve("whatsapp", WHATSAPP)
    provisional = identity.resolve("telegram", "local")
    code = identity.start_code(target, "telegram")

    status, merged = identity.finish_code(
        code, person_id=provisional, gateway="telegram", address="local"
    )

    assert status == "ok"
    assert merged == provisional
    assert identity.person(provisional) is None
    assert [item["gateway"] for item in identity.links(target)] == ["telegram", "whatsapp"]


def test_unlink_keeps_the_person(tmp_path) -> None:
    identity = store(tmp_path)
    person = identity.resolve("whatsapp", WHATSAPP)
    identity.link(person, "telegram", "local")

    assert identity.unlink(person, "telegram") is True
    assert identity.unlink(person, "telegram") is False
    assert identity.person(person) is not None
    assert [item["gateway"] for item in identity.links(person)] == ["whatsapp"]


def test_link_statuses_and_legacy_count(tmp_path) -> None:
    identity = store(tmp_path)
    person = identity.resolve("whatsapp", WHATSAPP)
    other = identity.resolve("telegram", "other")

    assert identity.link(person, "telegram", "local") == "ok"
    assert identity.link(other, "telegram", "local") == "taken"
    assert identity.link("", "telegram", "x") == "missing"
    assert identity.legacy_count() == 0

    with identity.db.connect() as conn:
        identity.db.upsert(conn, people, [{"person_id": WHATSAPP}], ["person_id"])

    assert identity.legacy_count() == 1


def test_mask_address_hides_the_middle() -> None:
    assert mask_address("local") == "local"
    assert mask_address(WHATSAPP) == "628••••2960"


def test_a_role_conflict_refuses_the_merge(tmp_path) -> None:
    db = database()
    identity = IdentityStore(db)
    target = identity.resolve("whatsapp", WHATSAPP)
    provisional = identity.resolve("telegram", "local")
    registry = RoleRegistry(
        by_name={
            "administrator": Role(
                "administrator", users=(target,), protected=True, tools=("*",), commands=("*",)
            ),
            "member": Role("member", users=(provisional,)),
        },
        default="member",
    )
    guarded = IdentityStore(db, roles=registry)
    code = guarded.start_code(target, "telegram")

    status, merged = guarded.finish_code(
        code, person_id=provisional, gateway="telegram", address="local"
    )

    assert (status, merged) == ("role_conflict", "")
    assert guarded.person(provisional) is not None
    assert [item["gateway"] for item in guarded.links(provisional)] == ["telegram"]
    assert [item["gateway"] for item in guarded.links(target)] == ["whatsapp"]


def test_concurrent_code_redemption_locks_the_code(tmp_path) -> None:
    db = database()
    identity = IdentityStore(db)
    target = identity.resolve("whatsapp", WHATSAPP)
    provisional = identity.resolve("telegram", "local")
    code = identity.start_code(target, "telegram")
    barrier = threading.Barrier(2)
    results: list[tuple[str, str]] = []

    def redeem() -> None:
        store = IdentityStore(db)
        barrier.wait(timeout=5)
        results.append(
            store.finish_code(
                code, person_id=provisional, gateway="telegram", address="local"
            )
        )

    threads = [threading.Thread(target=redeem) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(results) == [("ok", provisional), ("used", "")]

