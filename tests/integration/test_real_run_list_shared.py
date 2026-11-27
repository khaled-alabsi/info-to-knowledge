"""The run folders of this machine's `output/` and `knowledge/`, read through the real routes: the page asked for the
runs, the inbox and the tags every 3 seconds, each route building the run list over every folder, and two open pages
kept the server's core busy, each call queuing 6 to 8 seconds beside the model run (DEFECTS 405). Only the folders'
names and the time are read; nothing a run made is printed."""

import time

from fastapi.testclient import TestClient

from support import harness

from services import run_status_service
from services import web_api_service


def test_one_refresh_of_the_page_builds_the_run_list_once_defect():
    # Each of the three list routes built the list again. Now one refresh's routes share one build
    built = []
    summarise = run_status_service._summarise_folder
    run_status_service._summarise_folder = lambda *args: built.append(args[1].name) or summarise(*args)
    run_status_service.clear_shared_run_list()
    try:
        client = TestClient(web_api_service.build_app())
        start = time.monotonic()
        answers = [client.get(f"/api/{route}") for route in ("runs", "inbox", "tags")]
        seconds = time.monotonic() - start
    finally:
        run_status_service._summarise_folder = summarise
        run_status_service.clear_shared_run_list()
    folders = len({name for name in built})
    harness.check("every route answers", all(answer.status_code == 200 for answer in answers),
                  str([answer.status_code for answer in answers]))
    harness.check("each folder is summarised once for the three routes", folders > 0 and len(built) == folders,
                  f"{len(built)} summaries of {folders} folders in {seconds:.1f} s")
