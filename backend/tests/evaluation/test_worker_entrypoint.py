from threading import Event

from run_evaluation_worker import run_loop


class _FakeWorker:
    def __init__(self, values: list[str | None]) -> None:
        self._values = iter(values)

    def run_once(self) -> str | None:
        return next(self._values)


def test_evaluation_worker_entrypoint_runs_one_claim_and_exits(capsys) -> None:
    run_loop(
        _FakeWorker(["run-1"]),
        poll_interval_seconds=1,
        stop_event=Event(),
        once=True,
    )

    assert capsys.readouterr().out == '{"run_id": "run-1"}\n'


def test_evaluation_worker_entrypoint_exits_when_queue_is_idle(capsys) -> None:
    run_loop(
        _FakeWorker([None]),
        poll_interval_seconds=1,
        stop_event=Event(),
        once=True,
    )

    assert capsys.readouterr().out == ""
