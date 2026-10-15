"""The approver token: signed by the gateway, checked by the toolbox.

Pure tests, no database. Every value is typed by hand.
"""

import pytest

from mdeis.core import approver_token as token

SECRET = "a-test-secret-not-a-real-one"
NOW = 1_000_000  # an arbitrary moment; the code takes the clock as an argument for exactly this


def test_a_signed_token_names_the_person():
    who = token.verify(SECRET, token.sign(SECRET, "u-12", "Ada Teacher", now=NOW), now=NOW)
    assert (who.id, who.name) == ("u-12", "Ada Teacher")


def test_a_token_signed_with_another_secret_is_refused():
    forged = token.sign("some-other-secret", "u-12", "Ada Teacher", now=NOW)
    with pytest.raises(token.TokenError, match="signature"):
        token.verify(SECRET, forged, now=NOW)


def test_changing_the_name_inside_a_token_breaks_it():
    payload, signature = token.sign(SECRET, "u-12", "Ada Teacher", now=NOW).split(".")
    other = token.sign(SECRET, "u-12", "Dr Fake", now=NOW).split(".")[0]
    assert other != payload
    with pytest.raises(token.TokenError, match="signature"):
        token.verify(SECRET, other + "." + signature, now=NOW)


def test_an_expired_token_is_refused():
    made = token.sign(SECRET, "u-12", "Ada Teacher", now=NOW, lifetime=60)
    assert token.verify(SECRET, made, now=NOW + 59).id == "u-12"
    with pytest.raises(token.TokenError, match="expired"):
        token.verify(SECRET, made, now=NOW + 60)


@pytest.mark.parametrize("bad", [None, "", "no-dot", "a.b.c", "x."])
def test_a_malformed_token_is_refused(bad):
    with pytest.raises(token.TokenError):
        token.verify(SECRET, bad, now=NOW)


def test_there_is_no_token_without_a_secret_a_name_or_an_id():
    with pytest.raises(token.TokenError):
        token.sign("", "u-12", "Ada Teacher")
    with pytest.raises(token.TokenError):
        token.sign(SECRET, "", "Ada Teacher")
    with pytest.raises(token.TokenError):
        token.sign(SECRET, "u-12", "")
    with pytest.raises(token.TokenError):
        token.verify("", token.sign(SECRET, "u-12", "Ada Teacher", now=NOW), now=NOW)


def test_two_tokens_for_the_same_person_differ():
    first = token.sign(SECRET, "u-12", "Ada Teacher", now=NOW)
    second = token.sign(SECRET, "u-12", "Ada Teacher", now=NOW)
    assert first != second
