"""The system prompt phase 4 builds for a chapter of release test 1's froscon 2024 talk 3147: chapter 3 part 1 placed the
`.gitignore` figure under "The project is hosted at …kmon:" and tied the kernel-module viewer to the `.gitignore` with a
claim neither note made (DEFECTS 387). The prompt now bounds both."""

from support import harness

from prompts import system_prompt_builder


def test_the_talk_s_chapter_prompt_bounds_a_figure_s_lead_in_and_links_defect():
    # The instruction said nothing on what stands above a figure, nor on links between notes
    prompt = " ".join(system_prompt_builder.build_system_prompt("write", {}).split())
    harness.check("the prompt says what stands above a frame",
                  "The paragraph right above the frame is about that frame's own notes" in prompt, "")
    harness.check("the prompt forbids a link neither note states", "never a link neither states" in prompt, "")
