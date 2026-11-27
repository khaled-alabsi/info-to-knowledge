"""What layer 3's text may not contain, found by a program. Pure functions over a string.

REQUIREMENTS.md R3: the facts file holds no timestamp, no frame number, no meta-narration and no
claim said twice. The banned phrases and the timestamp pattern are stated once, in the output
contract, and read from there.
"""

from __future__ import annotations

import re
from typing import Any

from models import output_contract

__all__ = ["find_unread_brackets", "find_lists_restating_prose", "join_list_blocks", "list_cited_ids", "is_answered_question", "remove_figure_descriptions", "is_decision_note", "find_narrated_sentences",
           "find_copied_speech", "find_bare_topic_ids", "ENTRY_ID_PATTERN", "TOPIC_ID_PATTERN", "CUT_ID_PATTERN", "build_cut_id", "read_cut_number", "FIGURE_DESCRIPTION_MARK", "FIGURE_DESCRIPTION_START", "FIGURE_CAPTION_START",
           "FIGURE_MENTION", "render_figure_mention", "ID_SEPARATOR", "describe_unread_items",
           "expand_cross_frame_ranges", "find_narration_words", "write_short_ranges_in_full", "split_ids_after_fence",
           "find_banned_phrases", "find_timestamps", "find_entry_ids", "move_bracketed_entry_ids",
           "remove_bracketed_entry_ids", "expand_line_ranges", "find_repeated_sentences", "find_sentences_said_earlier", "remove_sections",
           "build_heading_anchors", "hide_secret_query_values", "hide_claim_secrets", "find_topic_links", "remove_topic_links",
           "render_topic_links", "find_empty_list_items", "list_code_blocks", "is_code_block_held", "render_heading_links", "is_secret_shown", "is_secret_named_strongly",
           "find_readable_secrets", "hide_secrets_in_record", "hide_secret_tokens",
           "rewrite_marked_line_ids_in_brackets", "find_credential_keys", "is_label_without_value",
           "has_credential_label", "is_credential_label", "is_auth_scheme", "PERSONAL_EMAIL"]

# What joins the ids a range across frames is written out as; a reader splits them on it
ID_SEPARATOR = ", "

# A person's e-mail address, as the text reader reads it on a cut; never after "/" or ":", where it is a web address's
# host with a 0 read as @ (2026-10-06: a server's host was refused as a person's address)
# An address preceded by "@" is a social handle, "@<name>@<server>", which its owner publishes on purpose: a talk's
# closing slide gave the speaker's, and refusing it left the reader no way to reach them (worker report, 2026-10-13)
# "git@<host>" is the user every git host serves its remotes as, never a person (2026-10-21: a chapter quoting a
# repository's SSH remote was refused as a person's address)
# A team's or a function's mailbox ("platform-team@", "support@", "info@") names no person: a deck's closing slide gave
# its team's address as the rollout's contact, and refusing it left the document no way to reach anyone (pdf-03)
_ROLE_MAILBOX = (r"(?![\w.+-]*(?:team|support|info|help|contact|service|sales|office|security|admin|noreply|no-reply|"
                 r"hello|feedback|press|billing|ops)(?:[.+-][\w.+-]*)?@)")
PERSONAL_EMAIL = re.compile(r"(?<![\w.+/:@-])(?!git@)" + _ROLE_MAILBOX + r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

_TIMESTAMP = re.compile(output_contract.DOCUMENT_TIMESTAMP_PATTERN)
_ISO_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?")
# A date written day first ("25.11.2025 14:30", a file's change time on screen) holds its clock time as data too
# A date written with its month's name ("October 18th at 23:30", "18 October, 23:30") holds its clock time too: a maintenance
# window was refused as recording positions (2026-10-05)
_MONTH = (r"(?:jan(?:uary|uar)?|feb(?:ruary|ruar)?|m(?:ar(?:ch)?|ärz)|apr(?:il)?|ma[iy]|jun[ei]?|jul[iy]?|aug(?:ust)?|"
          r"sep(?:t(?:ember)?)?|o[ck]t(?:ober)?|nov(?:ember)?|de[cz](?:ember)?)\.?")
_DATED_CLOCK_PREFIX = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.(?:\d{2,4})?"
                                 rf"|{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?|\d{{1,2}}(?:st|nd|rd|th)?\.?\s+{_MONTH}(?:\s+\d{{4}})?)"
                                 r",?(?:\s+(?:at|um|ab|-|–))?\s+$", re.IGNORECASE)
# A time of day the meeting names ("2:00 AM to 5:15 AM", "14:00 Uhr") is the subject's, not a place in the recording
# A time zone after a clock time ("17:03 UTC") marks a time of day as surely as AM or PM does
_TIME_OF_DAY_SUFFIX = re.compile(r"^\s*(?:[ap]\.?\s?m\.?|o'?clock|uhr|h|utc|gmt|cet|cest|mez|mesz)\b", re.IGNORECASE)
# A capital letter and its period alone is an initial, never a sentence's end: "John T. Jackalope." was cut after "T.",
# and the lone "Jackalope." left matched a spoken sentence cut the same way, refused as copied speech (DEFECTS 228)
_SENTENCE_END = re.compile(r"(?<=[.!?])(?<!\b[A-Z]\.)\s+")
# REQUIREMENTS.md R12: a section points to another topic by its id in brackets, `[T08.3]`.
_TOPIC_LINK = re.compile(r"\[(T\d{2,}\.\d+)\](?!\()")
# Several topic ids in one bracket, "[T03.1, T05.2]", written the way entry ids are listed: release test 3's executive
# summary showed three of them to the reader as written, since only a bracket with one id was a link (DEFECTS 463)
_TOPIC_LIST = re.compile(r"\[\s*(T\d{2,}\.\d+(?:\s*,\s*T\d{2,}\.\d+)+)\s*\](?!\()")
_TOPIC_ID = re.compile(r"T\d{2,}\.\d+")
# A topic id in round brackets, the section's name in italics before it or not: "_Exempt payee code_ (T08.6)" reached a
# reader as written, the id shown (pdf-real-fw9)
_TOPIC_IN_PARENS = re.compile(r"(?:_([^_\n]+)_\s*)?\((T\d{2,}\.\d+)\)")
_HEADING = re.compile(r"^#{1,6} (.+?)\s*$", re.MULTILINE)
_MIN_SENTENCE_WORDS = 3
# A lead-in of at most this many words ending in a colon is no claim (DEFECTS 383); over the public runs' repeat findings the
# colon sentences that were whole claims held 20 words or more
_BARE_LEAD_IN_WORDS = 4
# The words phase 3 uses when a part of a frame shows a credential
_SECRET_WORDS = re.compile(r"\b(?:secret|password|credential|token|api key)s?\b", re.IGNORECASE)
# What a masked cut of a secret may not still show: a long value mixing letters and digits (a web address is none), or a
# credential's label, whose value follows it (2026-09-30: a mask over a tenant id cleared a cut whose authorization
# header, code and cookie stayed readable)
# An id in GUID form, whole or cut off at the cut's edge, names a secret or an app and is none (2026-09-30: a secrets
# table's "Secret ID" column was read as secret values)
# A GUID the text reader misread by a letter ("5" read as "S") keeps its 8-4-4-4-12 shape and is no secret either; the
# reader may print a dash as a long dash.
# A name built of plain words, digit groups and short parts joined by "-", "_", ".", "/" or "+" ("010042-AcmeSyncApp-DEV",
# "application/vnd.acme.v2+json") is no secret: a random value mixes letters and digits inside one long part.
# A secret value is made of token characters only: text holding quotes, brackets, colons or "@" is data or a misread, such
# as a JSON header value (2026-09-30: a rate-limit header and correlation ids read as "@" were called secrets)
_DASH = "[-\u2013\u2014]"
_NAME_PART = r"(?:\d+|[A-Za-z]+|[A-Za-z0-9]{1,3})"
_LEFT_VALUE = re.compile(rf"^(?!/|https?:)(?!\W?[0-9a-fA-F]{{6,8}}(?:{_DASH}[0-9a-fA-F]{{1,12}}){{1,4}}\W*$)"
                         rf"(?!\W?[0-9A-Za-z]{{7,9}}(?:{_DASH}[0-9A-Za-z]{{3,5}}){{3}}{_DASH}[0-9A-Za-z]{{10,13}}\W*$)"
                         rf"(?!\W*{_NAME_PART}(?:[-_./+]{_NAME_PART})+\W*$)"
                         r"(?=\S*[A-Za-z])(?=\S*\d)[\"'(]?[A-Za-z0-9_\-+/=.~&%]{20,}[\"'),;.]*$")
_CREDENTIAL_LABEL = re.compile(r"^(?:authorization|cookie|set-cookie|password|client_secret|x-api-key|api-key):$|^bearer$",
                               re.IGNORECASE)
# A name=value pair whose name says it is a credential: "sessioncookie=…", "access_token=…"
_CREDENTIAL_PAIR = re.compile(r"^[\w.-]*(?:cookie|session|token|secret|password|apikey|api_key)[\w.-]*=", re.IGNORECASE)
# A key in a settings file whose name says its value is a credential, as the reader reads it: '"syncUserPassword":',
# '"clientSecret":' (2026-10-02: a configuration file's passwords passed because only their values were judged)
_CREDENTIAL_KIND = r"password|passwd|secret|apikey|api_key|accesstoken|access_token"
_CREDENTIAL_KEY = re.compile(rf"^[\w.-]*(?:{_CREDENTIAL_KIND})[\w.-]*:,?$", re.IGNORECASE)
_QUOTES = re.compile("[\"'`\u201c\u201d\u2018\u2019]")
# The marks the text reader keeps around a word: brackets, stars and quotes, curly ones too (2026-10-06: a debugger's
# '\u201cCookie:' header passed every secret check, as only straight quotes were stripped)
_WORD_MARKS = "[](){}<>*'\"`\u201c\u201d\u2018\u2019"
# The scheme word an authorization header puts between its label and its value
_AUTH_SCHEMES = {"basic", "bearer", "digest"}
# "the segment" bans the recording's own word for a part of itself. A customer segment is the subject's word, and a
# writer refused for it rewrote the meeting's own term away (2026-10-07), so the ban passes over the uses where
# another word of the subject stands beside it.
_SEGMENT_OF_THE_SUBJECT = re.compile(r"\b(?:customer|client|business|market|private|corporate|retail)\s+segments?\b"
                                     r"|\bsegments?[- ](?:based|specific|wise)\b", re.IGNORECASE)
# "frame" bans the recording's still. A stack frame or a data frame is the subject's term, and a label quoted from a
# tool's screen ("Global frame" in a code visualiser) is its own words; a writer refused for it renamed the label
# (2026-10-12), so the ban passes over those uses and over every code span and double-quoted text. "A time frame" and
# "a frame of reference" are English idioms, never the recording's still (a framework's own "time frames").
_FRAME_OF_THE_SUBJECT = re.compile(r"\b(?:stack|global|call|data|key|time)\s+frames?\b|\bframes?\s+of\s+reference\b"
                                   # a camera's or a detector's speed: "4.5 million frames per second" (a 39c3 talk)
                                   r"|\bframes?\s+(?:per|a)\s+second\b|\bframe\s+rates?\b"
                                   # a network protocol's frame: "frame sequence numbers" of a radio network (froscon 3211)
                                   r"|\b(?:ethernet|radio|network|packet|link|can|setup|control|ack|beacon|broadcast|handshake|request|response|reply)\s+frames?\b"
                                   r"|\bframes?[- ](?:sequence|header|check|counter|format|length|size|type|loss|delimiter)s?\b"
                                   r"|`[^`]*`|\"[^\"]*\"", re.IGNORECASE)
