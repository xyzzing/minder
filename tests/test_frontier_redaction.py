"""Frontier-consult redaction profiles (P4.1).

The trace store keeps consult *excerpts*; these tests pin that key-shaped
strings, bearer tokens and emails never survive into storage, and that the
external-prohibited profile refuses response-derived text outright.
"""
from minder_memory import frontier_redaction as fr


def test_api_key_shaped_strings_are_stripped():
    text = ("request failed for key sk-proj-abc123DEF456 and "
            "ghp_0123456789abcdefXYZ — retry")
    out = fr.redact_for_profile(text)
    assert "sk-proj-abc123DEF456" not in out
    assert "ghp_0123456789abcdefXYZ" not in out
    assert fr.canon._REDACTED in out


def test_bearer_tokens_and_emails_are_stripped():
    out = fr.redact_for_profile(
        "Authorization: Bearer abc.def-ghi_jkl/mno012345 contact "
        "ops@example.com")
    assert "abc.def-ghi_jkl/mno012345" not in out
    assert "ops@example.com" not in out
    assert "ops@example" not in out


def test_error_excerpts_and_relative_paths_survive():
    out = fr.redact_for_profile(
        "FileNotFoundError: docs/operator-web.md (exit code 1)")
    assert "FileNotFoundError" in out
    assert "docs/operator-web.md" in out


def test_oversized_body_is_truncated():
    out = fr.redact_for_profile("x" * (fr.MAX_BODY_CHARS + 500))
    assert len(out) <= fr.MAX_BODY_CHARS + len("\n...[truncated]")
    assert out.endswith("...[truncated]")


def test_deterministic_and_never_raises():
    text = "Bearer tok-12345678 user@host.io"
    assert fr.redact_for_profile(text) == fr.redact_for_profile(text)
    # non-string inputs are stringified, never raised over; None is "falsy"
    # and collapses to the empty string by canon.redact's `text or ""`
    assert fr.redact_for_profile(None) == ""
    assert fr.redact_for_profile(42) == "42"
    assert fr.redact_for_profile({"a": 1}) == "{'a': 1}"


def test_external_prohibited_refuses_response_text():
    assert fr.allows_response_text(fr.INTERNAL_CODE_DEFAULT) is True
    assert fr.allows_response_text(fr.EXTERNAL_PROHIBITED) is False
    # an unknown profile is not the prohibited one: allow, but the trace
    # store only ever writes profiles from PROFILES
    assert fr.allows_response_text("made-up") is True
    assert fr.EXTERNAL_PROHIBITED in fr.PROFILES
