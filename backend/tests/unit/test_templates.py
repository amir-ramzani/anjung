import pytest
from app.domains.notifications.templates import REQUIRED, render


def params(t: str) -> dict[str, str]:
    return {k: f"<{k}>" for k in REQUIRED[t]}


@pytest.mark.parametrize("template", sorted(REQUIRED))
@pytest.mark.parametrize("locale", ["en", "ms"])
def test_every_template_renders_in_both_languages(template: str, locale: str) -> None:
    subject, text, html = render(template, locale, params(template))
    assert subject and text and html.startswith("<p>")
    assert "$" not in subject + text + html  # nothing left unsubstituted


def test_malay_differs_from_english_and_unknown_locale_falls_back() -> None:
    p = params("invoice_issued")
    assert render("invoice_issued", "ms", p)[1] != render("invoice_issued", "en", p)[1]
    assert render("invoice_issued", "fr", p) == render("invoice_issued", "en", p)


def test_html_values_are_escaped_but_text_is_not() -> None:
    p = {**params("maintenance_update"), "status": "<script>x</script>"}
    _, text, html = render("maintenance_update", "en", p)
    assert "<script>" in text and "<script>" not in html and "&lt;script&gt;" in html


def test_a_missing_parameter_is_a_bug_not_an_empty_email() -> None:
    with pytest.raises(ValueError, match="pay_link"):
        render("invoice_issued", "en", {"number": "1", "amount": "2", "due_date": "3"})
    with pytest.raises(KeyError):
        render("nope", "en", {})


def test_payment_templates_carry_a_link_and_no_qr() -> None:
    for t in ("invoice_issued", "payment_reminder", "invoice_overdue"):
        _, text, html = render(t, "en", params(t))
        assert "<pay_link>" in text and "qr" not in (text + html).lower() and "<img" not in html