# A banned phrase is a whole word, with its plural: "frame" and "frames" are the recording's, "framework" and
# "iframe" are the subject's.
_BANNED = [(phrase, re.compile(rf"\b{re.escape(phrase)}s?\b", re.IGNORECASE))
           for phrase in output_contract.DOCUMENT_BANNED_PHRASES]


# A frame's note `F154.2`, a transcript line `S0415`, a prepared note of an audio recording `P2.3.1`
ENTRY_ID_PATTERN = r"(?:F\d+\.\d+|S\d{4,}|P\d+\.\d+\.\d+)"
_ENTRY_ID = re.compile(rf"\b{ENTRY_ID_PATTERN}\b")
# An article's number in a citation, `10.1016/S0959-4388(02)00301-1` or `S1473550418000423`, only looks like a
# transcript line: written after a slash, before a hyphen and a digit, or with more than 6 digits (a PDF's references,
# 2026-10-23)
_ARTICLE_NUMBER = re.compile(r"(?<=/)S\d+|S\d+(?=-\d)|S\d{7,}")
# `F1.4-F1.8` is the notes 4 to 8 of frame 1: a range inside brackets cites each note in it (the run of 2026-09-29 lost
# 18 citations because a chapter wrote `[F0.1-F0.4]`)
# `S0115-S0118` is the transcript lines 115 to 118, each cited (a chapter of the same run wrote it for a run of speech)
_RANGE = r"F\d+\.\d+\s*-\s*F\d+\.\d+|S\d{4,}\s*-\s*S\d{4,}"
# A range of notes is one frame's: a bracket holding `F40.1-F42.3`, across frames, is never read, since which notes lie
# between is not in the bracket; reading only its two ends dropped the notes between without a word (2026-09-29)
_CROSS_FRAME_RANGE = re.compile(r"F(\d+)\.\d+\s*-\s*F(?!\1\.)\d+\.\d+")
_CROSS_FRAME_ITEM = re.compile(r"\b(F\d+\.\d+)\s*-\s*(F\d+\.\d+)\b")
_ID_RANGE_ITEM = re.compile(r"\b(F\d+\.\d+|S\d{4,})\s*-\s*(F\d+\.\d+|S\d{4,})\b")
_CITED = re.compile(rf"F(?P<frame>\d+)\.(?P<first>\d+)\s*-\s*F(?P<last_frame>\d+)\.(?P<last>\d+)"
                    rf"|S(?P<first_line>\d{{4,}})\s*-\s*S(?P<last_line>\d{{4,}})|{ENTRY_ID_PATTERN}")
# A cut named beside the notes, `[F6.4, cut-003]`, is read and cites nothing: the frame is placed by its own image line.
# All three answers of the run of 2026-09-29's first chapter were rejected over brackets like it that the program did not read.
CUT_ID_PATTERN = r"cut-\d+"
# A step of one note, `F2.7-3` (step 3 of the diagram note F2.7), cites that note; a heading before a note, `T01.1-F1.4`,
# cites the note. A chapter of the same run was blocked three times over both shapes, which say exactly one note each.
_STEP = r"F\d+\.\d+-\d+\b"
# A heading of the table of contents `T02.1`, as a heading line and a prefixed range write it
TOPIC_ID_PATTERN = r"T\d+\.\d+"
_PREFIXED = rf"{TOPIC_ID_PATTERN}-{ENTRY_ID_PATTERN}"
_BRACKET_ITEM = rf"(?:{_RANGE}|{_STEP}|{_PREFIXED}|{ENTRY_ID_PATTERN}|{CUT_ID_PATTERN})"
_BRACKET_BODY = rf"\[\s*{_BRACKET_ITEM}(?:\s*,\s*{_BRACKET_ITEM})*\s*\]"
# Backticks go with the bracket only when they wrap it on both sides: one optional backtick on each side took the closing
# backtick of a code span written right before the bracket, "`host.example.com` [F5.1]", and the published text's code
# ran on to the next backtick (2026-10-01)
# A bracket written doubled, "[[S0036]]", is one bracket: its inner bracket alone was taken out, and a published
# document showed "[]" 185 times (wiki run 3)
_ONE_BRACKET = rf"(?:`\s*{_BRACKET_BODY}\s*`|\[{_BRACKET_BODY}\]|{_BRACKET_BODY})"
# Several brackets joined by commas, "[F20.2], [S0634]", go as one: taken out one by one, they left ",." behind
# (2026-10-01: five lines of an executive summary)
_BRACKETED_IDS = re.compile(rf"\s*{_ONE_BRACKET}(?:\s*,\s*{_ONE_BRACKET})*")
# A bracket left in the text that still names a frame or a transcript line, `[F3]` or `[F3, S0061]`: meant as sources,
# and not read. A topic link `[T08.3]` and a web link `[the guide](...)` are not it.
_UNREAD_BRACKET = re.compile(r"\[[^\[\]\n]*\b(?:F\d+|S\d{4,}|P\d+\.\d+\.\d+)\b[^\[\]\n]*\](?!\()")
# A range of one frame's notes written short, `F82.1-82.3`: the second end without its `F`. Its meaning is plain, and
# a chapter was refused for it as an unreadable bracket
_SHORT_RANGE = re.compile(r"\bF(\d+)\.(\d+)\s*-\s*(\d+)\.(\d+)\b")
# The line after a code block's closing fence that holds its ids and then goes on with text, "[F66.3] This script …":
# the ids stay on the line, the text starts a paragraph of its own. A chapter was refused because the diagram's ids
# were not alone on their line.
_IDS_THEN_TEXT_AFTER_FENCE = re.compile(r"(^```[ \t]*\n)(\[[^\[\]\n]+\])[ \t]+(\S[^\n]*)", re.MULTILINE)
# A marked line ("> **Set aside:** ...") ending with its ids in round brackets, a full stop after them or not
_MARKED_LINE_ROUND_IDS = re.compile(r"^(>\s*\*\*[^*\n]+:\*\*[^\n]*?)\s*\(((?:(?:P\d+\.\d+\.\d+|[FS]\d+(?:\.\d+)?(?:-[FS]?\d+(?:\.\d+)?)?)"
                                    r"(?:,\s*)?)+)\)\.?[ \t]*$", re.MULTILINE)
# A range written with a typographic dash, `F13.9–F13.12`: the same range, and a chapter was refused for it as an
# unreadable bracket (2026-10-06). Only between two ids inside a bracket of ids, so prose dates keep their dash
# Two ids parted by spaces alone, "[F14.1 S0200]": a worker refused for semicolons planned to part them so (DEFECTS 233)
_SPACE_BETWEEN_IDS = re.compile(r"(?<=\d)\s+(?=(?:F\d+\.\d+|S\d{4,}|P\d+\.\d+\.\d+)\b)")
_ID_BRACKET = re.compile(r"\[[^\[\]\n]*\b(?:F\d+\.\d+|S\d{4,}|P\d+\.\d+\.\d+)\b[^\[\]\n]*\](?!\()")
_DASH_BETWEEN_IDS = re.compile(r"(?<=\d)\s*[\u2010-\u2015\u2212]\s*(?=[FS]?\d)")
# An item the bracket grammar reads, the one grammar `_BRACKET_ITEM` states
_READ_ITEM = re.compile(_BRACKET_ITEM)
# A frame's line, `![alt](cut-NN "caption")` or its rendered `![alt](path)`, and the caption line rendered under it, `*caption*`
_FIGURE_LINE = re.compile(r"^(?:!\[[^\]]*\]\([^)]*\)|\*[^*\s][^*]*\*)$")
_NUMBER = re.compile(r"\d(?:[\d,.]*\d)?")
_MIN_REPEATED_NUMBERS = 3
# The line under a figure's caption that opens its description, as a chapter writes it and the document renders it,
# and the pattern every reader finds it by (the space after `>` may be missing)
FIGURE_DESCRIPTION_LABEL = "**What the figure shows:**"
FIGURE_DESCRIPTION_MARK = f"> {FIGURE_DESCRIPTION_LABEL}"
FIGURE_DESCRIPTION_START = r">\s*" + re.escape(FIGURE_DESCRIPTION_LABEL)
_WHOLE_FIGURE_DESCRIPTION_LINE = re.compile(rf"^{FIGURE_DESCRIPTION_START}.*$", re.MULTILINE)
# A figure's caption line opens with this, its number after it: `*Figure 3. The login page*`
FIGURE_CAPTION_START = "*Figure "
# "Figure 3" anywhere: a caption or a sentence that points to the figure
FIGURE_MENTION = re.compile(r"\bFigure (\d+)\b")
_FIGURE_DESCRIPTION_LINE = re.compile(rf"^{FIGURE_DESCRIPTION_START}")
_ANCHOR_DROPPED = re.compile(r"[^\w\- ]")
# A query or fragment parameter whose NAME is one of these carries a secret. Matched as the whole name, so `keyboard`
# is not a `key`. REQUIREMENTS.md R8: layer 2 keeps a web address whole; the document never shows the value.
_SECRET_PARAMETERS = frozenset({"code", "token", "access_token", "id_token", "refresh_token", "client_secret", "secret",
                                "password", "passwd", "pwd", "key", "api_key", "apikey", "assertion", "samlresponse",
                                "sig", "signature", "session", "sessionid", "jwt", "auth", "authorization"})
# A secret-named parameter after `?`, `&`, `#` or an escaped `&amp;`, in any web address, with or without its scheme.
# The value stops at the next separator, and a sentence's closing punctuation after it is left outside.
_SECRET_PARAMETER = re.compile(r"(?P<lead>[?&#]|&amp;)(?P<name>[A-Za-z_][A-Za-z0-9_]*)="
                               r"(?P<value>[^&#\s)\]\"'<>]+?)(?=[.,;:!?]*(?:[&#\s)\]\"'<>]|$))")
_MASK = "…"
# How every other secret value is written once hidden
_SECRET_MASK = "[secret]"
# A generated secret: 30 or more characters mixing upper and lower case, digits and one of ~ . _. An id (a GUID is
# lowercase hex and hyphens), a word or a web address never mixes all four.
_SECRET_TOKEN = re.compile(r"(?<![A-Za-z0-9~._-])(?=[A-Za-z0-9~._-]*[A-Z])(?=[A-Za-z0-9~._-]*[a-z])"
                           r"(?=[A-Za-z0-9~._-]*[0-9])(?=[A-Za-z0-9~._-]*[~._])[A-Za-z0-9~._-]{30,}(?![A-Za-z0-9~._-])")
