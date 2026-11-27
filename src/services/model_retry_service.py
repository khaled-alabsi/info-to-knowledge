# <!-- sync: twin/services.model_retry_service v1 -->
"""Retrying ONE model request when the transport drops, without losing the attempt around it.

A local model host closes sockets. It happens on the longest generations, which are also the ones
that cost the most to redo: measured twice in one day, once 27 minutes in. The exception reached the
single `except` that wraps a worker attempt, and everything the attempt had built - its reads, its
writes, its reasoning - went with it, for a failure that had nothing to do with the work.

What is retried is the REQUEST, not the attempt: the same messages go back to the host and the
session continues from the reply. That is only safe because a model call has no side effect of its
own - the tools it asks for run after the reply comes back, so a request that never returned changed
nothing.

**Only transport failures.** A refused schema, a bad argument, a tool error: those are answers, and
repeating them wastes the budget the operator gave the item. The test is the SHAPE of the failure -
a connection that dropped, was refused, timed out, or a host-side 5xx - never the class of the
exception, because every provider spells it in its own library.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = ["RetrySettings", "call_with_transport_retry", "looks_like_transport_failure",
           "looks_like_host_down"]

# THE HOST IS NOT THERE AT ALL - a socket refused, not a socket dropped. Included in the transport markers
# below, and separated because it deserves a different amount of patience: a connection that dropped
# mid-generation means a busy host that will answer in seconds, while a refused connection means
# nothing is listening, which is an operator restarting the host, a machine waking up, or a model
# server reloading - minutes, not seconds.
_HOST_DOWN_MARKERS = (
    "connection refused",
    "all connection attempts failed",
    "connecterror",
    "name or service not known",
    "nodename nor servname provided",
    "failed to establish a new connection",
    # What the Ollama client raises when its server is not running
    "failed to connect to",
)


# The words the local hosts and their clients actually use, lowercased. Matched against the whole
# rendered exception because the type is provider-specific: `ollama.ResponseError`, `httpx`
# transport errors and the OpenAI SDK's own connection error carry the same sentences.
_TRANSPORT_MARKERS = (
    *_HOST_DOWN_MARKERS,
    "unexpected eof",
    "eoferror",
    "connection reset",
    "connection error",
    "connection aborted",
    "broken pipe",
    "remote end closed",
    "server disconnected",
    "timed out",
    "temporarily unavailable",
    # A CRASHED HOST CAN ANSWER 400, which reads as "your request was wrong" and is not: LM Studio
    # returns it when the backend falls over mid-reply. The status code cannot tell the two apart,
    # so these match the BODY - a genuine malformed request says none of them, and retrying one of
    # those forever is exactly what the status code exists to prevent.
    "the model has crashed",
    "fatal exception in the backend",
    "error in iterating prediction stream",
    # A HOST CAN ALSO REJECT THE MODEL'S OWN OUTPUT. Ollama parses a tool call against the model's
    # chat template, and a sample that does not fit comes back as a ResponseError with no usable
    # status code - the request was never the problem, and the next sample is usually fine.
    # Measured live: 2m37s of generation ended on "expected element type <function> but have <parameter>",
    # nothing matched here, and the attempt died on it.
    "expected element type",
    "status code: 500",
    "status code: 502",
    "status code: 503",
    "status code: 504",
)


def looks_like_transport_failure(error: BaseException) -> bool:
    """Whether this exception is the connection dropping rather than the model answering."""

    return any(marker in f"{type(error).__name__}: {error}".lower() for marker in _TRANSPORT_MARKERS)


def looks_like_host_down(error: BaseException) -> bool:
    """Whether nothing is listening at all, as opposed to a connection that dropped. The error's causes are read too:
    a client library wraps a refused socket in its own "Connection error.", and a restarting host was taken for a
    dropped call and given fifteen seconds instead of the long wait."""

    chain: list[BaseException] = []
    while error is not None and error not in chain:
        chain.append(error)
        error = error.__cause__ or error.__context__

    return any(marker in f"{type(link).__name__}: {link}".lower() for link in chain for marker in _HOST_DOWN_MARKERS)


@dataclass(frozen=True)
class RetrySettings:
    """How many times a dropped request is sent again, and how long the pauses are. `sleep` is a
    field rather than a hidden call so a test asserts the pause it was ASKED for instead
    of waiting it out, and `on_retry` is how the run log gets a line for every retry that
    happens - a retry nobody can see is indistinguishable from a slow host."""

    attempts: int = 3
    backoff_s: float = 5.0
    # WAITING OUT A HOST THAT IS NOT RUNNING, which the three attempts above cannot do: they spend
    # about fifteen seconds and then let the exception end the attempt. Measured - the
    # model host was stopped to free the GPU and not restarted, and the run burned three of one
    # item's five attempts in seventy-five seconds, each on a socket that was refused before a single
    # token was generated. Nothing was lost in those attempts because nothing had happened in them,
    # which is the whole argument for waiting instead: a refused connection costs only time.
    #
    # Bounded, not infinite. A host nobody is going to restart must eventually be reported, and the
    # attempt deadline is not a substitute - it is checked between model calls, and this waits inside
    # one. Twelve tries at 15s doubling to a 120s ceiling is roughly twenty minutes.
    host_down_attempts: int = 12
    host_down_backoff_s: float = 15.0
    host_down_max_backoff_s: float = 120.0
    sleep: Callable[[float], None] = field(default=time.sleep)
    on_retry: Callable[[int, BaseException, float], None] | None = None


def call_with_transport_retry(call: Callable[[], Any],
                              settings: RetrySettings) -> Any:
    """`call()`, retried while it fails on the transport. The last failure is raised as it stands."""

    if settings.attempts < 1:
        raise ValueError("attempts must be at least 1")
    # THE BUDGET IS CHOSEN BY THE FIRST FAILURE, not fixed before it. Which of the two kinds this is
    # cannot be known until something fails, and re-deciding on every failure would let a host that
    # flips between refusing and dropping reset its own budget forever.
    attempts, delay, ceiling = settings.attempts, float(settings.backoff_s), None
    decided = False
    attempt = 0
    while True:
        attempt += 1
        try:
            return call()
        except Exception as error:                        # noqa: BLE001 - re-raised unless transport
            if not looks_like_transport_failure(error):
                raise
            if not decided:
                decided = True
                # `attempts == 1` is how the operator switches retrying off, and a longer wait is
                # still a retry - so the long budget extends retrying, it never reintroduces it.
                if settings.attempts > 1 and looks_like_host_down(error):
                    attempts = max(settings.attempts, int(settings.host_down_attempts))
                    delay = float(settings.host_down_backoff_s)
                    ceiling = float(settings.host_down_max_backoff_s)
            if attempt >= attempts:
                raise
            if settings.on_retry is not None:
                settings.on_retry(attempt, error, delay)
            settings.sleep(delay)
            # Growing, because a host that just dropped a socket is usually still busy reloading the
            # model it dropped it over: retrying immediately meets the same wall.
            delay *= 2
            if ceiling is not None:
                # Capped for the long wait only: an unbounded doubling over twelve tries would put
                # the last pause hours away, long past the point where the answer stops being useful.
                delay = min(delay, ceiling)
