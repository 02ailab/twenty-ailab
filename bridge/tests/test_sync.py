# Unit tests for the pure sync helpers: company-from-email derivation (the "no fake
# bucket" decision), name handling (P2-1 split preservation), and the Note transcript
# filtering. No DB/HTTP — module-level pure functions only.
from app.services import sync


def test_company_from_corporate_email():
    assert sync._company_name_from_email("a@acme.com") == "Acme"
    assert sync._company_name_from_email("a@mail.acme.com") == "Acme"
    assert sync._company_name_from_email("a@acme.co.uk") == "Acme"


def test_company_from_free_or_malformed_email_is_none():
    for addr in ("a@gmail.com", "a@yandex.ru", "a@proton.me", "no-at-sign", "a@", "@acme.com", ""):
        assert sync._company_name_from_email(addr) is None


def test_registrable_label_multi_part_tld():
    assert sync._registrable_label("acme.co.uk") == "acme"
    assert sync._registrable_label("foo.bar.com.br") == "bar"


def test_should_assign_saldo_client_id():
    FIELD = "saldoClientId"
    # created -> always assign (record may be None: it's brand new)
    assert sync._should_assign_saldo_client_id("created", None, FIELD) is True
    assert sync._should_assign_saldo_client_id("created", {}, FIELD) is True
    # updated + existing record already has a number -> never reassign (stable key)
    assert sync._should_assign_saldo_client_id("updated", {FIELD: 2000}, FIELD) is False
    # updated + existing record lacks it (created before feature) -> backfill
    assert sync._should_assign_saldo_client_id("updated", {FIELD: None}, FIELD) is True
    assert sync._should_assign_saldo_client_id("updated", {FIELD: ""}, FIELD) is True
    assert sync._should_assign_saldo_client_id("updated", {}, FIELD) is True
    # updated but record unreadable (None) -> don't allocate (avoid burning a number)
    assert sync._should_assign_saldo_client_id("updated", None, FIELD) is False


def test_saldo_identifier():
    FIELD = "saldoClientId"
    # disabled -> never produce an identifier
    assert sync._saldo_identifier({FIELD: 2000}, FIELD, False) is None
    # numeric forms all normalize to a plain digit string
    assert sync._saldo_identifier({FIELD: 2000}, FIELD, True) == "2000"
    assert sync._saldo_identifier({FIELD: 2000.0}, FIELD, True) == "2000"
    assert sync._saldo_identifier({FIELD: "2001"}, FIELD, True) == "2001"
    # absent / blank -> None
    assert sync._saldo_identifier({}, FIELD, True) is None
    assert sync._saldo_identifier({FIELD: None}, FIELD, True) is None
    assert sync._saldo_identifier({FIELD: ""}, FIELD, True) is None
    # non-numeric junk -> None (never write garbage into the unique field)
    assert sync._saldo_identifier({FIELD: "abc"}, FIELD, True) is None


def test_is_pseudonym_name():
    # ContactPrivacy redacted forms — must be recognized so they never overwrite real names.
    assert sync._is_pseudonym_name("Клиент #2005") is True
    assert sync._is_pseudonym_name("Клиент #10") is True
    assert sync._is_pseudonym_name("  Клиент #7  ") is True
    # real names / empty -> not a pseudonym
    assert sync._is_pseudonym_name("Иван Петров") is False
    assert sync._is_pseudonym_name("Клиент") is False
    assert sync._is_pseudonym_name("Клиент #abc") is False
    assert sync._is_pseudonym_name("Client #5") is False
    assert sync._is_pseudonym_name("") is False
    assert sync._is_pseudonym_name(None) is False


def test_telegram_username():
    assert sync._telegram_username({"additional_attributes": {"username": "durov"}}) == "durov"
    assert sync._telegram_username({"additional_attributes": {"username": "@durov"}}) == "durov"
    # falls back to the social_ duplicate
    assert sync._telegram_username({"additional_attributes": {"social_telegram_user_name": "neo"}}) == "neo"
    # absent / blank -> None
    assert sync._telegram_username({"additional_attributes": {}}) is None
    assert sync._telegram_username({}) is None
    assert sync._telegram_username({"additional_attributes": {"username": "  "}}) is None


def test_telegram_id():
    contact = {"contact_inboxes": [{"source_id": "123456789"}]}
    assert sync._telegram_id(contact) == "123456789"
    # negative (group/channel) ids allowed
    assert sync._telegram_id({"contact_inboxes": [{"source_id": "-100200"}]}) == "-100200"
    # skip non-numeric source_ids (e.g. web widget uuid), take the numeric telegram one
    mixed = {"contact_inboxes": [{"source_id": "abc-uuid"}, {"source_id": "555"}]}
    assert sync._telegram_id(mixed) == "555"
    # none present -> None
    assert sync._telegram_id({"contact_inboxes": []}) is None
    assert sync._telegram_id({}) is None
    assert sync._registrable_label("acme.com") == "acme"
    assert sync._registrable_label("localhost") == ""


def test_split_name():
    assert sync.split_name("John Smith") == ("John", "Smith")
    assert sync.split_name("John van der Berg") == ("John", "van der Berg")
    assert sync.split_name("Cher") == ("Cher", "")
    assert sync.split_name("  ") == ("", "")


def test_full_name_join():
    assert sync._full_name({"firstName": "John", "lastName": "Smith"}) == "John Smith"
    assert sync._full_name({"firstName": "Cher", "lastName": ""}) == "Cher"
    assert sync._full_name({}) == ""


def test_core_matches_skips_name_when_absent_from_desired():
    person = {"name": {"firstName": "Refined", "lastName": "Split"},
              "emails": {"primaryEmail": "a@acme.com"}}
    # desired omits name (the P2-1 path that preserves a human-refined split) but the
    # email already matches -> no write needed.
    assert sync._core_matches(person, {"emails": {"primaryEmail": "a@acme.com"}}) is True
    # email differs -> write needed even though name is omitted.
    assert sync._core_matches(person, {"emails": {"primaryEmail": "b@acme.com"}}) is False


def test_core_matches_compares_name_when_present():
    person = {"name": {"firstName": "A", "lastName": "B"}}
    assert sync._core_matches(person, {"name": {"firstName": "A", "lastName": "B"}}) is True
    assert sync._core_matches(person, {"name": {"firstName": "A", "lastName": "C"}}) is False


def test_build_note_markdown_filters_and_labels():
    messages = [
        {"message_type": 0, "content": "Здравствуйте"},          # client
        {"message_type": 1, "content": "Добрый день"},           # agent
        {"message_type": 2, "content": "Conversation resolved"},  # activity -> skip
        {"message_type": 1, "content": "secret note", "private": True},  # private -> skip
        {"message_type": 0, "content": "   "},                    # empty -> skip
    ]
    body, count = sync._build_note_markdown(messages, max_messages=100)
    assert count == 2
    assert body == "**Клиент:** Здравствуйте\n\n**Агент:** Добрый день"


def test_build_note_markdown_caps_to_most_recent():
    messages = [{"message_type": 0, "content": f"m{i}"} for i in range(5)]
    body, count = sync._build_note_markdown(messages, max_messages=2)
    assert count == 2
    assert body == "**Клиент:** m3\n\n**Клиент:** m4"  # most recent kept