# A token segment: base64 of a JSON object always begins "eyJ" ('{"'), so a long run of it is a token, dot or not.
_TOKEN_SEGMENT = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{20,}(?:\.[A-Za-z0-9_-]*)*")
# A word inside a code name: a capital and lower case letters, or capitals alone, split at digits and separators
_NAME_PIECE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")
_FOUR_CONSONANTS = re.compile(r"[^aeiouy]{4}", re.IGNORECASE)
# A match this much made of words is a code name (`test_Refund3Parts_whenLimitReached`), never a generated value:
# measured, words make 0.55 to 0.83 of code names and at most 0.4 of 99.6% of random 30 to 60 character values
_NAME_WORD_SHARE = 0.5
# A name joined from parts by `_`, `.` or `-`, each part one or two runs of a case (`STG_ORDER_TAB.UNITfaktor_SATZ`,
# `202609_acmeKR_Systeme_Uebersicht`), is a code or file name whatever its words: measured, 1 of 172,980 random 30 to 60
# character values has three such parts and no other (2026-10-09: a column name and a workbook's name were hidden)
_NAME_MIN_PARTS = 3
_NAME_PART_CASE_RUNS = 2
_NAME_PART_SEPARATOR = re.compile(r"[._-]")
_CASE_RUN = re.compile(r"[a-z]+|[A-Z]+")
# A value written beside a name that says it is a credential, in the shapes an outline writes a pair: `NAME` / `value`,
# field "Name" = "value", NAME: value, NAME=value (2026-10-05: an environment table's short password was copied into a
# frame's notes; it is no generated token, so only its name tells it apart)
# A name in quotes is a label, and a colon after it starts the outline's description of the part, so only `=` gives it a
# value there: `button "Add from ConfigMap or Secret": below` keeps its description
_SECRET_KIND = rf"(?:{_CREDENTIAL_KIND}|client[_ ]?key|private[_ ]?key)"
# A bare name starts a word: in "ConfigMap/Secret: Select a resource" the word after the slash is a label's end. A
# table row written `Name "X", Value "v"` gives its value after ", Value"
_VALUE_AFTER = r"|,\s*value\s*[:=]?\s*"
_NAMED_SECRET = re.compile(rf"(?:(?<![\w/.-])(?P<name>`?[\w.-]*{_SECRET_KIND}[\w.-]*`?)"
                           rf"(?P<separator>\s*(?:/|=|:|→|->)\s*{_VALUE_AFTER})"
                           rf"|(?P<label>\"[^\"\n]*{_SECRET_KIND}[^\"\n]*\")(?P<equals>\s*=\s*{_VALUE_AFTER}))"
                           r"(?P<value>`[^`\n]+`|\"[^\"\n]+\"|'[^'\n]+'|[^\s,;`\"']+)", re.IGNORECASE)
# What is no value: an empty field shown as dots or stars, or a value already hidden
_NO_VALUE = re.compile(rf"^[`\"']?(?:[.•*·…]+|{re.escape(_SECRET_MASK)}|empty|\(none\))[`\"']?$", re.IGNORECASE)
# A part named as a credential, and the quoted value on a line under it: an outline writes a table row's name on one
# line and its value one level under it (2026-10-05: `- table row "<NAME>_SECRET":` then `- field "Value": "<value>"`)
_CREDENTIAL_PART = re.compile(rf"(?<![\w/.-])[\w.-]*{_SECRET_KIND}[\w.-]*", re.IGNORECASE)
_CHILD_VALUE = re.compile(r"(?P<lead>[:=]\s*)(?P<quote>[\"`'])(?P<value>[^\"`'\n]+)(?P=quote)")
# On a line that names a credential, whatever follows a "Value" label, in any quoting: `Value: v`, Value v, "Value" = "v"
# (2026-10-05: a row written `Name: <NAME>_SECRET`, `Value: <value>` passed the shapes above)
# A "value" joined to the word before it is no label: "Tag-Length-Value (TLV)" and the code `[value]` on a line saying
# "secret recovery" lost "(TLV)" and the code's "]" to the mask (DEFECTS 651)
_VALUE_LABEL = re.compile(r"(?P<lead>(?<![-\[(\w])value\b[\"`']?\s*(?:[:=]\s*)?[\"`']?)(?P<value>[^\"`'\n|,;]+?)(?P<end>\s*(?:[\"`'|,;]|$))",
                          re.IGNORECASE)
# A strong sign that a part shows a secret; "token" alone is weak: a page ABOUT tokens shows none (2026-09-30), and so is
# "credential": it names a flow ("client credentials flow"), a section or code ("filling credentials") as often as a
# value, and a cut of such code was held to a mask it could not have (2026-10-02)
_STRONG_SECRET_WORDS = re.compile(r"\b(?:secret|password|api key)s?\b", re.IGNORECASE)
# A secret's word naming a part of a form, not a value: "the login fields and password change prompt" described an empty
# sign-in form, and its cut was held to a mask over nothing (2026-10-03)
_FORM_PART_AFTER = re.compile(r"\s+(?:fields?|prompts?|change|rules?|polic(?:y|ies)|requirements?|complexity|box(?:es)?|"
                              r"inputs?|reset|forms?|labels?|entry)\b", re.IGNORECASE)
_VALUE_SHOWN = re.compile(r"\b(?:values?|typed|entered|filled in|visible|readable|in clear|plain text)\b", re.IGNORECASE)
# A command line's credential flag with its value in one word, `--token=<value>`; the reader reads the two dashes as one
# long dash. A value this long is a secret, never a placeholder like "generated" (2026-10-06: a login command's whole
# token was on a cut no check named)
_SECRET_VALUE_MIN_CHARS = 16
# A sentence about the recording instead of the subject: a person's action on the screen, or a note about the transcript
# itself. One published document narrated in 118 of its 709 sentences and wrote 17 about the transcript (2026-10-01).
_NARRATION = re.compile(r"\b(?:presenter|speaker)s?\b[^.!?\n]{0,40}?\b(?:hover|point|scroll|click|navigat|switch|zoom|paste|"
                        r"copie|copy|trail|paus|joke|remark|gestur|highlight|mention|refer|edit|comment|"
                        # the instruction's own "never write that something is shown, said, explained or presented"
                        r"explain|present|outline|describ|introduc|walk|show|said|says|stat|stress|emphasi)\w*",
                        re.IGNORECASE)
# The presenter or the speaker as the one acting in a sentence, whatever the verb ("The speaker concludes …", "accompanied
# by the speaker preparing …"): a verb list missed "completes", "concludes", "notes", "asks" and "detailed" in one
# document (2026-10-01). "The presenter's team" names a role's group and stays.
_SPEAKER_ACTS = re.compile(r"(?:^|[,;:]\s*|\bby\s+)the\s+(?:presenter|speaker)s?\b(?!['’]s)\s+(?:then\s+|also\s+)?[a-z]+",
                           re.IGNORECASE)
# The meeting's own course told as the subject: "The presentation moves to the third use case" reached a published
# chapter, since no presenter or speaker is named in it. Only the verbs of moving on count: how an agenda opens and
# ends ("the session concludes with questions") is what an agenda heading is about, and was refused as narration.
_COURSE_OF_MEETING = re.compile(r"\bthe\s+(?:presentation|session|talk|meeting|demo)\s+(?:then\s+|now\s+)?"
                                # "transitions" reached a chapter twice in one run (2026-10-04)
                                r"(?:(?:moves|turns|switches|shifts|proceeds|returns|goes|transitions)\s+(?:on\s+)?(?:to|back)\b"
                                # entering its next numbered part is moving on too (2026-10-04)
                                r"|enters\s+(?:section|part|chapter)\b)",
                                re.IGNORECASE)
# A sentence whose subject is the talk itself tells what the talk does, whatever its verb: over the published
# documents of 15 public runs, 28 sentences open "The presentation/talk/session/lecture/recitation <verb>" ("the
# lecture concludes after this summary", "the talk spans 30 minutes", "the presentation defines strict boundaries"),
# none needed by a reader as written; the moving-on verbs above were widened one by one (DEFECTS 204). "The meeting"
# stays out: a business meeting's decision is written "the meeting decided ...", and "the demonstration of" names a
# process
# A word after it that makes a compound noun of the subject's ("the presentation layer", "the session token",
# "the speech recognition") is no talk; "the speech explains that" is (DEFECTS 237)
_TALK_AS_SUBJECT = re.compile(r"^\W*the\s+(?:presentation|talk|session|lecture|recitation|speech)(?:'s)?\s+"
                              r"(?!(?:of|layer|tier|logic|token|id|key|cookie|state|storage|data|timeout|store|manager"
                              r"|handling|management|notes|hall|room|recognition|synthesis|bubble|signal|to)\b)\w+",
                              re.IGNORECASE)
# Someone giving the talk at an event tells who presented where, never what the talk teaches: "Michael presents a talk at
# FrosCon 19" reached an accepted chapter, its subject a name no rule could list (DEFECTS 229). "At" keeps a meeting's
# own task, "gives a presentation to the board on Friday", out
_TALK_GIVEN_AT = re.compile(r"\b(?:presents|presented|gives|gave|delivers|delivered)\s+(?:a|the|this|his|her|their)\s+"
                            r"(?:talk|presentation|lecture|keynote)\s+at\b", re.IGNORECASE)
_DECISION_NOTE = re.compile(r"^\W*(?:\d{4}\s+)?decision\W*:", re.IGNORECASE)
# A step the meeting has not reached yet is about the recording: four open points asking what a lecture would show next
# reached accepted chapters (2026-10-12). "Not yet shown to customers" is the subject's, so a shown or written that goes
# on to someone is left alone
_NOT_YET_SHOWN_PATTERN = (r"\bnot\s+(?:yet\s+)?(?:written\s+or\s+)?(?:shown|written|demonstrated|addressed|covered)\s+yet\b"
                          r"|\bnot\s+yet\s+(?:written\s+or\s+)?(?:shown|written|demonstrated|addressed|covered)\b(?!\s+to\b)"
                          r"|\bat\s+this\s+(?:stage|point)\s+of\s+the\s+(?:explanation|lecture|talk|presentation|meeting)\b"
                          # "not shown until the next step", "not shown in this section": the recording's own course
                          # in other words; "not shown until checkout" is the subject's and is left alone (2026-10-12)
                          r"|\bnot\s+(?:yet\s+)?(?:shown|written|demonstrated|addressed|covered)\s+(?:until\s+(?:the\s+)?"
                          r"next\s+(?:step|frame|slide|part|section)|in\s+this\s+(?:frame|section|step|moment|part))\b")
_NOT_YET_SHOWN = re.compile(_NOT_YET_SHOWN_PATTERN, re.IGNORECASE)
# Only the meeting's own transcript is meta: "transcribes" alone also names a subject (2026-10-03: a product whose
# pipeline transcribes client calls was refused as narration, and its worker reported the refusal wrong, rightly)
# A word that points at the recording's own transcript may stand between: "the list is not completed in the provided
# transcript" reached a published chapter (lecture-1, 2026-10-15); "the call transcript" stays a product's subject
_RECORDING_META = re.compile(r"\b(?:the\s+(?:(?:provided|given|available|automatic|spoken)\s+)?transcript\w*|in\s+the\s+transcription|transcribed\s+as|transcription\s+"
                             r"(?:error|garbl)\w*|mishear\w*|mispron\w*|cut off|visual alignment|the cursor|on (?:the|a) slide)\b"
                             # A sentence whose subject is the slide is about the recording: "The slide separates the
                             # logical steps from the screen they lead to" reached a chapter (2026-10-05); so did "A
                             # slide lists command examples" and "The title slide lists the presenter's details" (DEFECTS
                             # 250). "A slide deck" and "a slide rule" name things, not the recording's slide
                             r"|^\W*(?:the|a|an|this|one|another)\s+(?:\w+\s+)?slides?\b(?!\s+(?:deck|rule|show)s?\b)"
                             r"|" + _NOT_YET_SHOWN_PATTERN,
                             re.IGNORECASE)
_NOT_WORD_OR_SPACE = re.compile(r"[^\w ]")
# A sentence whose words lie this share or more in spoken runs of `min_words` words, the screen's own runs left out, is
# speech stitched in: a sentence joined from two spoken phrases, filler kept, passed when neither made up half of it
# (38c3 talk 797, DEFECTS 543). Over 1,458 sentences of public runs' accepted sections two reach 0.75, both such; the
# next, a German slide bullet the notes keep in English, 0.70
_SPOKEN_RUNS_SHARE = 0.75
_LIST_OR_TABLE_LINE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|\|)")
_BLOCK_START = re.compile(r"^\s*(?:!\[|>|[-*+]\s|\d+[.)]\s|\||#)")


