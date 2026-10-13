"""The public page (site/index.html) and the proxy settings that serve it. Static checks only.

The page is shown to strangers, so it is held to the project's own rules: no script, no request to
any other address, the made-up-data notice, no label on a learner, no claim about test results.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PAGE = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
ALLOWED_LINKS = {"https://github.com/Sedoid/MDEIS"}


class Found(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.links, self.sources = [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for name, value in attrs:
            if name == "href":
                self.links.append(value)
            if name in ("src", "srcset", "data", "action"):
                self.sources.append(value)


def parsed():
    found = Found()
    found.feed(PAGE)
    return found


def test_the_page_runs_no_script_and_loads_nothing_from_anywhere_else():
    found = parsed()
    assert "script" not in found.tags and "iframe" not in found.tags and "form" not in found.tags
    assert found.sources == [], "no image, script or frame is fetched"
    assert "@import" not in PAGE and "url(" not in PAGE, "no stylesheet fetches anything"
    external = {link for link in found.links if re.match(r"https?://", link or "")}
    assert external <= ALLOWED_LINKS, external - ALLOWED_LINKS


def test_the_page_says_the_data_is_made_up_and_the_licence():
    assert "made-up data" in PAGE and "not for use with real learners" in PAGE
    assert "Apache-2.0" in PAGE


def test_the_page_uses_none_of_the_words_the_rulebook_bans_except_to_forbid_them():
    template = yaml.safe_load((ROOT / "templates" / "v1.yaml").read_text(encoding="utf-8"))
    banned = template["wording"][0]["banned"]
    text = re.sub(r"<[^>]+>", " ", PAGE)
    for word in banned:
        for match in re.finditer(rf"\b{word}\b", text, re.IGNORECASE):
            context = text[max(0, match.start() - 60) : match.end() + 20]
            assert "never" in context or "not" in context, f"{word!r} used as a label: {context!r}"


def test_the_page_claims_no_test_result_and_no_unfinished_feature_as_done():
    lower = PAGE.lower()
    for claim in ("all tests pass", "100%", "passes every", "production-ready", "accuracy of"):
        assert claim not in lower, claim
    assert "does not repeat test results" in lower


def test_the_page_fits_a_phone_and_both_colour_schemes():
    assert 'name="viewport"' in PAGE and "prefers-color-scheme: dark" in PAGE


def test_the_proxy_pins_its_image_binds_to_loopback_by_default_and_serves_only_two_things():
    proxy = (ROOT / "deploy" / "compose" / "proxy.yml").read_text(encoding="utf-8")
    assert "latest" not in proxy and re.search(r"caddy:\d+\.\d+\.\d+", proxy)
    assert "${PROXY_BIND:-127.0.0.1}" in proxy, "nothing is public unless it is set on purpose"
    assert 'profiles: ["prod"]' in proxy
    compose = yaml.safe_load(proxy)
    assert compose["services"]["proxy"]["networks"] == ["edge"], "it reaches Open WebUI only"
    caddy = (ROOT / "deploy" / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    forwarded = re.findall(r"reverse_proxy\s+(\S+)", caddy)
    assert forwarded == ["webui:8080"], forwarded
    for private in ("gateway", "orchestrator", "mdeis-mcp", "postgres", "n8n", "pg-mcp"):
        assert private not in caddy.split("reverse_proxy")[1], private
    assert "Strict-Transport-Security" in caddy and "-Server" in caddy


def test_the_page_only_proxy_file_serves_the_page_and_forwards_nothing():
    caddy = (ROOT / "deploy" / "caddy" / "Caddyfile.page").read_text(encoding="utf-8")
    directives = "\n".join(line for line in caddy.splitlines() if not line.lstrip().startswith("#"))
    assert "reverse_proxy" not in directives, "nothing is forwarded to any container"
    assert "root * /srv/site" in directives and "respond \"Not found\" 404" in directives
    assert "http://:80" in directives, "plain http: it must carry nothing secret"
    assert "Content-Security-Policy" in directives and "-Server" in directives
    for private in ("webui", "gateway", "orchestrator", "mdeis-mcp", "postgres", "n8n"):
        assert private not in directives, private


def test_the_bare_ip_proxy_file_forwards_only_to_the_chat_and_the_upload_page():
    caddy = (ROOT / "deploy" / "caddy" / "Caddyfile.ip").read_text(encoding="utf-8")
    targets = [line.split()[1] for line in caddy.splitlines() if "reverse_proxy" in line
               and not line.lstrip().startswith("#")]
    assert sorted(targets) == ["portal:8080", "webui:8080"]
