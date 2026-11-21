from dataclasses import replace

from jerome.proofread.consistency import consistency_candidates
from jerome.types import DEFAULT_QUOTES, BlockText, Category, Severity, Source, StyleSheet

STYLE_EN = StyleSheet("en-US", DEFAULT_QUOTES["en-US"], {}, [])
STYLE_DE = StyleSheet("de-DE", DEFAULT_QUOTES["de-DE"], {}, [])


def test_hyphen_variant_minority_flagged():
    blocks = [BlockText(i, "Send an e-mail.") for i in range(4)] + [BlockText(9, "Send an email.")]
    c = consistency_candidates(blocks, STYLE_EN)
    assert [(x.block_id, x.original, x.replacement, x.severity) for x in c] == [
        (9, "email", "e-mail", Severity.MEDIUM)
    ]
    assert c[0].source == Source.CONSISTENCY
    assert c[0].category == Category.CONSISTENCY
    assert "4" in c[0].explanation and "1" in c[0].explanation


def test_sentence_initial_capital_is_not_a_case_variant():
    assert consistency_candidates([BlockText(1, "Spring came. It was spring.")] * 3, STYLE_EN) == []


def test_sentence_initial_minority_gets_capitalised_replacement():
    blocks = [
        BlockText(1, "We like e-mail. E-mail is fine. Email is bad. Use e-mail."),
        BlockText(2, "Send e-mail. Or e-mail."),
    ]
    c = consistency_candidates(blocks, STYLE_EN)
    assert [(x.original, x.replacement) for x in c] == [("Email", "E-mail")]


def test_tie_flags_nothing():
    blocks = [BlockText(1, "An e-mail."), BlockText(2, "An email.")]
    assert consistency_candidates(blocks, STYLE_EN) == []


def test_two_word_variant_when_joined_form_exists():
    blocks = [
        BlockText(1, "An e-mail. Another e-mail. More e-mail, and e-mail. Send an e mail now.")
    ]
    c = consistency_candidates(blocks, STYLE_EN)
    assert [(x.original, x.replacement) for x in c] == [("e mail", "e-mail")]
    text = blocks[0].text
    assert text[c[0].start : c[0].end] == "e mail"


def test_two_word_sequence_ignored_without_joined_token():
    assert consistency_candidates([BlockText(1, "Take a note. Take a no te.")], STYLE_EN) == []
    assert consistency_candidates([BlockText(1, "Sit in the sun room. Sun room.")], STYLE_EN) == []


def test_house_spelling_flagged():
    style = replace(STYLE_EN, house_spellings={"OK": ["okay", "O.K."]})
    assert consistency_candidates([BlockText(1, "It was okay.")], style)[0].replacement == "OK"


def test_house_spelling_with_dots_and_whole_word_only():
    style = replace(STYLE_EN, house_spellings={"OK": ["okay", "O.K."]})
    c = consistency_candidates([BlockText(1, "Say O.K. or okays, not okay.")], style)
    assert [(x.original, x.replacement) for x in c] == [("O.K.", "OK"), ("okay", "OK")]
    assert all(x.severity == Severity.MEDIUM and x.category == Category.CONSISTENCY for x in c)


def test_wrong_quotes_for_de_DE():
    c = consistency_candidates([BlockText(1, 'Er sagte "Hallo".')], STYLE_DE)
    assert {(x.original, x.replacement) for x in c} == {('"', "„"), ('"', "“")}
    assert all(x.category == Category.PUNCTUATION and x.severity == Severity.MEDIUM for x in c)


def test_en_gb_straight_quotes_use_double_pair():
    style = StyleSheet("en-GB", DEFAULT_QUOTES["en-GB"], {}, [])
    c = consistency_candidates([BlockText(1, 'He said "hi".')], style)
    assert [(x.original, x.replacement) for x in c] == [('"', "“"), ('"', "”")]


def test_de_ch_uses_guillemets():
    style = StyleSheet("de-CH", DEFAULT_QUOTES["de-CH"], {}, [])
    c = consistency_candidates([BlockText(1, 'Er sagte "Hallo".')], style)
    assert [(x.original, x.replacement) for x in c] == [('"', "«"), ('"', "»")]


def test_opening_quote_after_bracket_and_at_start():
    c = consistency_candidates([BlockText(1, '"A" ("b")')], STYLE_EN)
    assert [x.replacement for x in c] == ["“", "”", "“", "”"]


def test_correct_quotes_single_quotes_and_apostrophes_not_flagged():
    text = "“Fine,” he said. It’s John's ‘book’ and 'that'."
    assert consistency_candidates([BlockText(1, text)], STYLE_EN) == []
    assert consistency_candidates([BlockText(1, "Das ist Toms ’Buch’ und 'so'.")], STYLE_DE) == []


def test_near_duplicate_names():
    blocks = [BlockText(i, "Then Katharina laughed.") for i in range(5)] + [
        BlockText(9, "Then Katarina laughed.")
    ]
    (x,) = consistency_candidates(blocks, STYLE_EN)
    assert (x.original, x.replacement, x.severity) == ("Katarina", "Katharina", Severity.LOW)
    assert "5" in x.explanation and "1" in x.explanation