def _normalise_sentence(sentence: str) -> str:
    return " ".join(sentence.strip().strip(".!?").split()).casefold()


def _is_readable(bracket: str) -> bool:
    """Whether the program reads the bracket as note ids: a range across frames, or one written backwards
    (`[F1.8-F1.4]`, which cites nothing), is left in the text to be named."""

    if _CROSS_FRAME_RANGE.search(bracket):
        return False
    for match in _CITED.finditer(bracket):
        if match.group("first") is not None and int(match.group("first")) > int(match.group("last")):
            return False
        if match.group("first_line") is not None and int(match.group("first_line")) > int(match.group("last_line")):
            return False

    return True


def _list_cited_ids(bracket: str) -> list[str]:
    """Every id a bracket cites, a range `F1.4-F1.8` written out as F1.4, F1.5, F1.6, F1.7, F1.8."""

    cited: list[str] = []
    for match in _CITED.finditer(bracket):
        if match.group("first_line") is not None:
            width = len(match.group("first_line"))
            cited += [f"S{number:0{width}d}"
                      for number in range(int(match.group("first_line")), int(match.group("last_line")) + 1)]
            continue
        if match.group("frame") is None:
            cited.append(match.group(0))
            continue
        # A range across frames never reaches here: `_is_readable` leaves such a bracket to be named
        frame, first, last = match.group("frame"), int(match.group("first")), int(match.group("last"))
        cited += [f"F{frame}.{number}" for number in range(first, last + 1)]

    return cited


def _list_sentences(text: str) -> list[str]:
    """Every sentence of `text`, normalised, headings, fragments of fewer than three words and bare lead-ins left out."""

    # A frame's line and its caption line are a figure, not a claim: a figure placed twice is its own check
    # (DOCUMENT_IMAGE_PLACED_TWICE), and counted here it failed a whole document on a slip of form (2026-10-03). They
    # and the headings are blanked, and a sentence wrapped over two lines is read whole, as `_list_raw_sentences` reads
    # A code block's fence line is no word: glued to the code's last line it made "<code> ```" a sentence the finished
    # document counted and no section's own text held, so a code block printed under three headings passed every
    # chapter and failed the document (lecture-1, 2026-10-14)
    kept = "\n".join("" if line.lstrip().startswith(("#", "```")) or _FIGURE_LINE.match(line.strip()) else line
                     for line in text.splitlines())
    normalised = [_normalise_sentence(sentence) for sentence in _list_raw_sentences(kept)]

    # A lead-in of a few words ending in a colon states nothing: "Electron apps are:" before a slide's list was refused as
    # said earlier three checks running, and the writer dropped the list and lost its notes (DEFECTS 383)
    return [sentence for sentence in normalised if len(sentence.split()) >= _MIN_SENTENCE_WORDS
            and not (sentence.endswith(":") and len(sentence.split()) <= _BARE_LEAD_IN_WORDS)]


def _hide_parameter(match: re.Match) -> str:
    if match.group("name").casefold() not in _SECRET_PARAMETERS:
        return match.group(0)

    return f"{match.group('lead')}{match.group('name')}={_MASK}"


def _is_word(piece: str) -> bool:
    vowels = sum(letter in "aeiouyAEIOUY" for letter in piece)

    return len(piece) >= 4 and vowels / len(piece) >= 0.25 and not _FOUR_CONSONANTS.search(piece)


def _hide_unless_name(match: re.Match[str]) -> str:
    """`[secret]` for a generated value; a code name, made mostly of words or joined from parts of one or two case
    runs, and never holding `~`, as it was."""

    value = match.group(0)
    words = sum(len(piece) for piece in _NAME_PIECE.findall(value) if _is_word(piece))
    parts = [part for part in _NAME_PART_SEPARATOR.split(value) if part]
    is_joined_name = len(parts) >= _NAME_MIN_PARTS and all(len(_CASE_RUN.findall(part)) <= _NAME_PART_CASE_RUNS
                                                           for part in parts)
    is_name = words / len(value) >= _NAME_WORD_SHARE or is_joined_name

    return value if "~" not in value and is_name else _SECRET_MASK


def _hide_named_value(match: re.Match[str]) -> str:
    value = match.group("value")
    if _NO_VALUE.match(value):
        return match.group(0)
    quote = value[0] if value[0] in "`\"'" else ""
    name = match.group("name") or match.group("label")
    separator = match.group("separator") or match.group("equals")

    return f"{name}{separator}{quote}{_SECRET_MASK}{quote}"


def _is_sentence_after_value(match: re.Match[str]) -> bool:
    """Whether the words after "value" are a sentence going on, not a value: no ":", "=" or quote marks the label, and
    what follows holds a space ("a random 4-byte value generated at boot", which was hidden as "value [secret]" on a
    slide about a CRC "secret", DEFECTS 646). A bare token after it ("Value abc123") is still a value."""

    lead = match.group("lead")
    # A quote closing a quotation right after "value" ("... a 4-byte value" → it changes) marks no label; one opening
    # the value does, and the lead then ends with it
    is_labelled = any(mark in lead for mark in ":=") or lead.endswith(("\"", "`", "'"))

    return not is_labelled and " " in match.group("value").strip()


def _hide_labelled_value(line: str) -> str:
    if not _CREDENTIAL_PART.search(line):
        return line

    return _VALUE_LABEL.sub(lambda match: match.group(0) if _NO_VALUE.match(match.group("value").strip())
                            or _is_sentence_after_value(match)
                            else f"{match.group('lead')}{_SECRET_MASK}{match.group('end')}", line)


_STEP_SUMMARY_LINE = re.compile(r"^\s*-\s*(?:a\s+)?step\b.*\bof the screen of frame\s+\d+", re.IGNORECASE)


def _hide_values_under_named_parts(text: str) -> str:
    lines = [_hide_labelled_value(line) for line in text.split("\n")]
    parents: list[tuple[int, bool]] = []
    for number, line in enumerate(lines):
        indent = len(line) - len(line.lstrip())
        while parents and parents[-1][0] >= indent:
            parents.pop()
        if parents and parents[-1][1] and not _CREDENTIAL_PART.search(line):
            lines[number] = _CHILD_VALUE.sub(
                lambda match: match.group(0) if _NO_VALUE.match(match.group("value"))
                else f"{match.group('lead')}{match.group('quote')}{_SECRET_MASK}{match.group('quote')}", line)
        if line.strip():
            # A build step's own line sums up what it changes ("a password prompt overlay and an updated row"): it names
            # no credential field, and under it a headset's name was hidden while the password stood (DEFECTS 345)
            is_named = bool(_CREDENTIAL_PART.search(line)) and not _STEP_SUMMARY_LINE.match(line)
            parents.append((indent, is_named or bool(parents and parents[-1][1])))

    return "\n".join(lines)


def _strip_word(word: str) -> str:
    return word.strip(_WORD_MARKS).lstrip("-\u2013\u2014")


def _is_credential_flag(word: str) -> bool:
    stripped = _strip_word(word)
    match = _CREDENTIAL_PAIR.match(stripped)

    return match is not None and len(stripped) - match.end() >= _SECRET_VALUE_MIN_CHARS


def _is_credential_key(words: list[str],
                       index: int) -> bool:
    # A credential's key with a value after it that is neither a template variable, an address, dots standing for one
    # ("client_secret: ...," in a drawn sequence diagram) nor the next line's key, which follows once the value is masked
    if not _CREDENTIAL_KEY.match(_QUOTES.sub("", words[index])) or index + 1 >= len(words):
        return False
    value = _QUOTES.sub("", words[index + 1]).strip(",;")

    # A value naming its own kind is a placeholder: "password": "your-sync-password" (2026-10-02)
    return (re.search(r"[A-Za-z0-9]", value) is not None and not value.startswith("{{") and "://" not in value
            and not value.endswith(":") and not re.search(_CREDENTIAL_KIND, value, re.IGNORECASE))


def _is_followed_by_variable(words: list[str],
                             index: int) -> bool:
    # A header whose value is a template variable, "Authorization: Bearer {{accessToken}}", or dots standing for one,
    # "client_secret: ...," in a drawn sequence diagram, shows no secret
    # Quote marks go, braces stay: a quoted "{{accessToken}}" is a template variable too
    rest = [_QUOTES.sub("", word).strip() for word in words[index + 1:index + 3] if not is_auth_scheme(word)]

    return bool(rest) and (rest[0].startswith("{{") or re.search(r"[A-Za-z0-9]", rest[0]) is None)


def _is_label_or_key(word: str) -> bool:
    # A settings file's key counts as a label: a mask over '"apikey":' alone hid the key and left its value (2026-10-02)
    return is_credential_label(word) or _CREDENTIAL_KEY.match(_QUOTES.sub("", word)) is not None


def _list_word_runs(words: list[str],
                    size: int) -> set[tuple[str, ...]]:
    """Every run of `size` consecutive words of `words`."""

    return {tuple(words[start:start + size]) for start in range(len(words) - size + 1)}


def _compute_spoken_share(words: list[str],
                          spoken_runs: set[tuple[str, ...]],
                          screen_runs: set[tuple[str, ...]],
                          size: int) -> float:
    """The share of `words` inside a run of `size` words the speech holds and the screen does not."""

    covered: set[int] = set()
    for start in range(len(words) - size + 1):
        run = tuple(words[start:start + size])
        if run in spoken_runs and run not in screen_runs:
            covered.update(range(start, start + size))

    return len(covered) / len(words) if words else 0.0


def _list_raw_sentences(text: str) -> list[str]:
    # A line that opens a block of its own (a frame, a marked line, a list item, a table row, a heading) is never joined
    # to the line before: an `> **Open:**` line ending in its ids ran into the frame line under it, and a finding quoted
    # words from both as one sentence (2026-10-05)
    blocks: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            blocks.append("")
        elif not blocks or not blocks[-1] or _BLOCK_START.match(line):
            blocks.append(line.strip())
        else:
            blocks[-1] += " " + line.strip()

    return [sentence.strip() for block in blocks for sentence in _SENTENCE_END.split(" ".join(block.split()))
            if sentence.strip()]


def is_not_yet_shown(text: str) -> bool:
    """Does `text` only say a step is not shown, written or covered yet: a note about the recording, set aside."""

    return bool(_NOT_YET_SHOWN.search(text))


# A fact note that restates an earlier one of the same chapter: they share 60% of the content words of the shorter, 6 or
# more. Over the public runs' chapter requests 292 of 2,767 fact notes do, nearly all the same point noted on two frames
# or twice on one, which the writer then wrote twice (froscon 3211's radio module replaced, DEFECTS 302); a few are two
# values of one setting, so the writer is told of the pair and judges it
_RESTATE_SHARE = 0.6
_RESTATE_MIN_WORDS = 6
_RESTATE_STOP = frozenset("the a an and or of to in on for with by is are be as at from that this its it which can via "
                          "into when while than also only each".split())
_RESTATE_WORD = re.compile(r"[a-z0-9][a-z0-9.-]*[a-z0-9]|[a-z0-9]")


# A name a fact holds: a quoted text, or a capitalised word that does not open the sentence. Two facts naming different
# things are two points however many words they share: "A black arrow points from "Client" to the second component"
# and the same from "Gateway" were named one point, and the writer weighed merging them (DEFECTS 501)
_QUOTED_NAME = re.compile(r'"([^"]+)"')
_CAPITALISED = re.compile(r"(?<![.!?]\s)(?<!^)\b[A-Z][A-Za-z0-9-]+")


