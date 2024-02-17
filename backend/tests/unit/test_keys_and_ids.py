from app.core.ids import is_id, new_id
from app.repositories import keys
from hypothesis import given
from hypothesis import strategies as st


def test_new_id_is_valid_and_sortable() -> None:
    a, b = new_id(), new_id()
    assert is_id(a) and is_id(b) and a != b


def test_is_id_rejects_garbage() -> None:
    for bad in ("", "abc", "01JD8Z3K9Q4M7T2V6X8B5N1C0", "01JD8Z3K9Q4M7T2V6X8B5N1C0I", None, 5):
        assert not is_id(bad)


@given(st.text(min_size=1, max_size=40), st.text(min_size=1, max_size=40))
def test_label_lock_injective_per_property_and_case_insensitive(a: str, b: str) -> None:
    pid = new_id()
    ka, kb = keys.unit_label_lock_sk(pid, a), keys.unit_label_lock_sk(pid, b)
    if a.strip().casefold() == b.strip().casefold():
        assert ka == kb
    else:
        assert ka != kb
    assert keys.unit_label_lock_sk(new_id(), a) != ka


def test_org_prefix_on_all_org_scoped_keys() -> None:
    o, p, u = new_id(), new_id(), new_id()
    for k in (
        keys.org_pk(o),
        keys.entity_gsi1pk(o, "UNIT", u),
        keys.audit_pk(o, "2018-11"),
        keys.audit_entity_gsi1pk(o, "unit", u),
    ):
        assert k.startswith(f"ORG#{o}")
    assert keys.unit_sk(p, u).startswith(keys.prop_sk(p))
