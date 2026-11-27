"""A chapter describing a sign-in token's claims is no person's account (DEFECTS 704): a made chapter text (every name
invented) that names the claims in code marks after "user id" and "account" goes through phase 4's whole text check, as
the chapter step sends it, and passes; the same text with a person's account in quotes is refused."""

from support import harness

from models.validation_report import ValidationReport
from validators import document_validator

CHAPTER = """### How AcmeSync signs a user in (T01.1)

After the sign-in, AcmeSync's gateway receives a token. Its payload holds the user id claim `sub`, the claim `acr`
for how strongly the user was checked, and the account's `session_state`, which ties the token to one session [F12.1].

```json
{"sub": "<id>", "acr": "1", "session_state": "<state>"}
```
[F12.2]
"""


def test_a_chapter_naming_token_claims_passes_and_a_quoted_account_is_refused():
    section = ValidationReport(subject="chapter-01")
    document_validator.check_text(section, "chapter-01", CHAPTER)
    harness.check("token claims pass", "DOCUMENT_PERSONAL_IN_TEXT" not in harness.codes(section), str(section.findings))
    person = ValidationReport(subject="chapter-01")
    document_validator.check_text(person, "chapter-01",
                                  CHAPTER + '\nThe demo signs in as "jonas.berg42" every time [F12.3].\n')
    harness.check("a quoted account is refused", "DOCUMENT_PERSONAL_IN_TEXT" in harness.codes(person),
                  str(person.findings))
