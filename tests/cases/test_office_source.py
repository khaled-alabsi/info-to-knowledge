"""The office workflow's own rules: a Word or OpenDocument file is a document of its own
kind, its markdown is cut into whole blocks, and its table of contents is removed in each shape pandoc writes it."""

from pathlib import Path

from support import harness

from services import inbox_service
from services import office_source_service
from services import run_tags_service


def test_a_word_or_opendocument_file_is_an_office_document_tagged_from_office():
    harness.check("both suffixes are office files",
                  office_source_service.is_office(Path("guide.docx")) and office_source_service.is_office(Path("a.ODT")), "")
    harness.check("a PDF is not", not office_source_service.is_office(Path("guide.pdf")), "")
    harness.check("each is a document with no time", inbox_service.is_document(Path("guide.docx"))
                  and inbox_service.is_document(Path("guide.pdf")) and not inbox_service.is_document(Path("talk.mp4")), "")
    harness.check("its file type and format tag say office",
                  inbox_service.describe_file_type(Path("guide.odt")) == "office"
                  and run_tags_service.describe_format_tag(Path("guide.docx")) == "from-office"
                  and run_tags_service.is_format_tag("from-office"), "")


def test_the_table_of_contents_is_removed_in_each_shape_pandoc_writes_it():
    markdown = "\n\n".join([
        "Table of Contents",
        "[1 Introduction 9](#introduction)\n\n[1.1 Scope 9](#scope)",
        "[Foreword [xv](#foreword)](#foreword)",
        "**[2 Gateway](#gateway)** [14](#gateway)",
        "> [2.1 Shards](#shards) [15](#shards)",
        "# Introduction",
        "See [the gateway section](#gateway) for the shard rules.",
        "[AcmeSync home](https://acmesync.example) is the product page.",
    ])
    removed: list[dict] = []
    kept = office_source_service._remove_contents(office_source_service._split_blocks(markdown), removed)
    harness.check("only the heading and the text are left",
                  kept == ["# Introduction", "See [the gateway section](#gateway) for the shard rules.",
                           "[AcmeSync home](https://acmesync.example) is the product page."], str(kept))
    harness.check("each removal is named as the table of contents",
                  len(removed) == 6 and {entry["rule"] for entry in removed} == {"table of contents"}, str(removed))


def test_a_sentence_about_an_html_table_tag_does_not_glue_the_blocks_after_it_defect():
    # Defect: "\<table\>" in a sentence counted as an HTML table's start, and the rest of a long document became one block.
    # Fix: only a real tag opens an HTML table
    markdown = ("A table is like the HTML \\<table\\> tag.\n\n# Next\n\nText.\n\n<table>\n<tr><td>a</td></tr>\n\n"
                "<tr><td>b</td></tr>\n</table>\n\nAfter.")
    blocks = office_source_service._split_blocks(markdown)
    harness.check("each heading, paragraph and table is a block, a table whole across its blank line",
                  len(blocks) == 5 and blocks[1] == "# Next" and blocks[3].startswith("<table>")
                  and blocks[3].endswith("</table>"), str(blocks))


def test_a_code_block_with_a_blank_line_stays_one_block():
    blocks = office_source_service._split_blocks("```\nline one\n\nline two\n```\n\nAfter.")
    harness.check("the fence is one block", blocks == ["```\nline one\n\nline two\n```", "After."], str(blocks))


def test_a_picture_is_taken_out_of_its_block_and_listed():
    text, paths = office_source_service._take_pictures('Before ![a](/run/01_office-media/media/image1.png) after '
                                                      '<img src="/run/01_office-media/media/image2.png" />')
    harness.check("both pictures, in order, and the words kept",
                  paths == ["/run/01_office-media/media/image1.png", "/run/01_office-media/media/image2.png"]
                  and text == "Before  after", f"{paths} {text!r}")
