"""
Unit and Contract Verification for Ukrainian Text Normalization and Stemming.

Implements TKT-002 | REQ-F-004, REQ-NF-006, Constraint 6.3
Acceptance Criteria: AC-F-004
Contract: openapi-v2.yaml SearchQueryResponse.normalized_stems
"""

import pytest

from src.core.text_processing import (
    TextNormalizer,
    UkrainianStemmer,
    normalize_query_stems,
)


def test_tkt_002_openapi_contract_example():
    """
    Verify exact contract example from openapi-v2.yaml (SearchQueryResponse.normalized_stems):
    Query: 'загубив робочий телефон у метро'
    Stems: ['загуб', 'робоч', 'телефон', 'метр']

    Implements TKT-002 | REQ-F-004, REQ-NF-006
    """
    query = "загубив робочий телефон у метро"
    stems = normalize_query_stems(query)
    assert stems == ["загуб", "робоч", "телефон", "метр"]


def test_tkt_002_printer_inflections_stem_equivalence():
    """
    Verify AC-F-004: All inflected cases and numbers of 'принтер' normalize to 'принтер'.

    Implements TKT-002 | REQ-F-004 | AC-F-004
    """
    inflections = [
        "принтер",
        "принтера",
        "принтерові",
        "принтеру",
        "принтером",
        "принтері",
        "принтери",
        "принтерів",
        "принтерам",
        "принтерами",
        "принтерах",
    ]
    stems = [UkrainianStemmer.stem(w) for w in inflections]
    assert all(s == "принтер" for s in stems), f"Non-uniform stems for 'принтер': {dict(zip(inflections, stems))}"


def test_tkt_002_password_inflections_stem_equivalence():
    """
    Verify inflections of 'пароль' normalize to the single stem 'парол'.

    Implements TKT-002 | REQ-F-004 | AC-F-004
    """
    inflections = ["пароль", "пароля", "паролем", "паролі", "паролю", "паролів", "паролями", "паролях"]
    stems = [UkrainianStemmer.stem(w) for w in inflections]
    assert all(s == "парол" for s in stems), f"Non-uniform stems for 'пароль': {dict(zip(inflections, stems))}"


def test_tkt_002_email_and_network_inflections():
    """
    Verify inflections of 'пошта' and 'мережа' normalize to uniform stems.

    Implements TKT-002 | REQ-F-004 | AC-F-004
    """
    poshta_forms = ["пошта", "пошти", "поштою", "пошті", "пошту"]
    assert len(set(UkrainianStemmer.stem(w) for w in poshta_forms)) == 1
    assert UkrainianStemmer.stem("пошта") == "пошт"

    merezha_forms = ["мережа", "мережі", "мережею", "мережу", "мережах"]
    assert len(set(UkrainianStemmer.stem(w) for w in merezha_forms)) == 1
    assert UkrainianStemmer.stem("мережа") == "мереж"


def test_tkt_002_paper_and_software_inflections():
    """
    Verify inflections of 'папір' and 'програма' normalize to uniform stems.

    Implements TKT-002 | REQ-F-004 | AC-F-004
    """
    papir_forms = ["папір", "паперу", "папером", "папері", "папери", "паперами"]
    assert len(set(UkrainianStemmer.stem(w) for w in papir_forms)) == 1
    assert UkrainianStemmer.stem("папір") == "папір"

    programa_forms = ["програма", "програми", "програмою", "програму", "програмах", "програмами"]
    assert len(set(UkrainianStemmer.stem(w) for w in programa_forms)) == 1
    assert UkrainianStemmer.stem("програма") == "програм"


def test_tkt_002_stop_words_filtering():
    """
    Verify Ukrainian prepositions, conjunctions, particles, and pronouns are filtered out.

    Implements TKT-002 | REQ-F-004
    """
    stop_phrase = "я і мій він в у до з на під що як якщо це ось"
    stems = normalize_query_stems(stop_phrase)
    assert stems == []


def test_tkt_002_punctuation_stripping():
    """
    Verify punctuation marks, quotes, brackets, and math symbols are stripped cleanly.

    Implements TKT-002 | REQ-F-004
    """
    raw_text = "Привіт! Що робити, якщо: принтер (мережевий) не друкує?! #помилка [код 404]..."
    stems = normalize_query_stems(raw_text)
    assert "принтер" in stems
    assert "мережев" in stems
    assert "друк" in stems
    assert "помилк" in stems
    assert "код" in stems
    assert "404" in stems
    for s in stems:
        assert s.isalnum()


def test_tkt_002_apostrophe_normalization():
    """
    Verify that diverse typographical apostrophes within Ukrainian words are handled consistently.

    Implements TKT-002 | REQ-F-004, REQ-NF-006
    """
    variants = ["комп'ютер", "комп’ютер", "компʼютер", "комп`ютер"]
    for variant in variants:
        stems = normalize_query_stems(variant)
        assert stems == ["компютер"], f"Failed for variant: {variant}"

    connection_variants = ["з'єднання", "з’єднання", "зʼєднання"]
    for variant in connection_variants:
        stems = normalize_query_stems(variant)
        assert len(stems) == 1
        assert stems[0] == "зєдн"


def test_tkt_002_technical_loanwords():
    """
    Verify technical terms and abbreviations (VPN, Wi-Fi, SSO, 2FA) normalize predictably.

    Implements TKT-002 | REQ-F-004
    """
    assert normalize_query_stems("Wi-Fi") == ["wifi"]
    assert normalize_query_stems("wifi") == ["wifi"]
    assert normalize_query_stems("Налаштувати VPN та 2FA") == ["налашт", "vpn", "2fa"]
    assert normalize_query_stems("Microsoft Outlook та SSO") == ["microsoft", "outlook", "sso"]


def test_tkt_002_verbal_noun_and_verb_alignment():
    """
    Verify that verbal nouns align with verbs after suffix stripping.

    Implements TKT-002 | REQ-F-004
    """
    assert UkrainianStemmer.stem("налаштувати") == UkrainianStemmer.stem("налаштування")
    assert UkrainianStemmer.stem("підключити") == UkrainianStemmer.stem("підключення")
    assert UkrainianStemmer.stem("з'єднати") == UkrainianStemmer.stem("з'єднання")
    assert UkrainianStemmer.stem("друкувати") == UkrainianStemmer.stem("друкує")


def test_tkt_002_empty_and_whitespace_inputs():
    """
    Verify handling of edge-case inputs (empty string, whitespace, non-alphanumeric).

    Implements TKT-002 | REQ-F-004
    """
    assert normalize_query_stems("") == []
    assert normalize_query_stems("   ") == []
    assert normalize_query_stems("!!! ??? --- ...") == []


def test_tkt_002_scenario_2_query():
    """
    Verify BDD Scenario 2 query stemming:
    'Що робити, якщо в офісі зажувало папір у принтері?'

    Implements TKT-002 | REQ-F-004 | Scenario 2
    """
    query = "Що робити, якщо в офісі зажувало папір у принтері?"
    stems = normalize_query_stems(query)
    assert "принтер" in stems
    assert "папір" in stems
    assert "зажув" in stems
    assert "офіс" in stems
    assert "роб" in stems
    # Ensure stop-words are gone
    assert "що" not in stems
    assert "якщо" not in stems
    assert "в" not in stems
    assert "у" not in stems
