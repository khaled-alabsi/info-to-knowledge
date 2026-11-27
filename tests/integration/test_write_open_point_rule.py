"""38c3-openpv's chapter 1 (DEFECTS 630) ended on "> **Open:** The exact timeline for resolving missing regional data in
certain states is not specified [F23.1]", where F23.1 says only that the prototype runs on the user's device; over the
public runs' 15 open points, it and one more rest on no note. Through
the real prompt builder on the real instruction files: the chapter writer's system prompt carries the rule."""

from support import harness

from prompts import system_prompt_builder


def test_the_chapter_writer_s_prompt_carries_the_open_point_rule_defect():
    # the writer built an open point from what no note says
    prompt = " ".join(system_prompt_builder.build_system_prompt("write").split())
    harness.check("the open point's rule reaches the chapter writer",
                  "An open point rests on a note that leaves the point open" in prompt
                  and "never write one from what no note says" in prompt, "")
