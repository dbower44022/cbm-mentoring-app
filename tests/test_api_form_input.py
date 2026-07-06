"""Smart input parsing & formatting design (REQ-034, WTK-058).

Formatters and paste resolvers are pure; only the postal auto-fill read
touches the database (the REQ-061 reference rows the refresh job maintains).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from mentorapp.api import (
    PASTE_RESOLVABLE_TYPES,
    PasteResolution,
    auto_format,
    format_email,
    format_phone,
    format_postal_code,
    format_website,
    normalize_for_match,
    postal_autofill,
    resolve_paste,
)
from mentorapp.storage import PostalCode


class TestAutoFormat:
    def test_phone_ten_digits_formats_us_display(self) -> None:
        assert format_phone("555.123.4567") == "(555) 123-4567"
        assert format_phone("(555) 1234567") == "(555) 123-4567"

    def test_phone_eleven_digits_with_country_code(self) -> None:
        assert format_phone("1-555-123-4567") == "+1 (555) 123-4567"

    def test_phone_unrecognized_shapes_return_as_typed(self) -> None:
        # International / extension-bearing numbers must not be mangled into
        # a US shape — formatting is a convenience, never a gate.
        assert format_phone(" +44 20 7946 0958 ") == "+44 20 7946 0958"
        assert format_phone("x1234") == "x1234"

    def test_phone_partial_or_empty_input_returns_trimmed_as_typed(self) -> None:
        # Anything that isn't 10 digits (or 11 with a leading 1) — including
        # a local 7-digit number, an 11-digit non-US number, or nothing at
        # all — must come back as typed, trimmed, never forced into US shape.
        assert format_phone(" 555-0134 ") == "555-0134"
        assert format_phone("23334445555") == "23334445555"
        assert format_phone("") == ""

    def test_phone_formatting_never_splits_a_duplicate_match(self) -> None:
        raw = "555-123-4567"
        assert normalize_for_match("phone", format_phone(raw)) == normalize_for_match(
            "phone", raw
        )

    def test_email_trims_and_lowercases(self) -> None:
        assert format_email("  Mentor@Example.COM ") == "mentor@example.com"

    def test_website_gains_scheme_and_lowercases_host_only(self) -> None:
        assert format_website("Example.COM/Path?Q=1") == "https://example.com/Path?Q=1"
        assert format_website("HTTP://Example.com") == "http://example.com"
        assert format_website("") == ""

    def test_postal_code_formats_to_lookup_key(self) -> None:
        assert format_postal_code(" 62704-1234 ") == "62704"

    def test_postal_code_non_us_shapes_pass_through_uppercased(self) -> None:
        # The formatter is a convenience, never a gate: a code that isn't a
        # US ZIP is normalized (trim, upper) but never rejected or truncated.
        assert format_postal_code(" k1a 0b1 ") == "K1A 0B1"

    def test_website_keeps_port_and_path_case(self) -> None:
        assert (
            format_website("Example.COM:8080/Docs#Top") == "https://example.com:8080/Docs#Top"
        )

    def test_dispatch_covers_typed_fields_and_passes_others_through(self) -> None:
        assert auto_format("phone", "5551234567") == "(555) 123-4567"
        assert auto_format("email", " A@B.CO ") == "a@b.co"
        assert auto_format("website", "b.co") == "https://b.co"
        assert auto_format("postalCode", "62704-0001") == "62704"
        # Free text is never rewritten by auto-format.
        assert auto_format("text", "  keep  me  ") == "  keep  me  "

    def test_empty_values_never_raise_for_any_typed_field(self) -> None:
        # auto_format's contract: never raises, never rejects — an empty
        # control on focus-exit is the everyday case.
        for field_type in ("phone", "email", "website", "postalCode", "text"):
            assert auto_format(field_type, "") == ""


class TestResolvePaste:
    def test_full_name_fills_first_and_last(self) -> None:
        assert resolve_paste("personName", "Ada Byron Lovelace") == PasteResolution(
            components={"firstName": "Ada Byron", "lastName": "Lovelace"}
        )

    def test_last_comma_first_honored(self) -> None:
        assert resolve_paste("personName", "Lovelace, Ada") == PasteResolution(
            components={"firstName": "Ada", "lastName": "Lovelace"}
        )

    def test_single_token_fills_last_name_like_the_match_columns(self) -> None:
        assert resolve_paste("personName", "Lovelace") == PasteResolution(
            components={"lastName": "Lovelace"}
        )

    def test_address_confident_split_fills_all_components(self) -> None:
        resolved = resolve_paste("address", "12 Oak St, Apt 4, Springfield, IL 62704-1234")
        assert resolved == PasteResolution(
            components={
                "streetLine": "12 Oak St, Apt 4",
                "cityName": "Springfield",
                "stateCode": "IL",
                "postalCode": "62704",
            }
        )

    def test_address_without_state_zip_tail_stays_whole_as_remainder(self) -> None:
        # Two comma parts ("street, city") or a non "ST 62704" tail are below
        # the parser's confidence bar: nothing fills, the whole paste stays
        # visible for hand-finishing — a partial guess presented as fact is
        # exactly what REQ-034's ambiguity rule forbids.
        assert resolve_paste("address", "12 Oak St, Springfield") == PasteResolution(
            remainder="12 Oak St, Springfield"
        )
        assert resolve_paste("address", "12 Oak St, Springfield, Illinois") == PasteResolution(
            remainder="12 Oak St, Springfield, Illinois"
        )

    def test_address_ambiguity_keeps_remainder_visible_never_blocks(self) -> None:
        resolved = resolve_paste("address", "somewhere near the  old mill")
        assert resolved.components == {}
        assert resolved.remainder == "somewhere near the old mill"

    def test_unresolvable_field_type_lands_as_typed(self) -> None:
        assert resolve_paste("text", "anything at all") == PasteResolution(
            remainder="anything at all"
        )
        assert sorted(PASTE_RESOLVABLE_TYPES) == ["address", "personName"]


class TestPostalAutofill:
    def test_known_code_fills_city_and_state(self, session: Session) -> None:
        session.add(
            PostalCode(postal_code_value="62704", city_name="Springfield", state_code="IL")
        )
        session.commit()

        assert postal_autofill(session, "62704-1234") == {
            "cityName": "Springfield",
            "stateCode": "IL",
        }

    def test_unknown_code_is_unknown_not_invalid(self, session: Session) -> None:
        assert postal_autofill(session, "00000") is None

    def test_lookup_is_scoped_to_the_requested_country(self, session: Session) -> None:
        # The reference rows key on (country, code); a US row must not answer
        # for another country's identical code.
        session.add(
            PostalCode(postal_code_value="62704", city_name="Springfield", state_code="IL")
        )
        session.commit()

        assert postal_autofill(session, "62704", country_code="CA") is None

    def test_address_paste_postal_component_feeds_the_same_autofill(
        self, session: Session
    ) -> None:
        # REQ-034's one-auto-fill-path rule: the postalCode component an
        # address paste resolves IS a valid lookup key, so paste-filled and
        # typed codes take the identical path into city/state auto-fill.
        session.add(
            PostalCode(postal_code_value="62704", city_name="Springfield", state_code="IL")
        )
        session.commit()

        pasted = resolve_paste("address", "12 Oak St, Springfield, IL 62704-1234")
        assert postal_autofill(session, pasted.components["postalCode"]) == {
            "cityName": "Springfield",
            "stateCode": "IL",
        }