def _list_content_words(text: str) -> set[str]:
    return {word for word in _RESTATE_WORD.findall(text.lower()) if word not in _RESTATE_STOP}


def _list_names(text: str) -> set[str]:
    quoted = {name.casefold() for name in _QUOTED_NAME.findall(text)}
    capitalised = {word.casefold() for word in _CAPITALISED.findall(_QUOTED_NAME.sub(" ", text.strip()))}

    return quoted | capitalised


# A slide's own sentence held in a markdown note: the request tells the writer to state it in its own sentences, and a
# package page's "SimShady is a package for shading simulation ... It runs on the GPU using WebGL." was written as a
# paragraph after the facts that said it (38c3-openpv chapter 5, DEFECTS 637)
_MARKDOWN_SENTENCE = re.compile(r"[A-Z][^.!?\n]{20,}[.!?]")


def _find_restated(text: str,
                   seen: list[tuple[str, set[str], set[str]]]) -> str | None:
    """The first earlier fact `text` says again: 60% of the shorter's content words, 6 or more, and the same names."""

    words, names = _list_content_words(text), _list_names(text)
    for earlier_id, earlier, earlier_names in seen:
        shorter = min(len(words), len(earlier))
        is_same_names = names <= earlier_names or earlier_names <= names
        if shorter >= _RESTATE_MIN_WORDS and len(words & earlier) / shorter >= _RESTATE_SHARE and is_same_names:
            return earlier_id

    return None


def list_restating_entries(entries: list[dict]) -> dict[str, str]:
    """Each fact entry's id that restates an earlier fact entry of `entries`, and each markdown entry one of whose
    sentences does, mapped to the first such earlier fact's id."""

    seen: list[tuple[str, set[str], set[str]]] = []
    restating: dict[str, str] = {}
    for entry in entries:
        text = str(entry.get("text") or "")
        if entry.get("kind") == "markdown":
            found = next((earlier_id for sentence in _MARKDOWN_SENTENCE.findall(text)
                          for earlier_id in [_find_restated(sentence, seen)] if earlier_id), None)
            if found:
                restating[str(entry["id"])] = found
            continue
        if entry.get("kind") != "facts":
            continue
        found = _find_restated(text, seen)
        if found:
            restating[str(entry["id"])] = found
        seen.append((str(entry["id"]), _list_content_words(text), _list_names(text)))

    return restating


def find_banned_phrases(text: str) -> list[str]:
    """The banned phrases that occur in `text`, whatever their case, in the contract's own order."""

    kept = _FRAME_OF_THE_SUBJECT.sub(" ", _SEGMENT_OF_THE_SUBJECT.sub(" ", text))

    return [phrase for phrase, pattern in _BANNED if pattern.search(kept)]


def find_banned_snippets(text: str,
                         around: int = 40) -> list[str]:
    """The words round each banned phrase of `text`, `around` characters either side, once a sentence: a finding naming
    only the word left a worker reading its whole chapter for "frame" in a frame's id it had written (DEFECTS 238)."""

    snippets = []
    for sentence in _list_raw_sentences(text):
        kept = _FRAME_OF_THE_SUBJECT.sub(lambda match: " " * len(match.group(0)),
                                         _SEGMENT_OF_THE_SUBJECT.sub(lambda match: " " * len(match.group(0)), sentence))
        found = next((match for _, pattern in _BANNED for match in [pattern.search(kept)] if match), None)
        if found:
            start, end = max(0, found.start() - around), min(len(sentence), found.end() + around)
            snippets.append(("…" if start else "") + sentence[start:end] + ("…" if end < len(sentence) else ""))

    return snippets


def find_entry_ids(text: str) -> list[str]:
    """Entry ids written into the prose, `F154.2` or `[S0415]`. They belong in a paragraph's `entry_ids`, never in the
    text a reader reads: measured 2026-09-27, a section wrote an id in brackets after every sentence."""

    ids = _ENTRY_ID.findall(_ARTICLE_NUMBER.sub("", text))

    return ids


def find_bare_topic_ids(text: str) -> list[str]:
    """Heading ids written in a sentence outside brackets ("shown in T01.2"): a bracketed one is a link, a bare one
    reaches the reader as "T01.2" (DEFECTS 588)."""

    return re.findall(rf"(?<![\w.]){TOPIC_ID_PATTERN}(?![\w])", re.sub(r"\[[^\]\n]*\]", " ", text))


def split_ids_after_fence(text: str) -> str:
    """`text` with a code block's ids, written on the line after its fence with text after them, given that line alone."""

    return _IDS_THEN_TEXT_AFTER_FENCE.sub(lambda match: f"{match.group(1)}{match.group(2)}\n\n{match.group(3)}", text)


def rewrite_marked_line_ids_in_brackets(text: str) -> str:
    """`text` with a marked line's ids written in round brackets at its end put in square ones: a set-aside line ending
    "(F151.3, F153.1)." was refused as a marked line naming no id (2026-10-02)."""

    return _MARKED_LINE_ROUND_IDS.sub(lambda match: f"{match.group(1)} [{match.group(2).strip().rstrip(',')}]", text)


def write_short_ranges_in_full(text: str) -> str:
    """`text` with each short range, `F82.1-82.3`, written as `F82.1-F82.3`, a range's typographic dash,
    `F13.9–F13.12`, written as a hyphen, and ids parted by semicolons, `[F14.1; S0200]`, by commas, and so are ids parted by
    spaces alone: a chapter wrote every bracket with semicolons and each was refused as unread (DEFECTS 233)."""

    plain = _ID_BRACKET.sub(lambda match: _SPACE_BETWEEN_IDS.sub(", ", _DASH_BETWEEN_IDS.sub("-", match.group(0)).replace(";", ",")), text)

    return _SHORT_RANGE.sub(lambda match: f"F{match.group(1)}.{match.group(2)}-F{match.group(3)}.{match.group(4)}", plain)


def expand_cross_frame_ranges(text: str,
                              ordered_ids: list[str]) -> str:
    """`text` with each range across frames, `F40.1-F42.3`, written out as every frame note between its two ends in
    `ordered_ids` (the request's notes, in order). A range with an end `ordered_ids` does not hold is left as written."""

    def _expand(match: re.Match) -> str:
        first, last = match.group(1), match.group(2)
        if first not in ordered_ids or last not in ordered_ids or ordered_ids.index(first) > ordered_ids.index(last):
            return match.group(0)
        between = ordered_ids[ordered_ids.index(first):ordered_ids.index(last) + 1]

        return ID_SEPARATOR.join(entry_id for entry_id in between if entry_id.startswith("F"))

    return _CROSS_FRAME_ITEM.sub(_expand, text)


def expand_line_ranges(text: str,
                       ordered_ids: list[str]) -> str:
    """`text` with each range that has a transcript line at an end, `F63.1-S0249` or `S0234-S0236`, written out as
    every id between its two ends in `ordered_ids`, both kinds: counted out by number, `S0234-S0236` named S0235, a
    line no section cites, and a range from a note to a line was read as no range at all. A range with an end
    `ordered_ids` does not hold is left as written."""

    def _expand(match: re.Match) -> str:
        first, last = match.group(1), match.group(2)
        is_notes_only = first.startswith("F") and last.startswith("F")
        if (is_notes_only or first not in ordered_ids or last not in ordered_ids
                or ordered_ids.index(first) > ordered_ids.index(last)):
            return match.group(0)

        return ID_SEPARATOR.join(ordered_ids[ordered_ids.index(first):ordered_ids.index(last) + 1])

    return _ID_RANGE_ITEM.sub(_expand, text)


def remove_bracketed_entry_ids(text: str) -> str:
    """`text` with every bracket of note ids the program reads taken out, `[F154.2]` or `[S0415, F1.2]`; one it cannot
    read stays, to be named, and a topic link `[T08.3]` and a web link stay."""

    return _BRACKETED_IDS.sub(lambda match: "" if _is_readable(match.group(0)) else match.group(0), text)


def move_bracketed_entry_ids(paragraph: dict) -> dict:
    """The paragraph with every `[F154.2]` or `[S0415, F1.2]` its model wrote into the text taken out, and those ids
    added to its `entry_ids`, once each, in the order cited. A bare id is left where it is, for the validator to
    reject: only a bracket says for certain which sentence it cites."""

    text = str(paragraph.get("text", ""))
    cited = [entry_id for match in _BRACKETED_IDS.finditer(text) if _is_readable(match.group(0))
             for entry_id in _list_cited_ids(match.group(0))]
    entry_ids = list(dict.fromkeys([*(paragraph.get("entry_ids") or []), *cited]))
    moved = {**paragraph, "text": remove_bracketed_entry_ids(text).strip(), "entry_ids": entry_ids}

    return moved


def list_cited_ids(text: str) -> list[str]:
    """Every note id the brackets of `text` cite, a range written out, once each, in the order cited. A range written
    with a typographic dash, `[F81.1–F81.3]`, is read as one: unread, a block edit that dropped such a range was never
    named as losing what the draft before cited (DEFECTS 591)."""

    text = write_short_ranges_in_full(text)

    return list(dict.fromkeys(entry_id for match in _BRACKETED_IDS.finditer(text) if _is_readable(match.group(0))
                              for entry_id in _list_cited_ids(match.group(0))))


def find_unread_brackets(text: str) -> list[str]:
    """Every bracket in `text` that names a frame or a transcript line and that the program could not read as note ids."""

    return _UNREAD_BRACKET.findall(remove_bracketed_entry_ids(text))


def describe_unread_items(bracket: str) -> list[str]:
    """Each item of an unread bracket that is no note id, with what is wrong with it: a worker told only which bracket
    failed looked for a wrong id, and the fault was the bold marks around a right one (2026-09-30)."""

    described = []
    for item in (part.strip() for part in bracket.strip("[]").split(",")):
        if not item or (_READ_ITEM.fullmatch(item) and _is_readable(f"[{item}]")):
            continue
        if _CROSS_FRAME_RANGE.fullmatch(item):
            described.append(f"{item!r}: a range across frames: name each frame's notes")
            continue
        if _READ_ITEM.fullmatch(item) and not _is_readable(f"[{item}]"):
            described.append(f"{item!r}: a range written backwards: write the lower note first")
            continue
        bare = item.strip("*_`'\" ")
        why = ("marks around a note id: write the id alone" if bare != item and _READ_ITEM.fullmatch(bare) else
               "a whole frame, not a note: name its notes" if re.fullmatch(r"F\d+", bare) else "no note id")
        described.append(f"{item!r}: {why}")

    return described


def find_timestamps(text: str) -> list[str]:
    """Every recording-position clock time such as `1:23`; a clock value attached to an ISO date is metadata, one
    followed by AM, PM, o'clock, Uhr or a time zone is a time of day the meeting names, and one right after a colon is
    a place in a file."""

    # An ISO date-time in data ("2026-03-03T18:04:18Z") is metadata, taken out before the search (2026-09-30: '04:18'
    # was found inside one in a JSON block)
    text = _ISO_DATETIME.sub(" ", text)
    # A figure's description reads the figure, a chart's axis times its own: "a spike at about 20:00" read off an axis
    # was refused as a position in the recording (DEFECTS 241)
    text = _WHOLE_FIGURE_DESCRIPTION_LINE.sub(" ", text)

    # A value right after a colon is a place in a file, line and column ("utils.py:25:26", a linter's output), never a
    # position in the recording (Lecture 9 chapter 2)
    return [match.group(0) for match in _TIMESTAMP.finditer(text)
            if not _DATED_CLOCK_PREFIX.search(text[:match.start()]) and not _TIME_OF_DAY_SUFFIX.match(text[match.end():])
            and not text[:match.start()].endswith(":")]


