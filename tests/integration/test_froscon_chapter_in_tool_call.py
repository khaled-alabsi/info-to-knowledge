"""The system prompt phase 4 builds for a chapter of release test 1's froscon 2024 talk 3147: 327 of 336 public chapter
replies wrote the chapter as their own text and again in the tool call, and two of 3147's repeated their ends until the
loop test stopped them, about 16 minutes each (DEFECTS 386). The prompt now says the chapter goes only in the call."""

from support import harness

from prompts import system_prompt_builder


def test_the_talk_s_chapter_prompt_says_where_the_chapter_goes_defect():
    # The instruction said only "Return `markdown`", never where the chapter is not written
    prompt = " ".join(system_prompt_builder.build_system_prompt("write", {}).split())
    harness.check("the prompt says to write the chapter only in the tool call",
                  "Write the chapter only as the `markdown` of your `check_chapter` or `submit_chapter` call" in prompt,
                  prompt[:200])
