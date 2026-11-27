"""The terminal UI: the inbox, the run folders and the job queue on the left, the open run on the right, and a
line at the bottom that takes slash commands.

EVERY BUTTON IS A SLASH COMMAND. A click builds the same `SlashCommandRequest` the typed line would,
and one handler per command name carries it out, so the mouse and the keyboard cannot drift apart.
Everything goes through the server's API: the terminal UI starts no run of its own.
"""

from __future__ import annotations

import dataclasses
from urllib.parse import quote

from rich.text import Text
from textual.app import App
from textual.containers import Horizontal, Vertical
from textual.suggester import SuggestFromList
from textual.widget import Widget
from textual.widgets import (Button, Checkbox, DataTable, Footer, Header, Input, RichLog, Select, SelectionList,
                             Static, TabbedContent, TabPane)

from models import slash_command_vocabulary
from models import output_contract
from models.run_job import JOB_QUEUED, JOB_RUNNING
from models.slash_command_request import SlashCommandRequest
from models.ui_errors import ServerAnswerError, SlashCommandError
from services import ui_api_client
from utils import slash_command_utils

__all__ = ["ConsoleApp"]


def _name_run_folder(row: dict) -> str:
    """A run folder as the Runs table keys it and `/open` takes it: `<root>/<name>`."""

    return f"{row['root']}/{row['name']}"


def _build_run_path(run: dict) -> str:
    """The API path of one run folder, its root and name quoted: a name holding `#` or `?` reached another address."""

    return f"/runs/{quote(run['root'], safe='')}/{quote(run['name'], safe='')}"


_REFRESH_RUN_S = 1.0
_REFRESH_LISTS_S = 3.0
# The two refreshes, each of whose server failures is said once
_LISTS_REFRESH, _OPEN_RUN_REFRESH = "lists", "open run"
# The lines each log panel keeps
_LOG_MAX_LINES = 20_000
_QUEUED, _RUNNING = JOB_QUEUED, JOB_RUNNING
# The inbox states the commands read
_NEW, _DONE = "new", "done"

# button id -> the slash command it stands for, and where its words come from: the ticked inbox videos (`selected`),
# the word `all`, the phase chosen beside the buttons (`phase`), or none
_BUTTON_COMMANDS = {
    "process-selected": ("process", "selected"), "process-all": ("process", "all"), "resume": ("resume", None),
    "run-from": ("from", "phase"), "run-phase": ("phase", "phase"), "pause": ("pause", None), "drain": ("drain", None),
    "stop": ("stop", None), "continue": ("continue", None), "interrupt": ("interrupt", None),
    "reset-to": ("reset-to", "phase"),
}

_CSS = """
#left { width: 45%; }
#right { width: 55%; }
#run-detail { height: auto; padding: 0 1; border: round $accent; }
#inbox-buttons, #run-buttons, #phase-buttons { height: auto; }
#run-buttons Button, #phase-buttons Button, #inbox-buttons Button { min-width: 8; margin: 0 1 0 0; }
#phase-select { width: 18; }
#command { dock: bottom; }
"""


