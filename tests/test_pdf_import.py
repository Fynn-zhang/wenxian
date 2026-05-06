from app.pdf_import import split_paragraphs


def test_split_paragraphs_keeps_meaningful_blocks():
    text = (
        "This is the first paragraph with enough scientific context to keep together.\n\n"
        "Short label\n"
        "This second paragraph continues the method description with details."
    )

    paragraphs = split_paragraphs(text)

    assert len(paragraphs) == 2
    assert paragraphs[0].startswith("This is the first paragraph")
    assert "Short label" in paragraphs[1]
