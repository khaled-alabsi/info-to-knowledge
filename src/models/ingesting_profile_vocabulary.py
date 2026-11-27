"""The ingesting profiles a run's document can be written in, and the tags that pick each.

A profile is the shape of the document: what an agenda point is, and how a section lays out what the recording holds.
Every profile but the default and the one-call profiles has one instruction file,
`agent-context/base/ingesting-profile-<name>.md`, which phases 4 and 5 are given; the default, `knowledge`, is the
reference document the instructions describe on their own, and a one-call profile has its own call
(`agent-context/instructions/takeaway.md`).
"""

from __future__ import annotations

__all__ = ["DEFAULT_PROFILE", "DEFAULT_DOCUMENT_LANGUAGE", "OWN_LANGUAGE_PROFILES", "PROFILE_ALIASES", "PROFILE_DESCRIPTIONS", "PROFILES",
           "ONE_CALL_PROFILES", "MODEL_PICKABLE_PROFILES", "CHOSEN_BY", "FIXED_CHOOSERS", "PROMPT_FIELD"]

DEFAULT_PROFILE = "knowledge"
# The language a document is written in unless its profile is written in the recording's own
DEFAULT_DOCUMENT_LANGUAGE = "en"
# The profiles whose document is written in the recording's own language; every other one is written in English
OWN_LANGUAGE_PROFILES = ("language-lesson",)

# name -> the tags that pick it besides its own name, compared without letter case
PROFILE_ALIASES: dict[str, tuple[str, ...]] = {
    "knowledge": ("reference", "knowledge base"),
    "presentation": ("talk", "lecture", "webinar"),
    "conversation": ("chat", "discussion", "interview", "dialogue"),
    "meeting": ("call",),
    "language-lesson": ("learn German", "learn-german", "language lesson", "German lesson"),
    "sprint-change": ("sprint change", "sprint review", "change request"),
    "sprint-planning": ("sprint planning", "planning"),
    "takeaway": ("takeaways", "facts", "key facts"),
}

# name -> what kind of recording it is for, as phase 2's call is told when it picks one
PROFILE_DESCRIPTIONS: dict[str, str] = {
    "knowledge": "a reference document of what the recording teaches, organised by subject (the default)",
    "presentation": "one speaker, or a few in turn, teaching or showing a subject, questions at most at the end",
    "conversation": "two people or a few talking as equals: an interview, a chat, a discussion, no agenda",
    "meeting": "people deciding, planning or reporting status together, with decisions and action items",
    "language-lesson": "a teacher and learners, or a learner practising, working on a language being learned",
    "sprint-change": "changing a sprint's plan: work added, removed, moved or re-estimated, and why",
    "sprint-planning": "planning the next sprint: the backlog items discussed one by one, estimated, and committed or not",
    "takeaway": "only the facts the recording settled and the points it left open, in one fast call after the transcript",
}
# Every profile, in the order the page lists them
PROFILES = tuple(PROFILE_DESCRIPTIONS)
# The profiles whose document is ONE call over the transcript right after phase 1: no speech structuring, no
# preparation, no conversation, and phase 1 skips diarization unless `audio.takeaway.isDiarizationOn`. Such a profile is
# chosen by hand or by a tag, before phase 1, never by phase 2's call, and always takes the audio workflow.
ONE_CALL_PROFILES = ("takeaway",)
# The profiles phase 2's call of an audio recording may pick from
MODEL_PICKABLE_PROFILES = tuple(name for name in PROFILES if name not in ONE_CALL_PROFILES)

# Who chose a run's profile: the person on the run form or `run --ingesting-profile`, a tag, phase 2's model call, a
# conversion, or nobody (the default)
CHOSEN_BY = ("user", "tag", "model", "convert", "default")
# The choosers phase 2 never overrides: a profile picked by hand, or the one a conversion was made for
FIXED_CHOOSERS = ("user", "convert")

# The item field `context_map.json`'s rules read to give phases 4 and 5 the profile's file
PROMPT_FIELD = "ingesting_profile"