class ConsoleApp(App):
    """`./i2k tui`, talking to the server at `base_url`."""

    CSS = _CSS
    TITLE = "i2k"
    BINDINGS = [("ctrl+q", "quit", "quit")]

    def __init__(self,
                 base_url: str):
        super().__init__()
        self.base_url = base_url
        self.inbox_items: list[dict] = []
        self.run_folders: list[dict] = []
        self.jobs: list[dict] = []
        self.open_run: dict | None = None
        self.log_path = ""
        self.log_offset = 0
        # The last server failure said of each refresh (the lists, the open run), so one that lasts is said once
        self.server_errors: dict[str, str] = {}

    # -- layout -----------------------------------------------------------------------------------

    def _build_layout(self) -> list[Widget]:
        """The two columns and the command line; the tabs are filled once they are mounted."""

        run_buttons = [Button(label, id=button_id) for label, button_id in (
            ("Resume", "resume"), ("Pause", "pause"), ("Drain", "drain"), ("Stop", "stop"),
            ("Continue", "continue"), ("Interrupt", "interrupt"))]
        phase_select = Select([(f"{number} {row['phase']}", number)
                               for number, row in sorted(output_contract.PHASE_FILES.items())],
                              prompt="phase", id="phase-select")
        right = Vertical(Static("Open a run from the Runs tab, or type /open <folder>.", id="run-detail"),
                         Horizontal(*run_buttons, id="run-buttons"),
                         Horizontal(phase_select, Button("Run from", id="run-from"),
                                    Button("Run only", id="run-phase"),
                                    Button("Reset to (list)", id="reset-to", variant="warning"), id="phase-buttons"),
                         TabbedContent(id="outputs"), id="right")
        command = Input(placeholder="/help lists the commands", id="command",
                        suggester=SuggestFromList(slash_command_utils.list_suggestions(), case_sensitive=False))

        return [Header(), Horizontal(Vertical(TabbedContent(id="lists"), id="left"), right), command, Footer()]

    async def on_mount(self) -> None:
        """Build the screen, fill the lists, and keep them and the open run current."""

        await self.mount_all(self._build_layout())
        lists = self.query_one("#lists", TabbedContent)
        await lists.add_pane(TabPane("Inbox", SelectionList(id="inbox"),
                                     Horizontal(Button("Process selected", id="process-selected", variant="primary"),
                                                Button("Process all new", id="process-all"),
                                                Checkbox("Dry run", id="dry-run"), id="inbox-buttons"),
                                     id="tab-inbox"))
        await lists.add_pane(TabPane("Runs", DataTable(id="runs", cursor_type="row"), id="tab-runs"))
        await lists.add_pane(TabPane("Queue", DataTable(id="queue", cursor_type="row"), id="tab-queue"))
        outputs = self.query_one("#outputs", TabbedContent)
        await outputs.add_pane(TabPane("Run log", RichLog(id="log", wrap=True, max_lines=_LOG_MAX_LINES), id="tab-log"))
        await outputs.add_pane(TabPane("Messages", RichLog(id="messages", wrap=True, max_lines=_LOG_MAX_LINES), id="tab-messages"))
        self.query_one("#runs", DataTable).add_columns("Folder", "Where", "Status", "Live", "Run")
        self.query_one("#queue", DataTable).add_columns("Job", "Video", "Status", "Exit", "Queued")
        await self.reload_lists()
        self.set_interval(_REFRESH_LISTS_S, self.reload_lists)
        self.set_interval(_REFRESH_RUN_S, self.reload_open_run)
        self.query_one("#command", Input).focus()

    # -- refreshing -------------------------------------------------------------------------------

    def _print_message(self,
                       text: str,
                       is_error: bool = False) -> None:
        self.query_one("#messages", RichLog).write(Text(text, style="red" if is_error else ""))
        if is_error:
            self.notify(text, severity="error")

    def _report_server_error(self,
                             source: str,
                             exc: Exception | None) -> None:
        """Say a failure of one refresh once while it lasts, and once that the server answers it again (`exc` None)."""

        text = "" if exc is None else f"server: {exc}"
        if text == self.server_errors.get(source, ""):
            return
        if text:
            self._print_message(text, is_error=True)
        elif self.server_errors.get(source):
            self._print_message("server: answering again")
        self.server_errors[source] = text

    def _fill_table(self,
                    selector: str,
                    rows: list[tuple],
                    keys: list[str]) -> None:
        table = self.query_one(selector, DataTable)
        cursor = table.cursor_row
        # The cursor stays on the row it was on, found by its key, when that row is still listed
        cursor_key = table.coordinate_to_cell_key((cursor, 0)).row_key.value if table.row_count else None
        table.clear()
        for row, key in zip(rows, keys):
            table.add_row(*row, key=key)
        if rows:
            table.move_cursor(row=keys.index(cursor_key) if cursor_key in keys else min(cursor, len(rows) - 1))

    async def reload_lists(self) -> None:
        """Read the inbox, the run folders and the queue again."""

        # STEP 1: the three lists, kept only when all three came, so the stored lists are always the ones on screen
        try:
            inbox_items = await ui_api_client.fetch_json(self.base_url, "/inbox")
            run_folders = await ui_api_client.fetch_json(self.base_url, "/runs")
            jobs = await ui_api_client.fetch_json(self.base_url, "/jobs")
        except ServerAnswerError as exc:
            self._report_server_error(_LISTS_REFRESH, exc)
            return
        self._report_server_error(_LISTS_REFRESH, None)
        inbox = self.query_one("#inbox", SelectionList)
        ticked_videos = {self.inbox_items[number - 1]["video"] for number in inbox.selected
                         if 1 <= number <= len(self.inbox_items)}
        self.inbox_items, self.run_folders, self.jobs = inbox_items, run_folders, jobs

        # STEP 2: the inbox, each ticked video still ticked wherever it is listed now
        inbox.clear_options()
        for position, item in enumerate(self.inbox_items, start=1):
            inbox.add_option((f"{position}. {item['name']}  ({item['state']})", position,
                              item["video"] in ticked_videos))

        # STEP 3: the run folders and the queue
        self._fill_table("#runs", [(row["name"], row["root"], row["status"], "yes" if row["is_live"] else "",
                                    row["run_id"]) for row in self.run_folders],
                         [_name_run_folder(row) for row in self.run_folders])
        # The queue shows what waits, then what ended; a running job is in Runs, under its run folder.
        listed = ([job for job in self.jobs if job["status"] == _QUEUED]
                  + [job for job in reversed(self.jobs) if job["status"] not in (_QUEUED, _RUNNING)])
        self._fill_table("#queue", [(job["job_id"], job["request"]["video"].rsplit("/", 1)[-1], job["status"],
                                     "" if job["exit_code"] is None else str(job["exit_code"]), job["queued_at"])
                                    for job in listed],
                         [job["job_id"] for job in listed])

    def _render_detail(self,
                       detail: dict) -> Text:
        phases = "  ".join(f"{row['number']} {row['name']}: {row['status']}" for row in detail["phases"])
        frames = "  ".join(f"{status} {count}" for status, count in detail["frames"].items() if count)
        live = f"running, pid {', '.join(map(str, detail['live_pids']))}" if detail["live_pids"] else "no process"

        return Text.assemble((_name_run_folder(detail), "bold"), f"  {detail['status']}  ({live})"
                + (f"  signal: {detail['signal']}" if detail["signal"] else "")
                + f"\nvideo: {detail['video'] or 'not recorded'}\nphases: {phases}\nframes: {frames or 'none yet'}"
                + f"\nlog: {detail['log'] or 'none yet'}")

    async def reload_open_run(self) -> None:
        """Read the open run's state and the log lines added since the last read."""

        if self.open_run is None:
            return
        path = _build_run_path(self.open_run)
        try:
            detail = await ui_api_client.fetch_json(self.base_url, path)
            if detail["log"] != self.log_path:
                self.log_path, self.log_offset = detail["log"], 0
                self.query_one("#log", RichLog).clear()
            chunk = await ui_api_client.fetch_json(self.base_url, f"{path}/log", {"offset": self.log_offset})
        except ServerAnswerError as exc:
            self._report_server_error(_OPEN_RUN_REFRESH, exc)
            return
        self._report_server_error(_OPEN_RUN_REFRESH, None)
        self.open_run = detail
        self.log_offset = chunk["offset"]
        if chunk["text"]:
            self.query_one("#log", RichLog).write(Text(chunk["text"].rstrip("\n")))
        self.query_one("#run-detail", Static).update(self._render_detail(detail))

    # -- input ------------------------------------------------------------------------------------

    async def on_input_submitted(self,
                                 event: Input.Submitted) -> None:
        """A typed line: parse it and carry it out."""

        event.input.value = ""
        if not event.value.strip():
            return
        try:
            request = slash_command_utils.parse_slash_command(event.value)
        except SlashCommandError as exc:
            self._print_message(str(exc), is_error=True)
            return
        await self.run_slash_command(request)

    async def on_button_pressed(self,
                                event: Button.Pressed) -> None:
        """A click: the slash command the button stands for."""

        if (event.button.id or "") not in _BUTTON_COMMANDS:
            return
        name, word_source = _BUTTON_COMMANDS[event.button.id]
        phase = self.query_one("#phase-select", Select).value
        words = {"selected": tuple(str(n) for n in self.query_one("#inbox", SelectionList).selected), "all": ("all",),
                 "phase": (str(phase),) if isinstance(phase, int) else ()}.get(word_source, ())
        options = {"dry-run": True} if self.query_one("#dry-run", Checkbox).value else {}
        await self.run_slash_command(SlashCommandRequest(name=name, words=words, options=options))

    async def on_data_table_row_selected(self,
                                         event: DataTable.RowSelected) -> None:
        """A click on a run folder opens it."""

        if event.data_table.id == "runs" and event.row_key.value:
            await self.run_slash_command(SlashCommandRequest(name="open", words=(event.row_key.value,)))

    # -- the commands -----------------------------------------------------------------------------

    async def run_slash_command(self,
                                request: SlashCommandRequest) -> None:
        """Carry out one slash command, typed or clicked; a refusal or a server's answer is printed, never raised, since
        Textual ends the app on an error a handler raises."""

        handler = getattr(self, f"_run_{request.name.replace('-', '_')}")
        try:
            await handler(request)
        except (SlashCommandError, ServerAnswerError) as exc:
            self._print_message(str(exc), is_error=True)

    def _parse_numbers(self,
                       request: SlashCommandRequest,
                       usage: str,
                       example: str) -> list[int]:
        """The command's words as whole numbers; refused with what it needs when one is missing or no number."""

        if not request.words or not all(word.isdigit() for word in request.words):
            raise SlashCommandError(f"/{request.name} needs {usage}: /{request.name} {example}")

        return [int(word) for word in request.words]

    def _print_operation(self,
                         answer: dict) -> None:
        """A backend operation's answer, in red when it ended with a code other than 0."""

        self._print_message(answer["output"].rstrip() or f"exit code {answer['exit_code']}",
                            is_error=answer["exit_code"] != 0)

    def _show_tab(self,
                  selector: str,
                  tab_id: str) -> None:
        self.query_one(selector, TabbedContent).active = tab_id

    def _require_open_run(self) -> dict:
        if self.open_run is None:
            raise SlashCommandError("no run is open: pick one in the Runs tab, or /open <folder>")

        return self.open_run

    async def _queue(self,
                     items: list[dict],
                     request: SlashCommandRequest,
                     job_fields: dict) -> None:
        body = {"items": items, "is_dry_run": bool(request.options.get("dry-run")),
                "from_phase": slash_command_utils.parse_phase_number(request.options.get("from")),
                "to_phase": slash_command_utils.parse_phase_number(request.options.get("to")),
                "only_phase": slash_command_utils.parse_phase_number(request.options.get("phase")), **job_fields}
        jobs = await ui_api_client.send_json(self.base_url, "/jobs", body)
        self._print_message(f"queued {len(jobs)} job(s): " + ", ".join(job["job_id"] for job in jobs))
        await self.reload_lists()

    async def _queue_open_run(self,
                              request: SlashCommandRequest,
                              job_fields: dict) -> None:
        run = self._require_open_run()
        if not run.get("video"):
            raise SlashCommandError(f"{run['name']} has no run record naming its video; process it from the inbox")
        await self._queue([{"video": run["video"], "output": run["folder"]}], request, job_fields)

    async def _queue_open_run_at_phase(self,
                                       request: SlashCommandRequest,
                                       job_field: str) -> None:
        phase = slash_command_utils.parse_phase_number(request.words[0] if request.words else None)
        if phase is None:
            raise SlashCommandError(f"/{request.name} needs a phase: /{request.name} 3")
        await self._queue_open_run(request, {job_field: phase})

    async def _run_inbox(self,
                         request: SlashCommandRequest) -> None:
        await self.reload_lists()
        self._show_tab("#lists", "tab-inbox")

    def _pick_inbox_items(self,
                          request: SlashCommandRequest) -> list[dict]:
        numbers = self._parse_numbers(request, "the numbers of the inbox videos", "1 3")
        unknown = [n for n in numbers if not 1 <= n <= len(self.inbox_items)]
        if unknown:
            raise SlashCommandError(f"no inbox video {unknown[0]}; the inbox holds {len(self.inbox_items)}")

        return [self.inbox_items[n - 1] for n in numbers]

    async def _run_process(self,
                           request: SlashCommandRequest) -> None:
        if request.words == ("all",):
            chosen = [item for item in self.inbox_items if item["state"] == _NEW]
            if not chosen:
                raise SlashCommandError("nothing to process: the inbox is empty" if not self.inbox_items
                                        else "nothing to process: every inbox video already has a run")
        else:
            chosen = self._pick_inbox_items(request)
        await self._queue([{"video": item["video"], "output": item["output"]} for item in chosen], request, {})

    async def _move_inbox_items(self,
                                path: str,
                                chosen: list[dict],
                                place: str) -> None:
        outcomes = await ui_api_client.send_json(self.base_url, path, {"videos": [item["video"] for item in chosen]})
        for outcome in outcomes:
            self._print_message(outcome["error"] or f"moved to {place} as {outcome['moved_as']}",
                                is_error=bool(outcome["error"]))
        await self.reload_lists()

    async def _move_back_to_inbox(self,
                                  path: str,
                                  request: SlashCommandRequest,
                                  place: str,
                                  done_text: str) -> None:
        if not request.words:
            raise SlashCommandError(f"/{request.name} needs a name from {place}: /{request.name} AcmeSync.mp4")
        [outcome] = await ui_api_client.send_json(self.base_url, path, {"names": [" ".join(request.words)]})
        self._print_message(outcome["error"] or done_text.format(name=outcome["restored_as"]),
                            is_error=bool(outcome["error"]))
        await self.reload_lists()

    async def _run_trash(self,
                         request: SlashCommandRequest) -> None:
        chosen = self._pick_inbox_items(request)
        unfinished = [item for item in chosen if item["state"] != _DONE]
        if unfinished and not request.options.get("yes"):
            self._print_message("This file is not processed yet. Are you sure? " if len(unfinished) == 1
                                else "These files are not processed yet. Are you sure? ", is_error=True)
            for item in unfinished:
                self._print_message(f"  {item['name']} ({item['state']})")
            self._print_message(f"/trash {' '.join(request.words)} --yes moves them; their runs stay")
            self._show_tab("#outputs", "tab-messages")
            return
        await self._move_inbox_items("/inbox/trash", chosen, "inbox/.trash")

    async def _run_restore(self,
                           request: SlashCommandRequest) -> None:
        await self._move_back_to_inbox("/inbox/trash/restore", request, "the trash", "restored {name} to the inbox")

    async def _run_archive(self,
                           request: SlashCommandRequest) -> None:
        await self._move_inbox_items("/inbox/archive", self._pick_inbox_items(request), "inbox/.archive")

    async def _run_unarchive(self,
                             request: SlashCommandRequest) -> None:
        await self._move_back_to_inbox("/inbox/archive/restore", request, "the archive", "moved {name} back into the inbox")

    async def _run_runs(self,
                        request: SlashCommandRequest) -> None:
        await self.reload_lists()
        self._show_tab("#lists", "tab-runs")

    async def _run_open(self,
                        request: SlashCommandRequest) -> None:
        wanted = " ".join(request.words)
        matches = [row for row in self.run_folders if wanted in (row["name"], _name_run_folder(row))]
        if not matches:
            raise SlashCommandError(f"no run folder {wanted!r}; /runs lists them")
        if len(matches) > 1:
            raise SlashCommandError(f"{wanted!r} is the name of " + " and of ".join(
                _name_run_folder(row) for row in matches) + ": type /open " + " or /open ".join(
                _name_run_folder(row) for row in matches))
        [match] = matches
        self.open_run, self.log_path, self.log_offset = match, "", 0
        self.server_errors.pop(_OPEN_RUN_REFRESH, None)
        self.query_one("#log", RichLog).clear()
        self._show_tab("#outputs", "tab-log")
        await self.reload_open_run()

    async def _run_resume(self,
                          request: SlashCommandRequest) -> None:
        await self._queue_open_run(request, {"is_resuming": True})

    async def _run_from(self,
                        request: SlashCommandRequest) -> None:
        await self._queue_open_run_at_phase(request, "from_phase")

    async def _run_phase(self,
                         request: SlashCommandRequest) -> None:
        await self._queue_open_run_at_phase(request, "only_phase")

    async def _run_worker(self,
                          request: SlashCommandRequest) -> None:
        await self._queue_open_run(request, {"worker_indexes": self._parse_numbers(request, "frame numbers", "12 40")})

    async def _send_signal(self,
                           signal: str) -> None:
        run = self._require_open_run()
        answer = await ui_api_client.send_json(self.base_url, f"{_build_run_path(run)}/control",
                                               {"signal": signal})
        self._print_message(f"{run['name']}: signal now {answer['signal'] or 'none'}")

    async def _run_pause(self,
                         request: SlashCommandRequest) -> None:
        await self._send_signal("pause")

    async def _run_drain(self,
                         request: SlashCommandRequest) -> None:
        await self._send_signal("drain")

    async def _run_stop(self,
                        request: SlashCommandRequest) -> None:
        await self._send_signal("stop")

    async def _run_continue(self,
                            request: SlashCommandRequest) -> None:
        await self._send_signal("continue")

    async def _run_interrupt(self,
                             request: SlashCommandRequest) -> None:
        # The open run's own job, as the buttons beside it act on the open run and the page interrupts only its job
        run = self._require_open_run()
        running = next((job for job in self.jobs if job["status"] == _RUNNING
                        and job["request"]["output_root"] == run["folder"]), None)
        if running is None:
            raise SlashCommandError(f"{run['name']} has no running job of the queue; /stop signals a run started by hand")
        await ui_api_client.send_json(self.base_url, f"/jobs/{quote(running['job_id'], safe='')}/interrupt")
        self._print_message(f"interrupted job {running['job_id']}")

    async def _reset(self,
                     request: SlashCommandRequest,
                     kind: str) -> None:
        run = self._require_open_run()
        [number] = self._parse_numbers(dataclasses.replace(request, words=request.words[:1]), "a number", "3")
        answer = await ui_api_client.send_json(
            self.base_url, f"{_build_run_path(run)}/reset",
            {"kind": kind, "number": number, "is_confirmed": bool(request.options.get("yes"))})
        self._print_operation(answer)
        if answer["exit_code"] == 0 and not request.options.get("yes"):
            self._print_message(f"nothing removed; /{request.name} {request.words[0]} --yes removes it")
        self._show_tab("#outputs", "tab-messages")

    async def _run_reset_to(self,
                            request: SlashCommandRequest) -> None:
        await self._reset(request, "phase")

    async def _run_reset_chapter(self,
                                 request: SlashCommandRequest) -> None:
        await self._reset(request, "chapter")

    async def _run_queue(self,
                         request: SlashCommandRequest) -> None:
        await self.reload_lists()
        self._show_tab("#lists", "tab-queue")

    async def _run_cancel(self,
                          request: SlashCommandRequest) -> None:
        if not request.words:
            raise SlashCommandError("/cancel needs a job id; /queue lists them")
        job = await ui_api_client.send_json(self.base_url, f"/jobs/{quote(request.words[0], safe='')}/cancel")
        self._print_message(f"job {job['job_id']}: {job['status']}")
        await self.reload_lists()

    async def _print_command_output(self,
                                    path: str) -> None:
        self._print_operation(await ui_api_client.fetch_json(self.base_url, path))
        self._show_tab("#outputs", "tab-messages")

    async def _run_models(self,
                          request: SlashCommandRequest) -> None:
        await self._print_command_output("/models")

    async def _run_probe(self,
                         request: SlashCommandRequest) -> None:
        await self._print_command_output("/probe")

    async def _run_help(self,
                        request: SlashCommandRequest) -> None:
        for usage, meaning in slash_command_vocabulary.SLASH_COMMANDS.values():
            self._print_message(f"{usage}\n    {meaning}")
        self._show_tab("#outputs", "tab-messages")

    async def _run_quit(self,
                        request: SlashCommandRequest) -> None:
        self.exit()
