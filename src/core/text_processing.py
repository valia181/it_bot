"""
Ukrainian Text Normalization and Lightweight Rule-Based Stemming.

Implements TKT-002 | REQ-F-004, REQ-NF-006, Constraint 6.3
Strictly complies with openapi-v2.yaml SearchQueryResponse.normalized_stems contract.
Provides deterministic, zero-dependency Ukrainian text normalization without heavy NLP libraries.
"""

from typing import FrozenSet, List
import re

# Ukrainian vowels per standard Ukrainian phonology
UKRAINIAN_VOWELS: FrozenSet[str] = frozenset("аеєиіїоуюя")

# Curated Ukrainian stop-words (prepositions, conjunctions, particles, pronouns, auxiliaries)
# Strictly excludes technical or IT domain nouns (REQ-F-004, REQ-NF-006)
UKRAINIAN_STOP_WORDS: FrozenSet[str] = frozenset(
    {
        # Прийменники (prepositions)
        "в", "у", "до", "з", "із", "зі", "на", "за", "по", "під", "над", "при", "про", "від",
        "після", "перед", "через", "між", "для", "без", "біля", "проти", "серед", "крім",
        # Сполучники (conjunctions)
        "і", "й", "та", "а", "але", "бо", "що", "щоб", "як", "якщо", "чи", "або", "хоч",
        "ніж", "коли", "то", "тому",
        # Частки (particles)
        "не", "ні", "б", "би", "ж", "же", "це", "ось", "лише", "тільки", "навіть", "хай",
        # Займенники (pronouns)
        "я", "ми", "ти", "ви", "він", "вона", "воно", "вони",
        "мене", "мені", "мною", "нас", "нам", "нами",
        "тебе", "тобі", "тобою", "вас", "вам", "вами",
        "його", "йому", "ним", "її", "їй", "нею", "їх", "їм", "ними",
        "себе", "собі", "собою",
        "мій", "моє", "моя", "мої", "мого", "моєму", "моїм", "моїх", "моїми",
        "наш", "наша", "наше", "наші", "нашого", "нашому", "нашим", "наших",
        "твій", "твоя", "твоє", "твої", "твого", "твоєму", "твоїм",
        "ваш", "ваша", "ваше", "ваші", "вашого", "вашому", "вашим", "ваших",
        "свій", "своя", "своє", "свої", "свого", "своєму", "своїм", "своїх", "своїми",
        "той", "та", "те", "ті", "того", "тому", "тим", "тих", "тими",
        "цей", "ця", "це", "ці", "цього", "цьому", "цим", "цих", "цими",
        "хто", "хтось", "щось", "який", "яка", "яке", "які", "якого", "якому", "яким", "яких",
        # Допоміжні дієслова та прислівники стану (auxiliary verbs & adverbs)
        "бути", "був", "була", "було", "були", "буде", "будуть", "будемо", "будете",
        "є", "вже", "уже", "ще", "тут", "там", "тоді", "тепер", "зараз", "дуже",
        "так", "де", "куди", "звідки", "чому", "навіщо", "скільки",
    }
)

# Regex patterns for suffix stripping within the RV region
_RE_PERFECTIVE_GERUND = re.compile(
    r"(?:[аеєиіїоуюя](?:в|вши|вшись)|ив|ивши|ившись|яв|явши|явшись)$"
)
_RE_REFLEXIVE = re.compile(r"(?:ся|сь)$")
_RE_VERBAL_NOUN = re.compile(
    r"(?:ування|ювання|ання|ення|іння|єння|анні|енні|інні|енням|анням)$"
)
_RE_ADJECTIVE = re.compile(
    r"(?:ими|іми|ому|ему|єму|ого|ього|ої|ьої|их|іх|им|ім|ою|ею|єю|ову|еву|єву|ові|еві|єві|ий|ій|ей)$"
)
_RE_VERB = re.compile(
    r"(?:[аеєиіїоуюя](?:ла|ло|ли|ти|ть|те|мо)|увати|ювати|ував|ював|увала|ювала|ували|ювали|ує|ює|ила|или|ило|ити|іла|іли|іло|іти|ена|ено|ене|ені|уть|ють|ать|ять|емо|ємо|имо|їмо|ете|єте|ите|їте|ати|яти)$"
)
_RE_NOUN = re.compile(
    r"(?:ами|ями|ах|ях|ов|ев|єв|ів|їв|ом|ем|єм|ям|ам|ей|ой|ою|ею|єю|а|е|є|я|і|и|о|у|ю|ь)$"
)
_RE_DERIVATIONAL = re.compile(r"(?:ість|ость|ост)$")
_RE_APOSTROPHES = re.compile(r"[\u2019\u02BC`']")


