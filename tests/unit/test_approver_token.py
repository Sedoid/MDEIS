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


# --------------------------------------------------------------------------- the school claim
def payload_of(made):
    import base64
    import json

    text = made.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))


def test_the_school_claim_goes_in_the_token_and_comes_back():
    made = token.sign(SECRET, "u-12", "Ada Teacher", school="OCC", now=NOW)
    who = token.verify(SECRET, made, now=NOW)
    assert (who.id, who.name, who.school) == ("u-12", "Ada Teacher", "OCC")
    assert payload_of(made)["sch"] == "OCC"


def test_a_token_without_a_school_still_verifies_and_has_no_school_claim():
    made = token.sign(SECRET, "u-12", "Ada Teacher", now=NOW)
    assert "sch" not in payload_of(made), "the claim is written only when a school is given"
    who = token.verify(SECRET, made, now=NOW)
    assert who.school is None
    assert (who.id, who.name) == ("u-12", "Ada Teacher")


def test_an_old_token_made_before_the_claim_existed_still_verifies():
    # Built by hand in the old shape: four fields, no `sch`, signed the same way.
    import base64
    import hashlib
    import hmac
    import json

    body = {"uid": "u-12", "name": "Ada Teacher", "exp": NOW + 60, "nonce": "n"}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    payload = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    mac = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    old = payload + "." + base64.urlsafe_b64encode(mac).decode().rstrip("=")
    who = token.verify(SECRET, old, now=NOW)
    assert (who.id, who.name, who.school) == ("u-12", "Ada Teacher", None)


@pytest.mark.parametrize("bad", ["", "   ", 12, ["OCC"], True])
def test_a_school_that_is_not_a_plain_non_empty_string_is_not_signed(bad):
    with pytest.raises(token.TokenError, match="school"):
        token.sign(SECRET, "u-12", "Ada Teacher", school=bad, now=NOW)


@pytest.mark.parametrize("bad", ["", 12, ["OCC"], None, False])
def test_a_signed_token_whose_school_claim_is_not_a_non_empty_string_is_refused(bad):
    # A claim that is present but wrong is an error, not "no school", even when the signature holds.
    import base64
    import hashlib
    import hmac
    import json

    body = {"uid": "u-12", "name": "Ada Teacher", "exp": NOW + 60, "nonce": "n", "sch": bad}
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    payload = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    mac = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).digest()
    forged = payload + "." + base64.urlsafe_b64encode(mac).decode().rstrip("=")
    with pytest.raises(token.TokenError, match="school"):
        token.verify(SECRET, forged, now=NOW)


def test_changing_the_school_inside_a_token_breaks_the_signature():
    mine = token.sign(SECRET, "u-12", "Ada Teacher", school="OCC", now=NOW, nonce="n")
    other = token.sign(SECRET, "u-12", "Ada Teacher", school="ZZZ", now=NOW, nonce="n")
    payload, signature = mine.split(".")
    assert other.split(".")[0] != payload
    with pytest.raises(token.TokenError, match="signature"):
        token.verify(SECRET, other.split(".")[0] + "." + signature, now=NOW)


def test_adding_a_school_to_a_token_that_had_none_breaks_the_signature():
    plain = token.sign(SECRET, "u-12", "Ada Teacher", now=NOW, nonce="n")
    with_school = token.sign(SECRET, "u-12", "Ada Teacher", school="OCC", now=NOW, nonce="n")
    with pytest.raises(token.TokenError, match="signature"):
        token.verify(SECRET, with_school.split(".")[0] + "." + plain.split(".")[1], now=NOW)