def test_names_seen_fewer_than_3_times_ignored():
    blocks = [BlockText(1, "Then Katharina laughed."), BlockText(2, "Then Katarina laughed.")]
    assert consistency_candidates(blocks, STYLE_EN) == []


def test_ignore_words_exempt_names():
    blocks = [BlockText(i, "Then Katharina laughed.") for i in range(5)] + [
        BlockText(9, "Then Katarina laughed.")
    ]
    assert consistency_candidates(blocks, replace(STYLE_EN, ignore_words=["Katarina"])) == []


def test_ignore_words_exempt_spelling_variants():
    blocks = [BlockText(i, "Send an e-mail.") for i in range(3)] + [BlockText(9, "Send an email.")]
    assert consistency_candidates(blocks, replace(STYLE_EN, ignore_words=["email"])) == []


def test_names_ignore_sentence_initial_capitals():
    blocks = [BlockText(i, "Then Katharina laughed.") for i in range(5)] + [
        BlockText(9, "Katarina laughed.")
    ]
    assert consistency_candidates(blocks, STYLE_EN) == []


def test_output_sorted_by_block_and_start():
    style = replace(STYLE_EN, house_spellings={"OK": ["okay"]})
    blocks = [BlockText(2, 'okay "x"'), BlockText(1, 'a "y" okay')]
    c = consistency_candidates(blocks, style)
    keys = [(x.block_id, x.start) for x in c]
    assert keys == sorted(keys)


def test_german_capitalised_noun_initial_and_midsentence_not_flagged():
    blocks = [BlockText(i, "Haus und Hof. Das ist ein Haus.") for i in range(3)]
    blocks += [BlockText(9, "Haus. Wir sahen das Haus.")]
    assert consistency_candidates(blocks, STYLE_DE) == []


def test_english_proper_name_initial_and_midsentence_not_flagged():
    blocks = [BlockText(1, "Paris is big. We saw Paris. Paris again. Then Paris fell.")]
    assert consistency_candidates(blocks, STYLE_EN) == []


def test_sentence_initial_minority_hyphen_variant_flagged_with_capital():
    blocks = [BlockText(1, "Send an email. Use email. E-mail is old.")]
    blocks += [BlockText(2, "Use email."), BlockText(3, "Use email.")]
    c = consistency_candidates(blocks, STYLE_EN)
    assert [(x.original, x.replacement) for x in c] == [("E-mail", "Email")]


def test_name_check_is_english_only():
    blocks = [BlockText(i, "Dann kam Hand heran.") for i in range(5)] + [
        BlockText(9, "Dann kam Hans heran.")
    ]
    assert consistency_candidates(blocks, STYLE_DE) == []


def _many(text, n):
    return [BlockText(i, text) for i in range(n)]


def test_all_caps_tokens_are_not_variants():
    blocks = _many("Of the house.", 5) + [BlockText(9, "THE FALL OF THE HOUSE OF USHER")]
    assert consistency_candidates(blocks, STYLE_EN) == []


def test_german_case_only_variants_are_dropped():
    blocks = _many("Dann sah ich Sie dort stehen.", 9) + [BlockText(99, "Dann sah ich sie dort.")]
    assert consistency_candidates(blocks, STYLE_DE) == []
    # English still flags a case variant that is clearly in the minority
    en = _many("Visit the Internet cafe.", 4) + [BlockText(99, "Visit the internet cafe.")]
    assert [(x.original, x.replacement) for x in consistency_candidates(en, STYLE_EN)] == [
        ("internet", "Internet")
    ]


def test_space_only_variants_are_dropped_but_hyphen_variants_kept():
    split = _many("He came along nicely.", 6) + [BlockText(99, "He came a long nicely.")]
    assert consistency_candidates(split, STYLE_EN) == []
    german = _many("Das war nachdem es regnete.", 6) + [
        BlockText(99, "Das war nach dem es regnete.")
    ]
    assert consistency_candidates(german, STYLE_DE) == []
    hyphen = _many("See you to-morrow.", 4) + [
        BlockText(98, "See you tomorrow."),
        BlockText(99, "See you to morrow."),
    ]
    got = {(x.original, x.replacement) for x in consistency_candidates(hyphen, STYLE_EN)}
    assert got == {("tomorrow", "to-morrow"), ("to morrow", "to-morrow")}


def test_minority_needs_a_dominant_form_that_is_common_and_four_times_larger():
    three_to_one = _many("Send an e-mail.", 3) + [BlockText(9, "Send an email.")]
    assert consistency_candidates(three_to_one, STYLE_EN) == []
    two_to_zero = _many("Send an e-mail.", 2) + [BlockText(9, "Send an email.")]
    assert consistency_candidates(two_to_zero, STYLE_EN) == []
    four_to_one = _many("Send an e-mail.", 4) + [BlockText(9, "Send an email.")]
    assert len(consistency_candidates(four_to_one, STYLE_EN)) == 1
    five_to_two = _many("Send an e-mail.", 5) + _many("Send an email.", 2)
    assert consistency_candidates(five_to_two, STYLE_EN) == []