def _list_figures(sentence: str) -> set[str]:
    """The numbers of `sentence` that can name one fact: two digits or more."""

    return {number for number in _NUMBER.findall(sentence) if sum(char.isdigit() for char in number) >= 2}


# A table's separator row: "|---|---|", with or without colons and spaces
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _drop_table_headers(text: str) -> str:
    """`text` without each table's header and separator rows: a header repeated over a table continued from an
    earlier section states nothing, and counted as said earlier it made a writer drop the table, its last row with it
    (pdf-02)."""

    lines = text.splitlines()
    dropped = {index for index, line in enumerate(lines) if _TABLE_SEPARATOR.match(line)}
    dropped |= {index - 1 for index in dropped if index > 0 and lines[index - 1].lstrip().startswith("|")}

    return "\n".join(line for index, line in enumerate(lines) if index not in dropped)


def find_sentences_said_earlier(text: str,
                                earlier_text: str) -> list[str]:
    """Every sentence of `text` that `earlier_text` already holds, normalised, each listed once; a table's header and
    separator rows are no sentence."""

    text, earlier_text = _drop_table_headers(text), _drop_table_headers(earlier_text)
    earlier_sentences = _list_sentences(remove_bracketed_entry_ids(earlier_text))
    earlier = set(earlier_sentences)
    # The same figures in other words are the same fact: one count of a tenant's users, groups, applications and
    # devices stood in three sections, each sentence worded differently (2026-10-01)
    # Single digits are left out: 1, 3 and 5 stand in many facts of a coding lesson, and "`range(3, 5)` keeps the step
    # of 1" was refused as said by "`range(5)` gives 0, 1, 2, 3 and 4" (2026-10-12)
    earlier_numbers = [numbers for sentence in earlier_sentences
                       if len(numbers := _list_figures(sentence)) >= _MIN_REPEATED_NUMBERS]
    said = []
    for sentence in _list_sentences(remove_bracketed_entry_ids(text)):
        numbers = _list_figures(sentence)
        if sentence in earlier or (len(numbers) >= _MIN_REPEATED_NUMBERS
                                   and any(numbers <= other for other in earlier_numbers)):
            said.append(sentence)

    return list(dict.fromkeys(said))


def remove_sections(markdown: str,
                    headings: list[str]) -> str:
    """`markdown` without the second-level sections titled `headings`, each from its `## ` line to the next `## `
    line. The executive summary, the agenda and the closing may say again what a chapter says (REQUIREMENTS.md R9 and
    R11), so the check for a claim said twice reads the document without them."""

    kept = []
    is_removed = False
    for line in markdown.splitlines():
        if line.startswith("## "):
            is_removed = line[3:].strip() in headings
        if not is_removed:
            kept.append(line)

    return "\n".join(kept)


def render_figure_mention(number: int | str) -> str:
    return f"Figure {number}"


def remove_figure_descriptions(markdown: str) -> str:
    """`markdown` without each figure's `> **What the figure shows:**` paragraph. A description quotes every text on its
    figure exactly (write.md), so a screen message the prose quoted too is no claim said twice (2026-10-08: a success
    message quoted in a chapter and in the description of its figure failed the finished document)."""

    kept = []
    is_in_description = False
    for line in markdown.splitlines():
        if _FIGURE_DESCRIPTION_LINE.match(line):
            is_in_description = True
            continue
        if is_in_description and line.startswith(">"):
            continue
        is_in_description = False
        kept.append(line)

    return "\n".join(kept)


def build_heading_anchors(headings: list[str]) -> list[str]:
    """The link anchor of each heading, in document order, as a markdown renderer derives it: lower case, punctuation
    dropped, spaces as hyphens, and a heading seen before numbered `-1`, `-2` on."""

    seen: dict[str, int] = {}
    anchors = []
    for heading in headings:
        anchor = _ANCHOR_DROPPED.sub("", heading.strip().casefold()).replace(" ", "-")
        count = seen.get(anchor, 0)
        seen[anchor] = count + 1
        anchors.append(anchor if count == 0 else f"{anchor}-{count}")

    return anchors


# A list item with nothing after its mark: "B." or "- " alone on its line
_EMPTY_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)]|[A-Z][.)])\s*$", re.MULTILINE)


def find_empty_list_items(text: str) -> list[str]:
    """The list items of `text` that hold no text, outside code fences: a list of exemption codes was written "B." and
    "C." with their text left out, which its paragraph held (pdf-real-fw9)."""

    empty, is_fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            is_fenced = not is_fenced
        elif not is_fenced and _EMPTY_LIST_ITEM.match(line):
            empty.append(line.strip())

    return empty


_CODE_FENCE_BLOCK = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
# A code block counts as written when this share of its lines stands in the text
CODE_BLOCK_HELD_SHARE = 0.8


def list_code_blocks(text: str) -> list[str]:
    """The fenced code blocks of `text`, each its lines between the fences, empty ones left out."""

    return [match.group(1).strip("\n") for match in _CODE_FENCE_BLOCK.finditer(text) if match.group(1).strip()]


_DIGIT_RUN = re.compile(r"\d+")


def find_changed_value_lines(blocks: list[str],
                             screen_texts: list[str]) -> list[tuple[str, str]]:
    """Each line of `blocks` that stands in no line of `screen_texts` while one of them is the same line with other
    numbers, paired with that line: a chapter copied a boot log as "RSSI: 0" where the frame's notes it cites write
    "RSSI: 100" (DEFECTS 301). Over the 975 code block lines of 17 public runs these three were the only such lines."""

    screen_lines = {" ".join(line.split()) for text in screen_texts for line in text.splitlines() if line.strip()}
    by_shape: dict[str, str] = {}
    for line in screen_lines:
        by_shape.setdefault(_DIGIT_RUN.sub("#", line), line)
    changed = []
    for block in blocks:
        for raw in block.splitlines():
            line = " ".join(raw.split())
            if not _DIGIT_RUN.search(line) or line in screen_lines:
                continue
            held = by_shape.get(_DIGIT_RUN.sub("#", line))
            if held is not None and (line, held) not in changed:
                changed.append((line, held))

    return changed


def is_code_block_held(block: str,
                       text: str) -> bool:
    """Whether `text` holds CODE_BLOCK_HELD_SHARE of `block`'s lines, spacing aside."""

    lines = [" ".join(line.split()) for line in block.splitlines() if line.strip()]
    written = {" ".join(line.split()) for line in text.splitlines()}

    return bool(lines) and sum(line in written for line in lines) >= CODE_BLOCK_HELD_SHARE * len(lines)


def remove_topic_links(text: str) -> str:
    """`text` with every topic link `[T08.3]` taken out: what the summary is shown of a section, where a link reads as
    a source to cite and a summary cited one."""

    return " ".join(_TOPIC_LINK.sub("", text).split())


def find_topic_links(text: str) -> list[str]:
    """The topic ids a text links to, `[T08.3]` or `[T08.3, T09.1]`, in the order written."""

    found = [(match.start(), [match.group(1)]) for match in _TOPIC_LINK.finditer(text)]
    found += [(match.start(), _TOPIC_ID.findall(match.group(1))) for match in _TOPIC_LIST.finditer(text)]

    return [topic_id for _, ids in sorted(found) for topic_id in ids]


def render_topic_links(text: str,
                       targets: dict[str, tuple[str, str]]) -> str:
    """Every `[T08.3]` whose id is in `targets` (id -> heading, anchor) as a markdown link to that heading, and every
    `(T08.3)` too, the italic name before it as the link's text when there is one, and a bracket of several ids
    `[T08.3, T09.1]` as one link each, separated by commas. An unknown id is left as written, for the check to name; a
    bracket with one unknown id stays whole."""

    listed = _TOPIC_LIST.sub(lambda match: (", ".join(f"[{targets[topic_id][0]}](#{targets[topic_id][1]})"
                                                      for topic_id in _TOPIC_ID.findall(match.group(1)))
                                            if all(topic_id in targets for topic_id in _TOPIC_ID.findall(match.group(1)))
                                            else match.group(0)), text)
    linked = _TOPIC_LINK.sub(lambda match: (f"[{targets[match.group(1)][0]}](#{targets[match.group(1)][1]})"
                                            if match.group(1) in targets else match.group(0)), listed)

    return _TOPIC_IN_PARENS.sub(lambda match: (f"[{match.group(1) or targets[match.group(2)][0]}]"
                                               f"(#{targets[match.group(2)][1]})"
                                               if match.group(2) in targets else match.group(0)), linked)


def render_heading_links(markdown: str,
                         headings_by_id: dict[str, str]) -> str:
    """Every `[T08.3]` in `markdown` as a link to the first heading printed as `headings_by_id[id]`, with the anchor a
    markdown renderer gives that heading in this document."""

    headings = _HEADING.findall(markdown)
    anchors: dict[str, str] = {}
    for heading, anchor in zip(headings, build_heading_anchors(headings)):
        anchors.setdefault(heading, anchor)

    return render_topic_links(markdown, {topic_id: (heading, anchors[heading]) for topic_id, heading in
                                         headings_by_id.items() if heading in anchors})


# A full stop, question or exclamation mark ending a sentence inside a joined code block, or a run of them: an output's
# "..." line, of which one dot was taken out, still cut the block, and its opening lines, shared with another output,
# were refused as said twice (DEFECTS 657)
_CODE_SENTENCE_END = re.compile(r"[.!?…]+(\s|$)")


def _join_code_blocks(text: str) -> str:
    """`text` with each fenced code block's lines joined into one line, so the block is one sentence: a workflow file
    repeats `- uses: actions/checkout@v5` in each of its jobs, and read line by line one block was refused as a
    sentence said three times (2026-10-21). A block written again under another heading is still one sentence said
    twice."""

    lines, block = [], None
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            if block is not None:
                # Its sentence ends taken out, or the sentence reader cut the joined block at each full stop again and
                # a fixed line two different blocks share was refused as said twice: two Bash permission dialogs, each
                # ending "Esc to cancel · Tab to add additional instructions" (Lecture 7, DEFECTS 586)
                joined = " ".join(part.strip() for part in block if part.strip())
                lines.append(_CODE_SENTENCE_END.sub(r"\1", joined))
            lines.append(line)
            block = [] if block is None else None
        elif block is not None:
            block.append(line)
        else:
            lines.append(line)
    if block is not None:
        lines.extend(block)

    return "\n".join(lines)


# What follows a question to say it stayed open: no answer ("Nobody knew.", "Not decided yet.")
_NOT_ANSWERED = re.compile(r"^(?:but\s+)?(?:nobody|no one|none of us)\s+(?:knew|knows|could\s+(?:say|tell|answer)|"
                           r"answered|had\s+an?\s+answer)\b|^(?:it\s+(?:is|was|remains)\s+)?"
                           r"(?:not|un)(?:\s*)(?:known|clear|decided|answered|settled|resolved)\b|^(?:still\s+)?open\b",
                           re.IGNORECASE)


# The form `DOCUMENT_OPEN_ANSWERED`'s own fix asks for, "Asked whether <question>, the team answered that <answer>": it holds
# no "?", so the test below read it as no answer, and a Q&A's fourteen such lines stood in release test 3's document as
# open lines and in its closing's open points (DEFECTS 464). Nobody answering is no answer.
_ASKED_AND_ANSWERED = re.compile(r"^asked\b[^?]*?,\s+(?!(?:nobody|no one|none)\b)(?:the\s+)?[\w' -]{1,40}?\s+"
                                 r"(?:answered|replied|responded|said|stated|noted|explained|concluded|acknowledged|confirmed)\b",
                                 re.IGNORECASE)


