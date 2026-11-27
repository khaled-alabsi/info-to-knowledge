"""Release test 1's froscon 2024 talk 3147, frame 120: its notes' build step line summed up "a password prompt
overlay", and the program hid the headset's name on the row under it as "[secret]" (DEFECTS 345). Frame 120's real
outline lines, the name as frame 119's notes quote it, through the real secret hiding."""

from support import harness

from utils import document_text_utils

_OUTLINE = (
    "- step 2 of the screen of frame 119: shows an updated Impala window with a password prompt overlay and an updated "
    "Paired Devices row in Bluetui\n"
    "  - dialog box \"Enter the password\": overlaid in the center of the Impala panel, containing a filled text field "
    "showing \"temere282\"\n"
    "  - data row update in Bluetui \"Paired Devices\": \"Bose NC 700 HP dm\" row now shows \"68%\" and a battery icon "
    "next to the connected status")


def test_the_talk_s_headset_name_under_the_step_line_is_kept_defect():
    hidden = document_text_utils.hide_secret_tokens(_OUTLINE)
    harness.check("the headset's name stays", "Bose NC 700 HP dm" in hidden, hidden)
