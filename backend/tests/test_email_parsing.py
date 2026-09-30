from __future__ import annotations

from email.message import EmailMessage

import httpx
import pytest

from app.email.links import LinkResolver, check_link, extract_links, host_allowed, unwrap
from app.email.parse import auth_passed, forwarded_original_sender, html_to_text, parse_mime

HAVLOCK_HTML = """<html><body>
<p>Dear Student,</p><p>A new opportunity has been posted.</p>
<table><tr><td>Company</td><td>Acme Corp</td></tr><tr><td>Role</td><td>SDE Intern</td></tr>
<tr><td>Deadline</td><td>30 Sep 2026, 11:59 PM</td></tr></table>
<p><a href="https://nam12.safelinks.protection.outlook.com/?url=https%3A%2F%2Fapp.havlock.in%2Fjobs%2F4821&data=x">Apply Now</a></p>
<p><a href="https://app.havlock.in/unsubscribe?u=1">Unsubscribe</a> | <a href="https://twitter.com/havlock">Twitter</a></p>
</body></html>"""


def mime(**kw: str) -> bytes:
    m = EmailMessage()
    m["From"] = kw.get("sender", "Havlock <noreply@havlock.in>")
    m["To"] = "abhi@college.edu"
    m["Subject"] = kw.get("subject", "New Job: Acme Corp - SDE Intern")
    m["Date"] = kw.get("date", "Wed, 24 Sep 2026 10:00:00 +0530")
    m["Message-ID"] = kw.get("mid", "<abc123@havlock.in>")
    if "auth" in kw:
        m["Authentication-Results"] = kw["auth"]
    m.set_content(kw.get("text", "Plain version"))
    m.add_alternative(kw.get("html", HAVLOCK_HTML), subtype="html")
    if kw.get("attach"):
        m.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="JD.pdf")
    return m.as_bytes()


def test_parse_mime_basics() -> None:
    e = parse_mime(mime(attach="1"), provider_id="prov-1")
    assert e.message_id == "prov-1" and e.internet_message_id == "<abc123@havlock.in>"
    assert e.sender == "noreply@havlock.in" and e.sender_name == "Havlock"
    assert e.received_at.isoformat() == "2026-09-24T04:30:00+00:00"
    assert "Acme Corp" in html_to_text(e.html or "") and e.attachments[0].filename == "JD.pdf"


def test_links_unwrap_safelinks_and_skip_footer() -> None:
    e = parse_mime(mime())
    links = extract_links(e.html, None)
    assert [unwrap(c.url) for c in links] == ["https://app.havlock.in/jobs/4821"]
    assert links[0].text == "Apply Now" and links[0].score > 0


@pytest.mark.parametrize(
    ("host", "ok"),
    [
        ("app.havlock.in", True),
        ("havlock.in", True),
        ("evil-havlock.in", False),
        ("app.havlock.in.evil.com", False),
        ("x.app.havlock.in", True),
    ],
)
def test_host_allowed(host: str, ok: bool) -> None:
    assert host_allowed(host, ["*.havlock.in"]) is ok


def test_check_link_rules() -> None:
    allowed = ["*.havlock.in"]
    good = check_link(
        "https://nam12.safelinks.protection.outlook.com/?url=https%3A%2F%2Fapp.havlock.in%2Fjobs%2F1",
        sender_allowed=True,
        allowed_domains=allowed,
        resolver=None,
    )
    assert good.safe and good.final_url == "https://app.havlock.in/jobs/1"
    assert not check_link(
        "https://app.havlock.in/x", sender_allowed=False, allowed_domains=allowed, resolver=None
    ).safe
    assert (
        "not https"
        in check_link(
            "http://app.havlock.in/x", sender_allowed=True, allowed_domains=allowed, resolver=None
        ).reason
    )
    assert (
        "not an allowed"
        in check_link(
            "https://bit.ly/abc", sender_allowed=True, allowed_domains=allowed, resolver=None
        ).reason
    )
    assert (
        "credentials"
        in check_link(
            "https://user:pw@app.havlock.in/",
            sender_allowed=True,
            allowed_domains=allowed,
            resolver=None,
        ).reason
    )


def test_resolver_follows_tracking_redirects_with_head_only() -> None:
    seen: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, str(req.url)))
        if req.url.host == "click.mailer.com":
            return httpx.Response(302, headers={"location": "https://app.havlock.in/jobs/77"})
        if req.url.host == "evil.click":
            return httpx.Response(302, headers={"location": "https://phish.example/login"})
        return httpx.Response(200)

    r = LinkResolver(httpx.MockTransport(handler))
    ok = check_link(
        "https://click.mailer.com/t/abc",
        sender_allowed=True,
        allowed_domains=["*.havlock.in"],
        resolver=r,
    )
    assert ok.safe and ok.final_url == "https://app.havlock.in/jobs/77"
    bad = check_link(
        "https://evil.click/x", sender_allowed=True, allowed_domains=["*.havlock.in"], resolver=r
    )
    assert not bad.safe and "phish.example" in bad.reason
    assert {m for m, _ in seen} == {"HEAD"}  # never downloads page content


def test_forwarded_sender_and_auth() -> None:
    body = (
        "---------- Forwarded message ---------\nFrom: Havlock <noreply@havlock.in>\n"
        "Date: Wed, 24 Sep 2026\nSubject: New Job\n\nApply here https://app.havlock.in/jobs/9"
    )
    fwd = parse_mime(
        mime(
            sender="Abhi <abhi@college.edu>",
            subject="Fwd: New Job",
            text=body,
            html=f"<pre>{body}</pre>",
            auth="mx.google.com; dkim=pass header.d=college.edu; dmarc=pass",
        )
    )
    assert forwarded_original_sender(fwd) == "noreply@havlock.in"
    assert auth_passed(fwd) is True
    assert forwarded_original_sender(parse_mime(mime())) is None  # not forwarded
    assert auth_passed(parse_mime(mime(auth="mx; dmarc=fail"))) is False
    assert auth_passed(parse_mime(mime())) is None