def is_answered_question(line: str) -> bool:
    """Whether an open point is a question followed by its answer: text after its first "?" that is not one more
    question, or the form "Asked <question>, <someone> answered <answer>". A parenthesis after the question is a note
    on it, not its answer, and a line saying nobody knew is no answer: "Open question: does a partner need a new key?
    Nobody knew." was refused as answered (pdf-06)."""

    if _ASKED_AND_ANSWERED.match(_BRACKETED_IDS.sub("", line).strip()):
        return True
    depth = 0
    for index, char in enumerate(line):
        depth += {"(": 1, ")": -1}.get(char, 0)
        # A "?" inside a parenthesis is a note's own question ("(the client or the server?)"), never the point's
        if char == "?" and depth <= 0:
            after = _BRACKETED_IDS.sub("", line[index + 1:]).strip(" .,;:)]")

            return (bool(after) and not after.endswith("?") and not after.startswith("(")
                    and not _NOT_ANSWERED.match(after))

    return False


def join_list_blocks(text: str) -> str:
    """`text` with each run of list or table lines joined into one line: the whole document holds no ids to tell a
    screen's list from the writer's, and the section checks compare the writer's own lists line by line."""

    lines, run = [], []
    for line in text.splitlines():
        if _LIST_OR_TABLE_LINE.match(line):
            run.append(line.strip())
            continue
        if run:
            lines.append(" ".join(run))
            run = []
        lines.append(line)
    if run:
        lines.append(" ".join(run))

    return "\n".join(lines)


def find_repeated_sentences(text: str,
                            is_list_one_sentence: bool = False) -> list[str]:
    """Every sentence that occurs more than once, normalised, each listed once. A heading or a
    fragment of fewer than three words is not a sentence. A code block is one sentence, and with
    `is_list_one_sentence` each list or table is too."""

    seen: set[str] = set()
    repeated: list[str] = []
    joined = _join_code_blocks(text)
    for sentence in _list_sentences(join_list_blocks(joined) if is_list_one_sentence else joined):
        if sentence in seen and sentence not in repeated:
            repeated.append(sentence)
        seen.add(sentence)

    return repeated


def hide_secret_query_values(text: str) -> str:
    """`text` with the value of every secret-named query or fragment parameter hidden: `?code=abc&lang=de` becomes
    `?code=…&lang=de`, in an address with or without its scheme. Everything else is left exactly as it was."""

    return _SECRET_PARAMETER.sub(_hide_parameter, text)


def hide_secret_tokens(text: str) -> str:
    """`text` with every generated secret value, such as a client secret or a token copied off a screen, and every
    value written beside a name that says it is a credential, written `[secret]`. A code name the same shape matches
    (a test method with a digit in it) is kept."""

    hidden = _SECRET_TOKEN.sub(_hide_unless_name, _TOKEN_SEGMENT.sub(_SECRET_MASK, text))

    return _hide_values_under_named_parts(_NAMED_SECRET.sub(_hide_named_value, hidden))


def _hide_secrets_in_value(field_value: Any) -> Any:
    """One field of a record with its texts' secrets hidden: a text, every text of a list, and every text of an entry
    of a labelled section (a web address's `what_it_is`, a screenshot's `why`); anything else as it was."""

    if isinstance(field_value, str):
        return hide_secret_tokens(field_value)
    if isinstance(field_value, list):
        return [_hide_secrets_in_value(item) for item in field_value]
    if isinstance(field_value, dict):
        return hide_secrets_in_record(field_value)

    return field_value


def hide_secrets_in_record(record: dict) -> dict:
    """The record with `hide_secret_tokens` applied to every text and every text of a list, the rest as it was."""

    return {key: _hide_secrets_in_value(field_value) for key, field_value in record.items()}


def hide_claim_secrets(claim: dict) -> dict:
    """A paragraph, a decision, an action item or a summary sentence with the secrets in its web addresses masked."""

    return {**claim, "text": hide_secret_query_values(str(claim.get("text", "")))}


def is_secret_shown(text: str) -> bool:
    """Does `text`, phase 3's note on a part of a frame, say the part shows a credential?"""

    return _SECRET_WORDS.search(text) is not None


def is_credential_label(word: str) -> bool:
    # The reader keeps the brackets and marks a screen prints around a header's name: "(cookie:", "[authorization:"
    return _CREDENTIAL_LABEL.match(word.strip(_WORD_MARKS)) is not None


def is_secret_named_strongly(text: str) -> bool:
    """Does `text`, phase 3's note on a frame, name a secret, a password or an API key, which holds a cut
    to a mask whatever the reader reads on it? Not when each such word only names a part of a form ("password field",
    "password change prompt") and nothing says a value shows."""

    named = list(_STRONG_SECRET_WORDS.finditer(text))
    if not named:
        return False

    return (not all(_FORM_PART_AFTER.match(text, match.end()) for match in named)
            or _VALUE_SHOWN.search(text) is not None)


def find_readable_secrets(words: list[str]) -> list[str]:
    """The words the text reader read on a cut that still show a secret: a credential label whole, a long value by its
    first 8 characters, so a finding names it without repeating it."""

    readable = []
    for index, word in enumerate(words):
        is_label, is_key = is_credential_label(word), _is_credential_key(words, index)
        is_shown = (_LEFT_VALUE.match(word) is not None or (is_label and not _is_followed_by_variable(words, index))
                    or _CREDENTIAL_PAIR.match(_strip_word(word)) is not None or is_key)
        if is_shown:
            # A label or a key is named whole; a value only by its start, so the finding never repeats it
            readable.append(word if is_label or is_key else f"{word[:8]}…")

    return readable


def find_credential_keys(words: list[str]) -> list[str]:
    """The keys in `words` that name a credential and have a value after them ('"syncUserPassword":' then a value): the
    one sign of a secret precise enough to hold a cut to a mask with no note from phase 3. A long value alone is not:
    on the cuts of earlier runs it matched file paths, address paths and parts of record ids."""

    # A header's label with a long value after it counts too (2026-10-06: a debugger's 'Cookie:' header with its session
    # value was cut with no warning and passed the check, phase 3's note naming no secret)
    return [word for index, word in enumerate(words) if _is_credential_key(words, index) or _is_credential_flag(word)
            or (is_credential_label(word) and index + 1 < len(words)
                and len(_strip_word(words[index + 1])) >= _SECRET_VALUE_MIN_CHARS)]


def is_label_without_value(words: list[str]) -> bool:
    """Do `words`, the words under a mask, hold a credential's label and nothing of its value? Punctuation and an
    authorization scheme ("Basic") are no value."""

    shown = [word for word in words if len(word) > 2]

    return any(_is_label_or_key(word) for word in shown) and all(_is_label_or_key(word) or is_auth_scheme(word)
                                                                 for word in shown)


def has_credential_label(words: list[str]) -> bool:
    """Does one of `words` name a credential, as a header or a field label ("cookie:", "authorization:"), or an
    authorization scheme ("Basic", "Bearer"), or a name=value pair naming a cookie, a session or a token, which the
    reader often reads where it misses the label before them?"""

    return any(is_credential_label(word) or is_auth_scheme(word)
               or _CREDENTIAL_PAIR.match(_strip_word(word)) is not None for word in words)


def is_decision_note(text: str) -> bool:
    """Whether a note opens by naming itself a decision ("Decision: …", "**<year> Decision**: …"), as phase 3 writes one
    a slide or the speech labels a decision."""

    return bool(_DECISION_NOTE.match(text))


def find_narration_words(sentence: str) -> str:
    """The words of `sentence` that make it narration, "the speaker introduces", or "" when none do: a finding that
    quoted only a sentence's first words left its worker hunting for the clause at its end."""

    for pattern in (_NARRATION, _COURSE_OF_MEETING, _RECORDING_META):
        match = pattern.search(sentence)
        if match:
            return match.group(0)
    match = _TALK_AS_SUBJECT.match(sentence.strip())
    if match:
        return match.group(0).strip()
    match = _TALK_GIVEN_AT.search(sentence)
    if match:
        return match.group(0)
    match = _SPEAKER_ACTS.search(sentence.strip())

    return match.group(0).lstrip(",;: ") if match else ""


def find_narrated_sentences(text: str) -> list[str]:
    """The sentences of `text` that tell what someone did in the recording (hovered, pointed, scrolled, joked, paused)
    or talk about the transcript itself (cut off, misheard), not about the subject."""

    # The patterns live in `find_narration_words` alone, so a sentence refused always has the words to quote
    return [sentence for sentence in _list_raw_sentences(text) if find_narration_words(sentence)]


def find_copied_speech(text: str,
                       speech_texts: list[str],
                       min_words: int = 6,
                       screen_texts: list[str] | None = None) -> list[str]:
    """The sentences of `text` that hold a whole spoken sentence of `speech_texts` word for word, or are held whole by
    one, of `min_words` words or more: the speech pasted in, filler and all, instead of what it establishes. A sentence
    equal to a whole spoken sentence is a copy at any length. A sentence `screen_texts` holds is the screen's own text,
    written as the block it is, and no copy of the speaker who read it aloud (2026-10-13: a slide's bullets, read out,
    were refused)."""

    on_screen = " ".join(" ".join(_NOT_WORD_OR_SPACE.sub(" ", screen.lower()).split()) for screen in screen_texts or [])

    every_part = [" ".join(_NOT_WORD_OR_SPACE.sub(" ", part.lower()).split()) for speech in speech_texts
                  for part in _SENTENCE_END.split(speech)]
    spoken = [part for part in every_part if len(part.split()) >= min_words]
    # A whole spoken sentence copied is a copy at any length: "Okay. So." and a lone "Back." reached a document
    exact = {part for part in every_part if part}
    spoken_runs = _list_word_runs(" ".join(every_part).split(), min_words)
    screen_runs = _list_word_runs(on_screen.split(), min_words)
    copied = []
    for sentence in _list_raw_sentences(text):
        written = " ".join(_NOT_WORD_OR_SPACE.sub(" ", sentence.lower()).split())
        if written and written in on_screen:
            continue
        # A spoken sentence inside a written one is a copy only when it is at least half of it: a six-word spoken
        # definition inside a 27-word sentence of the writer's own blocked a chapter (2026-10-04)
        if written in exact or (len(written.split()) >= min_words
                                and any((part in written and 2 * len(part.split()) >= len(written.split()))
                                        or written in part for part in spoken)):
            copied.append(sentence)
        elif (len(written.split()) >= min_words
              and _compute_spoken_share(written.split(), spoken_runs, screen_runs, min_words) >= _SPOKEN_RUNS_SHARE):
            copied.append(sentence)

    return copied


def is_auth_scheme(word: str) -> bool:
    """Whether a word is the scheme an authorization header puts between its label and its value."""

    return word.strip(_WORD_MARKS).casefold() in _AUTH_SCHEMES


def build_cut_id(number: int) -> str:
    """The id of the `number`th cut of a run, `cut-001` for the first; `CUT_ID_PATTERN` matches it."""

    return f"cut-{number:03d}"


def read_cut_number(cut_id: str) -> int:
    """The number a cut id was built from: 3 for `cut-003`."""

    return int(cut_id.removeprefix("cut-"))


# A person's first and last name as the notes give it beside a role: "the presenter <First> <Last>", "Referent:
# <First> <Last>", "<First> <Last> is the speaker". A chapter wrote the presenter's full name where write.md asks for
# the role (froscon 3211, DEFECTS 292); over 11 public runs these name the presenters of 3, and no company ("by" and
# "von" named organisations and are left out)
_ROLE_NAME = r"([A-ZÄÖÜ][a-zäöüß]+(?:\s[A-ZÄÖÜ][a-zäöüß]+){1,2})"
_NAMED_ROLE = re.compile(rf"(?:presenter|speaker|Referent(?:in)?:?|moderator|host|presented by|talk by)\s+\**{_ROLE_NAME}"
                         rf"|{_ROLE_NAME}\s+(?:is|was)\s+(?:the|a)\s+(?:presenter|speaker|moderator|host)")


