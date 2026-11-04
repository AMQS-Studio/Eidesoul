"""Identity contract: `per_…` ids, gateway links, and the `/link` codes."""


import pytest
from support import database

from monoagens.engine.access.identity import (
    CODE_ATTEMPTS,
    CODE_TTL,
    ID_PATTERN,
    IdentityStore,
    is_person_id,
    mask_address,
)
from monoagens.engine.schema import people

WHATSAPP = "6285111242960"


def store(tmp_path) -> IdentityStore:
    return IdentityStore(database())


def test_resolve_is_stable_and_per_gateway(tmp_path) -> None:
    identity = store(tmp_path)

    first = identity.resolve("whatsapp", WHATSAPP)
    second = identity.resolve("whatsapp", f" {WHATSAPP} ")
    other = identity.resolve("cli", "local")

    assert ID_PATTERN.fullmatch(first)
    assert is_person_id(first)
    assert first == second
    assert first != other
    assert identity.person(first)["name"] == ""
    assert [item["gateway"] for item in identity.links(first)] == ["whatsapp"]


def test_resolve_rejects_an_empty_address(tmp_path) -> None:
    with pytest.raises(ValueError, match="address is empty"):
        store(tmp_path).resolve("whatsapp", "  ")


def test_a_code_only_works_on_the_gateway_that_requested_it(tmp_path) -> None:
    identity = store(tmp_path)
    person = identity.resolve("whatsapp", WHATSAPP)
    code = identity.start_code(person, "cli")

    assert len(code) == 6
    assert identity.code_target(code) == person
    assert identity.finish_code(
        code, person_id=person, gateway="whatsapp", address=WHATSAPP
    ) == ("wrong_gateway", "")


def test_five_wrong_attempts_exhaust_the_code(tmp_path) -> None:
    identity = store(tmp_path)
    person = identity.resolve("whatsapp", WHATSAPP)
    code = identity.start_code(person, "cli")

    for _ in range(CODE_ATTEMPTS):
        status, _ = identity.finish_code(
            code, person_id=person, gateway="whatsapp", address=WHATSAPP
        )
        assert status == "wrong_gateway"

    assert identity.finish_code(code, person_id=person, gateway="cli", address="local") == (
        "too_many",
        "",
    )


def test_expired_code_is_refused(tmp_path) -> None:
    identity = store(tmp_path)
    person = identity.resolve("whatsapp", WHATSAPP)
    code = identity.start_code(person, "cli", now=1_000.0)

    status, merged = identity.finish_code(
        code,
        person_id=person,
        gateway="cli",
        address="local",
        now=1_000.0 + CODE_TTL + 1,
    )

    assert (status, merged) == ("expired", "")


def test_code_is_single_use(tmp_path) -> None:
    identity = store(tmp_path)
    person = identity.resolve("whatsapp", WHATSAPP)
    code = identity.start_code(person, "cli")
    assert identity.finish_code(code, person_id=person, gateway="cli", address="local")[0] == "ok"

    assert identity.finish_code(code, person_id=person, gateway="cli", address="local") == (
        "used",
        "",
    )


def test_an_established_identity_cannot_take_another_name_code(tmp_path) -> None:
    identity = store(tmp_path)
    target = identity.resolve("whatsapp", WHATSAPP)
    established = identity.resolve("cli", "local")
    identity.link(established, "telegram", "555")
    code = identity.start_code(target, "telegram")

    assert identity.finish_code(
        code, person_id=established, gateway="telegram", address="555"
    ) == ("wrong_person", "")


def test_an_address_of_a_two_link_person_is_taken(tmp_path) -> None:
    identity = store(tmp_path)
    target = identity.resolve("whatsapp", WHATSAPP)
    owner = identity.resolve("cli", "local")
    identity.link(owner, "telegram", "555")
    code = identity.start_code(target, "cli")

    assert identity.finish_code(code, person_id=target, gateway="cli", address="local") == (
        "taken",
        "",
    )


def test_a_provisional_identity_is_folded_in(tmp_path) -> None:
    identity = store(tmp_path)
    target = identity.resolve("whatsapp", WHATSAPP)
    provisional = identity.resolve("cli", "local")
    code = identity.start_code(target, "cli")

    status, merged = identity.finish_code(
        code, person_id=provisional, gateway="cli", address="local"
    )

    assert status == "ok"
    assert merged == provisional
    assert identity.person(provisional) is None
    assert [item["gateway"] for item in identity.links(target)] == ["cli", "whatsapp"]


def test_unlink_keeps_the_person(tmp_path) -> None:
    identity = store(tmp_path)
    person = identity.resolve("whatsapp", WHATSAPP)
    identity.link(person, "cli", "local")

    assert identity.unlink(person, "cli") is True
    assert identity.unlink(person, "cli") is False
    assert identity.person(person) is not None
    assert [item["gateway"] for item in identity.links(person)] == ["whatsapp"]


def test_link_statuses_and_legacy_count(tmp_path) -> None:
    identity = store(tmp_path)
    person = identity.resolve("whatsapp", WHATSAPP)
    other = identity.resolve("cli", "other")

    assert identity.link(person, "cli", "local") == "ok"
    assert identity.link(other, "cli", "local") == "taken"
    assert identity.link("", "cli", "x") == "missing"
    assert identity.legacy_count() == 0

    with identity.db.connect() as conn:
        identity.db.upsert(conn, people, [{"person_id": WHATSAPP}], ["person_id"])

    assert identity.legacy_count() == 1


def test_mask_address_hides_the_middle() -> None:
    assert mask_address("local") == "local"
    assert mask_address(WHATSAPP) == "628••••2960"
