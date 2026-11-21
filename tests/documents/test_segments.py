from jerome.documents.segments import split_sentences


def test_english_sentences():
    assert split_sentences("Mr. Smith left. He was late.", "en") == [(0, 15), (16, 28)]


def test_german_sentences_with_abbreviation():
    assert len(split_sentences("Das ist z. B. gut. Ja.", "de")) == 2
