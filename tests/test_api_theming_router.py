"""``/theming`` endpoints (WTK-120): the WTK-114 contracts live, REQ-044/045/046.

Server-side enforcement proven over the wire: fixed-slot structure and the
caller's-namespace duplicate check on template writes, the fixed step set on
the type-scale retune, standard operators + status-slot effects (plus the
REQ-019 condition-field catalog) on rule writes, system-template read-only,
the delete cascade, append-then-reorder ordering, and the DB-S4 409 with the
current record — all inside the one envelope.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from mentorapp.api.deps import get_current_user_id, get_session
from mentorapp.api.routers.theming import (
    CODE_UNKNOWN_CONDITION_FIELD,
    get_condition_field_catalog,
)
from mentorapp.api.theming_surface import (
    CODE_CONDITION_VALUE_NOT_ALLOWED,
    CODE_DUPLICATE_TEMPLATE_NAME,
    CODE_INVALID_COLOR,
    CODE_INVALID_RULE_ORDER,
    CODE_MISSING_STEP,
    CODE_SYSTEM_TEMPLATE_READ_ONLY,
    CODE_UNKNOWN_OPERATOR,
    CODE_UNKNOWN_SLOT,
    CODE_UNKNOWN_STEP,
)
from mentorapp.main import create_app
from mentorapp.storage import (
    SHARED_TYPE_SCALE_NAME,
    TYPE_SCALE_DEFAULT_SIZES,
    AppUser,
    ColorTemplate,
    ConditionalFormattingRule,
    TypeScale,
    UserPreference,
)
from mentorapp.ui.template_flow import TEMPLATE_CHOICE_PREFERENCE_KEY
from mentorapp.ui.theming import STANDARD_TEMPLATE

# The fields the stub grid-entity catalog "serves" (REQ-019).
KNOWN_FIELDS = {"engagementStatus", "mentorName"}


class _StubCatalog:
    def is_condition_field(self, field_name: str) -> bool:
        return field_name in KNOWN_FIELDS


@pytest.fixture()
def app_client(session: Session) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_condition_field_catalog] = _StubCatalog
    return TestClient(app)


def _user(session: Session, username: str) -> uuid.UUID:
    user = AppUser(crm_user_id=f"crm-{username}", username=username)
    session.add(user)
    session.flush()
    return user.user_id


@pytest.fixture()
def user_id(session: Session) -> uuid.UUID:
    return _user(session, "mentor.one")


@pytest.fixture()
def scale(session: Session) -> TypeScale:
    row = TypeScale(
        type_scale_name=SHARED_TYPE_SCALE_NAME,
        scale_steps=dict(TYPE_SCALE_DEFAULT_SIZES),
    )
    session.add(row)
    session.flush()
    return row


def _colors() -> dict[str, str]:
    return {
        "appBackground": "#f4f6f8",
        "panelBackground": "#ffffff",
        "headerBackground": "#1d3557",
        "headerText": "#ffffff",
        "accent": "#2a6f97",
        "rowBackground": "#ffffff",
        "rowAlternateBackground": "#f0f4f8",
        "rowText": "#1a1a1a",
        "selectedRowBackground": "#d6e6f2",
        "selectedRowText": "#102a43",
        "groupHeaderBackground": "#e3e9ef",
        "groupHeaderText": "#243b53",
        "statusPositive": "#2d6a4f",
        "statusWarning": "#b45309",
        "statusNegative": "#b02a37",
    }


def _fonts() -> dict[str, dict[str, Any]]:
    return {
        "uiFont": {"stepKey": "md", "fontFamily": "Inter", "fontWeight": 400},
        "dataFont": {"stepKey": "sm", "fontFamily": "Inter", "fontWeight": 600},
    }


def _template(
    session: Session,
    scale: TypeScale,
    name: str,
    *,
    owner: uuid.UUID | None = None,
    launch_key: str = "standard",
) -> ColorTemplate:
    template = ColorTemplate(
        color_template_name=name,
        template_type="system" if owner is None else "user",
        user_id=owner,
        type_scale_id=scale.type_scale_id,
        color_slots=_colors(),
        font_slots=_fonts(),
        launch_set_key=launch_key if owner is None else None,
    )
    session.add(template)
    session.flush()
    return template


def _rule(
    session: Session, template: ColorTemplate, order: int, field: str = "engagementStatus"
) -> ConditionalFormattingRule:
    rule = ConditionalFormattingRule(
        color_template_id=template.color_template_id,
        condition_field=field,
        condition_operator="equals",
        condition_value="overdue",
        effect="rowBackground",
        effect_slot="statusNegative",
        evaluation_order=order,
    )
    session.add(rule)
    session.flush()
    return rule


def _headers(user_id: uuid.UUID) -> dict[str, str]:
    return {"X-User-ID": str(user_id)}


def _create_body(name: str = "My theme") -> dict[str, Any]:
    return {
        "colorTemplateName": name,
        "colorSlots": _colors(),
        "fontSlots": _fonts(),
        "typeStepChoice": "md",
    }


def _codes(body: dict[str, Any]) -> set[tuple[str | None, str]]:
    return {(entry["fieldName"], entry["code"]) for entry in body["errors"]}


# --- Templates ----------------------------------------------------------------------


def test_list_serves_system_plus_own_never_anothers(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    _template(session, scale, "Standard")
    _template(session, scale, "Mine", owner=user_id)
    other = _user(session, "mentor.two")
    _template(session, scale, "Theirs", owner=other)
    response = app_client.get("/theming/templates", headers=_headers(user_id))
    assert response.status_code == 200
    names = [record["colorTemplateName"] for record in response.json()["data"]]
    assert names == ["Standard", "Mine"]
    assert all("rowVersion" in record for record in response.json()["data"])


def test_create_makes_a_user_template_with_server_assigned_type(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    response = app_client.post(
        "/theming/templates", headers=_headers(user_id), json=_create_body()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["templateType"] == "user"
    assert body["data"]["launchSetKey"] is None
    assert body["data"]["userID"] == str(user_id)
    assert body["data"]["typeScaleID"] == str(scale.type_scale_id)
    # Readable colors: the guardrail reviewed the save and found nothing.
    assert body["meta"]["contrastWarnings"] == []
    row = session.scalars(select(ColorTemplate).where(ColorTemplate.user_id == user_id)).one()
    assert row.created_by == user_id


def test_create_refuses_client_supplied_template_type(
    app_client: TestClient, user_id: uuid.UUID, scale: TypeScale
) -> None:
    response = app_client.post(
        "/theming/templates",
        headers=_headers(user_id),
        json={**_create_body(), "templateType": "system"},
    )
    assert response.status_code == 422


def test_create_accumulates_structure_and_duplicate_name_failures(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    _template(session, scale, "My theme", owner=user_id)
    body = _create_body("My theme")
    body["colorSlots"]["accent"] = "tomato"
    body["colorSlots"]["extraSlot"] = "#000000"
    response = app_client.post("/theming/templates", headers=_headers(user_id), json=body)
    assert response.status_code == 422
    assert _codes(response.json()) >= {
        ("colorSlots.accent", CODE_INVALID_COLOR),
        ("colorSlots.extraSlot", CODE_UNKNOWN_SLOT),
        ("colorTemplateName", CODE_DUPLICATE_TEMPLATE_NAME),
    }


def test_get_answers_rules_in_order_and_hides_foreign_templates(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    mine = _template(session, scale, "Mine", owner=user_id)
    _rule(session, mine, 1)
    _rule(session, mine, 2, field="mentorName")
    response = app_client.get(
        f"/theming/templates/{mine.color_template_id}", headers=_headers(user_id)
    )
    assert response.status_code == 200
    orders = [r["evaluationOrder"] for r in response.json()["data"]["rules"]]
    assert orders == [1, 2]
    theirs = _template(session, scale, "Theirs", owner=_user(session, "mentor.two"))
    hidden = app_client.get(
        f"/theming/templates/{theirs.color_template_id}", headers=_headers(user_id)
    )
    assert hidden.status_code == 404


def test_patch_edits_fields_and_bumps_row_version(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    mine = _template(session, scale, "Mine", owner=user_id)
    colors = _colors()
    colors["accent"] = "#155e75"
    response = app_client.patch(
        f"/theming/templates/{mine.color_template_id}",
        headers=_headers(user_id),
        json={"rowVersion": 1, "colorTemplateName": "Renamed", "colorSlots": colors},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["colorTemplateName"] == "Renamed"
    assert body["data"]["colorSlots"]["accent"] == "#155e75"
    assert body["data"]["rowVersion"] == 2
    assert "contrastWarnings" in body["meta"]


def test_patch_stale_row_version_is_409_with_current_record(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    mine = _template(session, scale, "Mine", owner=user_id)
    response = app_client.patch(
        f"/theming/templates/{mine.color_template_id}",
        headers=_headers(user_id),
        json={"rowVersion": 7, "colorTemplateName": "Renamed"},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["errors"][0]["code"] == "staleRowVersion"
    assert body["data"]["colorTemplateName"] == "Mine"
    assert body["data"]["rowVersion"] == 1


def test_system_templates_refuse_every_write_verb(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    system = _template(session, scale, "Standard")
    template_path = f"/theming/templates/{system.color_template_id}"
    headers = _headers(user_id)
    attempts = [
        app_client.patch(template_path, headers=headers, json={"rowVersion": 1}),
        app_client.delete(template_path, headers=headers),
        app_client.post(
            f"{template_path}/rules",
            headers=headers,
            json={
                "conditionField": "engagementStatus",
                "conditionOperator": "isEmpty",
                "effect": "accent",
                "effectSlot": "statusWarning",
            },
        ),
        app_client.put(f"{template_path}/rules/order", headers=headers, json={"ruleOrder": []}),
    ]
    for response in attempts:
        assert response.status_code == 422
        assert ("colorTemplateID", CODE_SYSTEM_TEMPLATE_READ_ONLY) in _codes(response.json())


def test_delete_cascades_to_the_templates_rules(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    mine = _template(session, scale, "Mine", owner=user_id)
    rule = _rule(session, mine, 1)
    response = app_client.delete(
        f"/theming/templates/{mine.color_template_id}", headers=_headers(user_id)
    )
    assert response.status_code == 200
    assert response.json()["data"]["rulesDeleted"] == 1
    session.expire_all()
    assert mine.deleted_at is not None and mine.deleted_by == user_id
    assert rule.deleted_at is not None
    gone = app_client.get(
        f"/theming/templates/{mine.color_template_id}", headers=_headers(user_id)
    )
    assert gone.status_code == 404


# --- The shared type scale ----------------------------------------------------------


def test_type_scale_read_and_retune(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    read = app_client.get("/theming/type-scale", headers=_headers(user_id))
    assert read.status_code == 200
    assert read.json()["data"]["scaleSteps"] == TYPE_SCALE_DEFAULT_SIZES
    retuned = {"xs": 12, "sm": 13, "md": 15, "lg": 18, "xl": 22}
    write = app_client.patch(
        "/theming/type-scale",
        headers=_headers(user_id),
        json={"rowVersion": 1, "scaleSteps": retuned},
    )
    assert write.status_code == 200
    assert write.json()["data"]["rowVersion"] == 2
    session.expire_all()
    assert scale.scale_steps == retuned


def test_type_scale_steps_are_never_minted_or_dropped(
    app_client: TestClient, user_id: uuid.UUID, scale: TypeScale
) -> None:
    steps = {"sm": 12, "md": 14, "lg": 16, "xl": 20, "xxl": 28}
    response = app_client.patch(
        "/theming/type-scale",
        headers=_headers(user_id),
        json={"rowVersion": 1, "scaleSteps": steps},
    )
    assert response.status_code == 422
    assert _codes(response.json()) == {
        ("scaleSteps.xs", CODE_MISSING_STEP),
        ("scaleSteps.xxl", CODE_UNKNOWN_STEP),
    }


def test_type_scale_stale_version_is_409(
    app_client: TestClient, user_id: uuid.UUID, scale: TypeScale
) -> None:
    response = app_client.patch(
        "/theming/type-scale",
        headers=_headers(user_id),
        json={"rowVersion": 9, "scaleSteps": dict(TYPE_SCALE_DEFAULT_SIZES)},
    )
    assert response.status_code == 409
    assert response.json()["data"]["rowVersion"] == 1


# --- Conditional formatting rules ---------------------------------------------------


def test_rule_create_appends_at_the_end(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    mine = _template(session, scale, "Mine", owner=user_id)
    _rule(session, mine, 1)
    response = app_client.post(
        f"/theming/templates/{mine.color_template_id}/rules",
        headers=_headers(user_id),
        json={
            "conditionField": "mentorName",
            "conditionOperator": "isNotEmpty",
            "effect": "rowText",
            "effectSlot": "statusPositive",
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["evaluationOrder"] == 2


def test_rule_create_refuses_evaluation_order_in_the_body(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    mine = _template(session, scale, "Mine", owner=user_id)
    response = app_client.post(
        f"/theming/templates/{mine.color_template_id}/rules",
        headers=_headers(user_id),
        json={
            "conditionField": "mentorName",
            "conditionOperator": "isEmpty",
            "effect": "accent",
            "effectSlot": "statusWarning",
            "evaluationOrder": 1,
        },
    )
    assert response.status_code == 422


def test_rule_create_enforces_vocabulary_and_field_catalog(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    mine = _template(session, scale, "Mine", owner=user_id)
    response = app_client.post(
        f"/theming/templates/{mine.color_template_id}/rules",
        headers=_headers(user_id),
        json={
            "conditionField": "noSuchField",
            "conditionOperator": "matches",
            "effect": "rowBackground",
            "effectSlot": "#ff0000",
        },
    )
    assert response.status_code == 422
    assert _codes(response.json()) >= {
        ("conditionField", CODE_UNKNOWN_CONDITION_FIELD),
        ("conditionOperator", CODE_UNKNOWN_OPERATOR),
        ("effectSlot", CODE_UNKNOWN_SLOT),
    }


def test_rule_patch_revalidates_the_merged_document(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    mine = _template(session, scale, "Mine", owner=user_id)
    rule = _rule(session, mine, 1)
    path = f"/theming/rules/{rule.conditional_formatting_rule_id}"
    # equals -> isEmpty while the stored comparand survives the merge: refused.
    refused = app_client.patch(
        path, headers=_headers(user_id), json={"rowVersion": 1, "conditionOperator": "isEmpty"}
    )
    assert refused.status_code == 422
    assert ("conditionValue", CODE_CONDITION_VALUE_NOT_ALLOWED) in _codes(refused.json())
    accepted = app_client.patch(
        path,
        headers=_headers(user_id),
        json={"rowVersion": 1, "conditionOperator": "isEmpty", "conditionValue": None},
    )
    assert accepted.status_code == 200
    assert accepted.json()["data"]["conditionValue"] is None
    assert accepted.json()["data"]["rowVersion"] == 2


def test_rule_delete_keeps_survivor_order_gaps(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    mine = _template(session, scale, "Mine", owner=user_id)
    first = _rule(session, mine, 1)
    _rule(session, mine, 2)
    _rule(session, mine, 3)
    response = app_client.delete(
        f"/theming/rules/{first.conditional_formatting_rule_id}", headers=_headers(user_id)
    )
    assert response.status_code == 200
    listed = app_client.get(
        f"/theming/templates/{mine.color_template_id}/rules", headers=_headers(user_id)
    )
    assert [r["evaluationOrder"] for r in listed.json()["data"]] == [2, 3]


def test_reorder_takes_a_full_permutation_and_reassigns(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    mine = _template(session, scale, "Mine", owner=user_id)
    rules = [_rule(session, mine, order) for order in (1, 2, 3)]
    ids = [str(r.conditional_formatting_rule_id) for r in rules]
    response = app_client.put(
        f"/theming/templates/{mine.color_template_id}/rules/order",
        headers=_headers(user_id),
        json={"ruleOrder": [ids[2], ids[0], ids[1]]},
    )
    assert response.status_code == 200
    served = response.json()["data"]
    assert [r["conditionalFormattingRuleID"] for r in served] == [ids[2], ids[0], ids[1]]
    assert [r["evaluationOrder"] for r in served] == [1, 2, 3]


def test_reorder_refuses_a_partial_list(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    mine = _template(session, scale, "Mine", owner=user_id)
    rules = [_rule(session, mine, order) for order in (1, 2)]
    response = app_client.put(
        f"/theming/templates/{mine.color_template_id}/rules/order",
        headers=_headers(user_id),
        json={"ruleOrder": [str(rules[0].conditional_formatting_rule_id)]},
    )
    assert response.status_code == 422
    assert ("ruleOrder", CODE_INVALID_RULE_ORDER) in _codes(response.json())


def test_unwired_condition_field_catalog_fails_loudly(
    session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    client = TestClient(app, raise_server_exceptions=False)
    mine = _template(session, scale, "Mine", owner=user_id)
    response = client.post(
        f"/theming/templates/{mine.color_template_id}/rules",
        headers=_headers(user_id),
        json={
            "conditionField": "engagementStatus",
            "conditionOperator": "isEmpty",
            "effect": "accent",
            "effectSlot": "statusWarning",
        },
    )
    assert response.status_code == 500
    assert response.json()["errors"][0]["code"] == "internalError"


# --- The effective theme (WTK-230, REQ-044 layers one and two) ------------------------


def _choose(session: Session, user_id: uuid.UUID, template_key: str) -> None:
    # The WTK-113 flow's as_preference_value document under the canonical key.
    session.add(
        UserPreference(
            user_id=user_id,
            preference_key=TEMPLATE_CHOICE_PREFERENCE_KEY,
            preference_value={"templateKey": template_key},
        )
    )
    session.flush()


def _effective(app_client: TestClient, user_id: uuid.UUID) -> dict[str, Any]:
    response = app_client.get("/theming/effective", headers=_headers(user_id))
    assert response.status_code == 200
    data: dict[str, Any] = response.json()["data"]
    return data


def test_effective_serves_the_builtin_standard_when_nothing_is_seeded(
    app_client: TestClient, user_id: uuid.UUID, scale: TypeScale
) -> None:
    # No system template rows exist: layer one is the shipped in-code
    # Standard document, rendered in the persisted slot shapes.
    data = _effective(app_client, user_id)
    assert data["colorSlots"] == STANDARD_TEMPLATE["colors"]
    assert data["fontSlots"] == {
        "uiFont": {"stepKey": "md", "fontFamily": "Inter", "fontWeight": 400},
        "dataFont": {"stepKey": "md", "fontFamily": "Inter", "fontWeight": 400},
    }
    # typeScale travels exactly as GET /theming/type-scale serves it.
    scale_read = app_client.get("/theming/type-scale", headers=_headers(user_id))
    assert data["typeScale"] == scale_read.json()["data"]


def test_effective_serves_the_seeded_org_default_template(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    org = _template(session, scale, "Org Standard")
    org.color_slots = {**_colors(), "accent": "#0a3d62"}
    session.flush()
    data = _effective(app_client, user_id)
    assert data["colorSlots"]["accent"] == "#0a3d62"
    assert data["fontSlots"] == _fonts()


def test_effective_replaces_the_default_wholesale_with_the_users_choice(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    _template(session, scale, "Org Standard")
    mine = _template(session, scale, "Mine", owner=user_id)
    mine.color_slots = {**_colors(), "accent": "#7b2d8b"}
    session.flush()
    _choose(session, user_id, str(mine.color_template_id))
    data = _effective(app_client, user_id)
    assert data["colorSlots"]["accent"] == "#7b2d8b"


def test_effective_resolves_a_launch_key_choice(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    _template(session, scale, "Org Standard")
    dark = _template(session, scale, "Dark", launch_key="dark")
    dark.color_slots = {**_colors(), "appBackground": "#0d1117"}
    session.flush()
    _choose(session, user_id, "dark")
    data = _effective(app_client, user_id)
    assert data["colorSlots"]["appBackground"] == "#0d1117"


def test_effective_never_serves_another_users_template(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    org = _template(session, scale, "Org Standard")
    theirs = _template(session, scale, "Theirs", owner=_user(session, "mentor.two"))
    theirs.color_slots = {**_colors(), "accent": "#000000"}
    session.flush()
    # Even a choice row naming a foreign template resolves to the org
    # default — the ownership boundary holds on the read path too.
    _choose(session, user_id, str(theirs.color_template_id))
    data = _effective(app_client, user_id)
    assert data["colorSlots"] == org.color_slots


def test_effective_ignores_a_stale_or_unknown_choice(
    app_client: TestClient, session: Session, user_id: uuid.UUID, scale: TypeScale
) -> None:
    org = _template(session, scale, "Org Standard")
    _choose(session, user_id, str(uuid.uuid4()))
    data = _effective(app_client, user_id)
    assert data["colorSlots"] == org.color_slots
