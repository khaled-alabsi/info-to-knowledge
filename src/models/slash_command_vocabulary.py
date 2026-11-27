"""The slash commands the terminal UI knows, with what each does, and the options they take."""

from __future__ import annotations

__all__ = ["SLASH_COMMANDS", "VALUE_OPTIONS", "FLAG_OPTIONS"]

# name -> (what to type, what it does). The order is the order `/help` prints.
SLASH_COMMANDS = {
    "inbox": ("/inbox", "list the videos in the inbox"),
    "process": ("/process all | /process 1 3 [--from N] [--to N] [--phase N] [--dry-run]",
                "queue a run for every new inbox video, or for the numbered ones"),
    "trash": ("/trash 1 3 [--yes]", "move the numbered inbox videos to inbox/.trash; their runs stay"),
    "restore": ("/restore <name>", "move a video or meeting folder from inbox/.trash back into the inbox"),
    "archive": ("/archive 1 3", "move the numbered inbox videos to inbox/.archive; their runs stay and stay listed"),
    "unarchive": ("/unarchive <name>", "move a video or meeting folder from inbox/.archive back into the inbox"),
    "runs": ("/runs", "list the run folders"),
    "open": ("/open <run folder>", "show a run folder's phases, frames and log"),
    "resume": ("/resume [--dry-run]", "continue the open run where it stopped"),
    "from": ("/from N [--to N] [--dry-run]", "run the open run's video again from phase N"),
    "phase": ("/phase N [--dry-run]", "run phase N alone on the open run"),
    "worker": ("/worker N [N ...]", "ask phase 3 again for the named frames of the open run"),
    "pause": ("/pause", "pause the open run at its next check"),
    "drain": ("/drain", "let the open run finish its current frame, then stop"),
    "stop": ("/stop", "stop the open run at its next check"),
    "continue": ("/continue", "remove a pause or drain signal"),
    "interrupt": ("/interrupt", "interrupt the open run's running job, as Ctrl-C would"),
    "reset-to": ("/reset-to N [--yes]", "list, or with --yes remove, every phase of the open run after N"),
    "reset-chapter": ("/reset-chapter N [--yes]", "list, or with --yes remove, chapter N and every later one"),
    "queue": ("/queue", "list the job queue"),
    "cancel": ("/cancel <job id>", "take a queued job off the queue"),
    "models": ("/models", "which model each phase calls"),
    "probe": ("/probe", "are the model hosts reachable"),
    "help": ("/help", "list these commands"),
    "quit": ("/quit", "leave the terminal UI; runs keep going"),
}
VALUE_OPTIONS = ("from", "to", "phase")
FLAG_OPTIONS = ("dry-run", "yes")