# A count the text gives of a table's rows, larger than the rows it holds, is wrong whatever the sentence means: no part
# count of 80 incidents is 82 (DEFECTS 519, "82 incidents" over an 80-row table). A smaller count may be a part ("nine
# tenants asked to split refunds") and is not judged here
_UNITS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
          "seventeen eighteen nineteen").split()
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_COUNT = re.compile(r"\b(\d[\d,]*|(?:" + "|".join(_TENS) + r")(?:-(?:" + "|".join(_UNITS[1:10]) + r"))?|"
                    + "|".join(_UNITS) + r")\s+(?:[A-Za-z-]+\s+){0,2}?([A-Za-z]+)\b", re.IGNORECASE)


def _read_count(word: str) -> int | None:
    word = word.casefold().replace(",", "")
    if word.isdigit():
        return int(word)
    tens, _, unit = word.partition("-")
    if tens in _TENS:
        return _TENS[tens] + (_UNITS.index(unit) if unit in _UNITS else 0)

    return _UNITS.index(word) if word in _UNITS else None


def _read_table_rows(table: str) -> tuple[str, int]:
    """A markdown table's first column heading, its markup taken off and in lower case, and its count of data rows."""

    lines = [line for line in table.splitlines() if line.strip().startswith("|")]
    heading = re.sub(r"[*_`]", "", lines[0].strip("| ").split("|")[0]).strip().casefold() if lines else ""

    return heading, max(0, len(lines) - 2)


def find_counts_above_table_rows(blocks: list[dict],
                                 source_text: str = "") -> list[str]:
    """Each count a section's prose gives of the rows of one of its tables, named by the table's first column ("82
    incidents" for a table headed "Incident"), that is larger than the rows the table holds: "82 incidents (the table holds
    80)". A number `source_text` (the section's notes) states only as another quantity is no count of the rows: a
    budget the notes give as "50 retries in one minute", written "50 attempts per minute" over a table of 4 attempts
    (DEFECTS 526)."""

    tables = [_read_table_rows(str(block.get("text") or "")) for block in blocks if block.get("kind") == "table"]
    rows_of = {}
    for heading, rows in tables:
        if heading:
            rows_of[heading] = rows_of.get(heading, 0) + rows
    stated = {}
    for match in _COUNT.finditer(source_text):
        stated.setdefault(_read_count(match.group(1)), set()).add(match.group(2).casefold().removesuffix("s"))
    found = []
    for block in blocks:
        if block.get("kind") == "table":
            continue
        for match in _COUNT.finditer(str(block.get("text") or "")):
            count, noun = _read_count(match.group(1)), match.group(2).casefold()
            rows = rows_of.get(noun.removesuffix("s")) if noun.endswith("s") else None
            is_other_quantity = count in stated and noun.removesuffix("s") not in stated[count]
            if count is not None and rows and count > rows and not is_other_quantity:
                found.append(f"{match.group(0)} (the table holds {rows})")

    return found


# Each time unit by the words a text writes it with
_TIME_UNITS = {"millisecond": ("ms", "msec", "millisecond", "milliseconds"),
               "second": ("s", "sec", "secs", "second", "seconds"),
               "minute": ("min", "mins", "minute", "minutes"),
               "hour": ("h", "hr", "hrs", "hour", "hours"),
               "day": ("d", "day", "days")}
_UNIT_OF = {word: unit for unit, words in _TIME_UNITS.items() for word in words}
_NUMBER_OR_WORD = (r"\d[\d,]*(?:\.\d+)?|(?:" + "|".join(_TENS) + r")(?:-(?:" + "|".join(_UNITS[1:10]) + r"))?|"
                   + "|".join(_UNITS))
_TIMED = re.compile(r"\b(" + _NUMBER_OR_WORD + r")[\s-]?(" + "|".join(sorted(_UNIT_OF, key=len, reverse=True))
                    + r")\b", re.IGNORECASE)
# A unit named by the key a value stands under: "lifetime_seconds: 3600", "timeout_ms=30"
_KEYED = re.compile(r"(?:_|\b)(" + "|".join(sorted(_UNIT_OF, key=len, reverse=True)) + r")[\"'`]?\s*[:=]\s*[\"'`]?("
                    + _NUMBER_OR_WORD + r")\b", re.IGNORECASE)
_BARE_NUMBER = re.compile(r"(?<![\w.,])(" + _NUMBER_OR_WORD + r")(?![\w.,]\w)", re.IGNORECASE)
# Minutes written "m" inside a duration, "4m 24s" or "1h 4m": a bare "m" is no minute (it may be metres), but between
# hours and seconds it is one; "4 minutes" for a report's "4m 24s" was refused as a unit the notes never give (DEFECTS 589)
_DURATION_MINUTES = re.compile(r"\b(\d+)m\s?(?=\d+(?:\.\d+)?s\b)|\b\d+h\s?(\d+)m\b")
# A rate: a word and the time unit it is counted per ("kilometers per hour", "retries per minute")
_RATE = re.compile(r"\b([\w-]+) per (second|minute|hour|day|week|month|year)\b", re.IGNORECASE)
# How a text may give the same rate: "per hour", "/h", "an hour", "each hour", "in one hour"
_RATE_ABBREVIATIONS = {"second": "s|sec", "minute": "min", "hour": "h|hr", "day": "d", "week": "wk", "month": "mo",
                       "year": "y|yr|a"}


def _read_number_value(word: str) -> float | None:
    """A number written in digits ("3,600") or as a word ("sixty") as its value."""

    word = word.replace(",", "")

    return float(word) if word[0].isdigit() else _read_count(word)


def find_units_unstated(text: str,
                        source_text: str) -> list[str]:
    """Each number with a time unit in `text` ("60 seconds") that `source_text` (the notes it cites) holds only with
    another unit or none: a setting `--retry-budget 60`, which counts retries, written "60 seconds" (DEFECTS 516). A
    number the notes never hold is left alone: it may be worked out, and other checks read it."""

    stated_units = {}
    for match in _TIMED.finditer(source_text):
        stated_units.setdefault(_read_number_value(match.group(1)), set()).add(_UNIT_OF[match.group(2).casefold()])
    for match in _KEYED.finditer(source_text):
        stated_units.setdefault(_read_number_value(match.group(2)), set()).add(_UNIT_OF[match.group(1).casefold()])
    for match in _DURATION_MINUTES.finditer(source_text):
        stated_units.setdefault(_read_number_value(match.group(1) or match.group(2)), set()).add("minute")
    stated ={_read_number_value(match.group(1)) for match in _BARE_NUMBER.finditer(source_text)}
    found = []
    for match in _TIMED.finditer(text):
        value, unit = _read_number_value(match.group(1)), _UNIT_OF[match.group(2).casefold()]
        if value in stated and unit not in stated_units.get(value, set()):
            found.append(match.group(0))

    return found + _find_rates_unstated(text, source_text)


# A list repeats a paragraph when at least this share of its items (and two) each have this share of their words of
# four letters or more in that one paragraph: over the public runs' sections the measure named 26 of 43 lists, each a
# slide's points written as prose and again as the slide's list
_RESTATING_ITEM_SHARE = 0.6
_RESTATED_WORD_SHARE = 0.7
_LIST_WORD = re.compile(r"[A-Za-z0-9]{4,}")
_LIST_ITEM = re.compile(r"^\s*[-*+]\s+(.*)$")


def find_lists_restating_prose(paragraphs: list[dict]) -> list[tuple[str, str]]:
    """Each list block of one section whose items mostly repeat one paragraph of the same section, as (the list's
    text, the paragraph's text): a slide's points written as prose and again as its list (DEFECTS 637)."""

    # A list's lead-in and the list under it are one block as the section reads them ("The outcome was:" then the
    # items), so a list is any block's bullet lines, and the prose is every other block's lines that are no bullet
    texts = [str(block.get("text") or "") for block in paragraphs if block.get("kind") in ("paragraph", "list")]
    found = []
    for place, text in enumerate(texts):
        items = [match.group(1) for line in text.splitlines() if (match := _LIST_ITEM.match(line))]
        if len(items) < 2:
            continue
        prose = ["\n".join(line for line in other.splitlines() if not _LIST_ITEM.match(line))
                 for other_place, other in enumerate(texts) if other_place != place]
        for paragraph in [each for each in prose if each.strip()]:
            words = set(_LIST_WORD.findall(paragraph.casefold()))
            restating = [item for item in items if (item_words := _LIST_WORD.findall(item.casefold()))
                         and sum(word in words for word in item_words) >= _RESTATED_WORD_SHARE * len(item_words)]
            if len(restating) >= max(2, _RESTATING_ITEM_SHARE * len(items)):
                found.append(("\n".join(line for line in text.splitlines() if _LIST_ITEM.match(line)), paragraph))
                break

    return found


def _find_rates_unstated(text: str,
                         source_text: str) -> list[str]:
    """Each rate in `text` ("several kilometers per hour") whose word `source_text` holds but never per that time unit:
    a chapter joined the notes' "several kilometers" with the data's hourly series (DEFECTS 635; over the public runs'
    documents it named that rate alone, the notes' "50 retries in one minute" giving "per minute"). A word the notes
    never hold is left alone, as a number is above."""

    source = source_text.casefold()
    found = []
    for match in _RATE.finditer(text):
        word, unit = match.group(1).casefold(), match.group(2).casefold()
        if not re.search(rf"\b{re.escape(word)}\b", source):
            continue
        given = (rf"\b{re.escape(word)}\s*(?:per {unit}|/\s*(?:{_RATE_ABBREVIATIONS[unit]}|{unit})\b"
                 rf"|(?:an?|each|every|in (?:a|an|one)) {unit}\b)")
        if not re.search(given, source):
            found.append(match.group(0))

    return found


# A he or she the writer gave a person, where the word after it reads the same after "they": a modal, "had", "did" or
# a verb in the past ("Jonas Weber notes he will add", DEFECTS 502). "his" is always "their"; "her" can be an object
# and is left alone
_GUESSED_PRONOUN = re.compile(r"\b(he|she)(?= (?:will|would|can|could|should|may|might|must|had|did|[a-z]{2,}ed)\b)"
                              r"|\b(his)\b", re.IGNORECASE)


def write_unheard_pronouns_as_they(text: str,
                                   source_text: str) -> str:
    """`text` with each he, she or his that `source_text` (all phase 1 heard or read) never uses written as they or
    their, where the sentence reads the same: write.md forbids a he or she the recording did not use, and the owner
    removed the check that refused one, so the program writes the neutral word itself."""

    heard = {word.casefold() for word in re.findall(r"\b(?:he|she|his)\b", source_text, re.IGNORECASE)}

    def _write_they(match: re.Match) -> str:
        word = match.group(0)
        if word.casefold() in heard:
            return word
        neutral = "their" if word.casefold() == "his" else "they"

        return neutral.capitalize() if word[0].isupper() else neutral

    return _GUESSED_PRONOUN.sub(_write_they, text)


def list_role_names(texts: list[str]) -> set[str]:
    """The full names the texts give a person's role, each as written."""

    return {match.group(1) or match.group(2) for text in texts for match in _NAMED_ROLE.finditer(text)
            if match.group(1) or match.group(2)}
