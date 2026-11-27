"""38c3-fault-injection's published document (2026-11-05): chapter 7 quotes three outputs of one exploit script, each
opening with "~/Projects/Chipolo", "> python sploit.py" and "Trying to connect. Pray the demo gods NOW!", the third
holding a line of "..." alone. The whole-document check cut the third output at that line and refused its opening
lines as a sentence said twice (DEFECTS_REPEATED_SENTENCE), and phase 4 failed after every chapter was accepted
(DEFECTS 657). The outputs' real lines in a made document, through the real document check: no repeat."""

from support import harness

from validators import document_folder_validator

BANNER = "~/Projects/Chipolo\n> python sploit.py\nTrying to connect. Pray the demo gods NOW!"
DOCUMENT = f"""# Exploiting a Bluetooth tracker

## Live demo

The exploit script initiates a BLE connection and waits for it.

```
{BANNER}
```

The script then sends the payload.

```
{BANNER}
0000fff0-0000-1000-8000-00805f9b34fb (Handle: 17): Vendor specific: 02
Sending payload
```

The overwritten callback is triggered.

```
{BANNER}
...
100%|███████████████████████████████████| 37/37 [00:43<00:00, 1.17s/it]
Overflowing pointer
Trigger vuln
```
"""


def test_the_demo_s_three_outputs_are_no_repeated_sentence_defect():
    # Defect: an output's "..." line cut it, and its opening lines read as said twice. Fix: a run of marks ends nothing
    repeated = document_folder_validator.find_repeated_document_sentences(DOCUMENT)
    harness.check("no sentence said twice", repeated == [], str(repeated))