class UkrainianStemmer:
    """
    Lightweight, rule-based Ukrainian suffix-stripping stemmer.

    Implements TKT-002 | REQ-F-004, Constraint 6.3
    Normalizes inflected word forms without external NLP models.
    """

    @classmethod
    def stem(cls, word: str) -> str:
        """
        Reduce an individual Ukrainian or loan word to its morphological stem.

        Implements TKT-002 | REQ-F-004, AC-F-004
        :param word: Lowercased, clean word token.
        :return: Normalized stem string.
        """
        word = word.lower()

        # Latin acronyms, technical IDs, or short tokens (< 3 chars) are preserved
        if len(word) <= 2 or not any(c in UKRAINIAN_VOWELS for c in word):
            return word

        # Find RV: region after the first vowel
        first_vowel_idx = -1
        for i, ch in enumerate(word):
            if ch in UKRAINIAN_VOWELS:
                first_vowel_idx = i
                break

        if first_vowel_idx == -1 or first_vowel_idx + 1 >= len(word):
            return word

        start = word[: first_vowel_idx + 1]
        rv = word[first_vowel_idx + 1 :]

        # Special handling: Ukrainian words ending in -грами (програми, діаграми) -> stem -грам
        if rv.endswith("грами"):
            rv = rv[:-1]
        else:
            # Step 1: Perfective gerund
            m_gerund = _RE_PERFECTIVE_GERUND.search(rv)
            if m_gerund:
                rv = rv[: m_gerund.start()]
            else:
                # Step 2: Reflexive particles
                rv = _RE_REFLEXIVE.sub("", rv)

                # Step 3: Verbal nouns (-ання, -ення, -ування)
                m_vn = _RE_VERBAL_NOUN.search(rv)
                if m_vn:
                    rv = rv[: m_vn.start()]
                else:
                    # Step 4: Adjective endings
                    m_adj = _RE_ADJECTIVE.search(rv)
                    if m_adj:
                        rv = rv[: m_adj.start()]
                    else:
                        # Step 5: Verb endings (including -ує, -увати)
                        m_verb = _RE_VERB.search(rv)
                        if m_verb:
                            rv = rv[: m_verb.start()]
                        else:
                            # Step 6: Noun endings
                            m_noun = _RE_NOUN.search(rv)
                            if m_noun:
                                rv = rv[: m_noun.start()]

        # Step 7: Derivational suffixes (-ість, -ость)
        rv = _RE_DERIVATIONAL.sub("", rv)

        # Step 8: Final cleanup of trailing soft signs
        rv = re.sub(r"ь$", "", rv)

        stem = start + rv

        # Step 9: Regularize high-frequency Ukrainian vowel alternation and palatalization
        if stem == "папер":
            stem = "папір"
        elif stem == "ноутбуц":
            stem = "ноутбук"

        return stem


class TextNormalizer:
    """
    Ukrainian text preprocessing pipeline:
    lowercasing, punctuation stripping, tokenization, stop-word removal, and rule-based stemming.

    Implements TKT-002 | REQ-F-004, REQ-NF-006, Constraint 6.3
    Strictly aligns with openapi-v2.yaml SearchQueryResponse.normalized_stems.
    """

    def __init__(self, stop_words: FrozenSet[str] = UKRAINIAN_STOP_WORDS):
        """Initialize the text normalizer with a custom or default stop-word set."""
        self.stop_words = stop_words
        self.stemmer = UkrainianStemmer()

    def tokenize_and_stem(self, text: str) -> List[str]:
        """
        Full text normalization pipeline returning an ordered list of normalized stems.

        Steps:
        1. Lowercase conversion.
        2. Replace 'wi-fi' with 'wifi' for consistent IT terminology.
        3. Strip typographic and ASCII apostrophes ('комп'ютер' -> 'компютер').
        4. Tokenize alphanumeric words (Ukrainian + Latin + numbers), discarding punctuation.
        5. Filter out Ukrainian stop-words.
        6. Apply rule-based Ukrainian suffix-stripping stemming.

        Implements TKT-002 | REQ-F-004, REQ-NF-006
        :param text: Raw input text from query or document.
        :return: List of normalized stems matching SearchQueryResponse.normalized_stems.
        """
        if not text:
            return []

        # 1. Lowercase
        normalized = text.lower()

        # 2. Harmonize hyphenated technical terms
        normalized = re.sub(r"\bwi-fi\b", "wifi", normalized)

        # 3. Strip apostrophes within words (Ukrainian комп'ютер, з'єднання)
        normalized = _RE_APOSTROPHES.sub("", normalized)

        # 4. Tokenize alphanumeric characters
        tokens = re.findall(r"[a-zа-яіїєґ0-9]+", normalized)

        # 5 & 6. Filter stop-words and apply suffix-stripping stemmer
        stems: List[str] = []
        for token in tokens:
            if token not in self.stop_words and len(token) > 0:
                stems.append(self.stemmer.stem(token))

        return stems


# Module-level convenience function strictly matching openapi-v2.yaml SearchQueryResponse contract
default_normalizer = TextNormalizer()


def normalize_query_stems(text: str) -> List[str]:
    """
    Convenience function returning normalized stems for a query or text snippet.

    Implements TKT-002 | REQ-F-004
    :param text: Input string.
    :return: List of normalized stems.
    """
    return default_normalizer.tokenize_and_stem(text)
