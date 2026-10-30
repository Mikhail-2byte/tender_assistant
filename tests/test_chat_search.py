import pytest

from tender_app.models import Tender, chat_search_query, search_tenders


@pytest.mark.parametrize(("message", "query"), [
    ("Найди тендер 7207", "тендер 7207"),
    ("поиск тендера № 424345", "тендера № 424345"),
    ("НАЙДИ Кузбассэнерго", "Кузбассэнерго"),
    ("7207", "7207"),
    ("№7207", "7207"),
    ("Найди", ""),
    ("Найди тендер", ""),
    ("По 7207 началась переторжка", None),
])
def test_explicit_search_command(message, query):
    assert chat_search_query(message) == query


def test_exact_number_returns_all_duplicates_before_partial_matches():
    records = [Tender(id=1, number="72070"), Tender(id=2, number="7207", customer="Первый"),
               Tender(id=3, number=" 7207 ", customer="Второй")]
    assert [item.id for item in search_tenders(records, "№ 7207")] == [2, 3]


def test_search_customer_or_subject_is_case_insensitive_and_includes_closed():
    records = [Tender(id=1, number="АБ-1", customer="Кузбассэнерго", status="Победа"),
               Tender(id=2, number="АБ-2", title="Валы по чертежам")]
    assert search_tenders(records, "КУЗБАСС") == records[:1]
    assert search_tenders(records, "ЧЕРТЕЖАМ") == records[1:]
    assert not search_tenders(records, "  ")
