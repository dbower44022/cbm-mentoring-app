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

    def test_dispatch_covers_typed_fields_and_passes_others_through(self) -> None:
        assert auto_format("phone", "5551234567") == "(555) 123-4567"
        assert auto_format("email", " A@B.CO ") == "a@b.co"
        assert auto_format("website", "b.co") == "https://b.co"
        assert auto_format("postalCode", "62704-0001") == "62704"
        # Free text is never rewritten by auto-format.
        assert auto_format("text", "  keep  me  ") == "  keep  me  "


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
