"""What the UI's services refuse, each with the reason a person reads in the page or the terminal."""

from __future__ import annotations

__all__ = ["UploadRefusedError", "DuplicateRecordingError", "UnknownRunFolderError", "UnknownJobError",
           "SlashCommandError", "ServerAnswerError", "UnknownArtifactError", "UnknownInboxItemError",
           "UnreadableOriginsError", "SettingPathError", "SettingsFileError", "AccountError"]


class UploadRefusedError(ValueError):
    """An uploaded file the inbox would never process: not a video, or no name."""


class DuplicateRecordingError(UploadRefusedError):
    """An uploaded recording the inbox holds or a done run processed already, found by its SHA-256. `duplicate` is
    `recording_hash_service.describe_duplicate`'s record of it: its `kind`, what it is the `same_as`, and its `run`."""

    def __init__(self,
                 message: str,
                 duplicate: dict):
        super().__init__(message)
        self.duplicate = duplicate


class UnknownRunFolderError(LookupError):
    """A run folder that is not directly inside `output/` or `knowledge/`."""


class UnknownJobError(LookupError):
    """A job id the queue does not hold."""


class SlashCommandError(ValueError):
    """A line in the terminal UI that is no slash command it knows, or one missing a value."""


class ServerAnswerError(RuntimeError):
    """The UI server answered a terminal UI call with an error; the message is the server's `detail`."""


class UnknownArtifactError(LookupError):
    """A run folder file the UI does not serve: outside the folder, missing, or program state such as a `.json`."""


class UnknownInboxItemError(LookupError):
    """A path the UI asked to move that is no video the inbox lists."""


class UnreadableOriginsError(UnknownInboxItemError):
    """A place's record of where its entries came from that does not read: a move is refused before it would write over
    it, the way a move of an unknown item is answered."""


class UnreadableTagsError(UnknownInboxItemError):
    """The record of the tags given to inbox videos that does not read: a write is refused before it would save a store
    holding one video over the rest."""


class UnreadableRunTagsError(ValueError):
    """A run folder's `run-tags.json` that does not read: a change of the tags is refused before it would save the new
    tags over what the file held."""


class SettingPathError(ValueError):
    """A setting's key that runs through a part of the file that holds a value (`part`), not a section."""

    def __init__(self,
                 part: str):
        super().__init__(f"{part} holds a value")
        self.part = part


class SettingsFileError(ValueError):
    """A settings file on disk that does not read as a JSON object: it is never written over."""


class AccountError(ValueError):
    """An account change the accounts refuse: a name taken or unknown, an unknown role, an empty name or password."""
