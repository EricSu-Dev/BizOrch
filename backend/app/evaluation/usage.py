"""Thread-safe live-evaluation usage limits and provider callbacks."""

from dataclasses import dataclass
from datetime import timedelta
from threading import Lock
from time import monotonic


class EvaluationLimitExceededError(RuntimeError):
    """A live run reached a server-owned call, token or duration limit."""


@dataclass(frozen=True, slots=True)
class EvaluationUsageLimits:
    max_calls: int = 40
    max_input_tokens: int = 200_000
    max_output_tokens: int = 50_000
    max_embedding_texts: int = 100
    max_run_duration: timedelta = timedelta(minutes=20)

    def __post_init__(self) -> None:
        if (
            self.max_calls < 1
            or self.max_input_tokens < 1
            or self.max_output_tokens < 1
            or self.max_embedding_texts < 1
            or self.max_run_duration <= timedelta(0)
        ):
            raise ValueError("evaluation usage limits must be positive")


@dataclass(frozen=True, slots=True)
class EvaluationUsageSnapshot:
    call_count: int
    input_token_count: int
    output_token_count: int
    embedding_text_count: int

    def delta(self, earlier: "EvaluationUsageSnapshot") -> "EvaluationUsageSnapshot":
        return EvaluationUsageSnapshot(
            call_count=self.call_count - earlier.call_count,
            input_token_count=self.input_token_count - earlier.input_token_count,
            output_token_count=self.output_token_count - earlier.output_token_count,
            embedding_text_count=(
                self.embedding_text_count - earlier.embedding_text_count
            ),
        )


class EvaluationUsageGuard:
    """Reserve external calls before I/O and reject bounded usage overruns."""

    def __init__(
        self,
        limits: EvaluationUsageLimits = EvaluationUsageLimits(),
        *,
        monotonic_clock=monotonic,
    ) -> None:
        self._limits = limits
        self._monotonic = monotonic_clock
        self._started_at = monotonic_clock()
        self._lock = Lock()
        self._calls = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._embedding_texts = 0

    def check_deadline(self) -> None:
        if self._monotonic() - self._started_at > self._limits.max_run_duration.total_seconds():
            raise EvaluationLimitExceededError("evaluation run duration limit exceeded")

    def before_model_call(self) -> None:
        self._reserve_call(embedding_texts=0)

    def after_model_call(self, *, input_tokens: int, output_tokens: int) -> None:
        self._record_tokens(input_tokens=input_tokens, output_tokens=output_tokens)

    def before_embedding_call(self, *, text_count: int) -> None:
        self._reserve_call(embedding_texts=text_count)

    def after_embedding_call(self, *, input_tokens: int) -> None:
        self._record_tokens(input_tokens=input_tokens, output_tokens=0)

    def snapshot(self) -> EvaluationUsageSnapshot:
        with self._lock:
            return EvaluationUsageSnapshot(
                call_count=self._calls,
                input_token_count=self._input_tokens,
                output_token_count=self._output_tokens,
                embedding_text_count=self._embedding_texts,
            )

    def reset(
        self,
        *,
        call_count: int = 0,
        input_token_count: int = 0,
        output_token_count: int = 0,
        embedding_text_count: int = 0,
        elapsed_seconds: float = 0,
    ) -> None:
        values = (
            call_count,
            input_token_count,
            output_token_count,
            embedding_text_count,
        )
        if any(value < 0 for value in values):
            raise ValueError("initial evaluation usage must not be negative")
        if elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must not be negative")
        with self._lock:
            self._calls = call_count
            self._input_tokens = input_token_count
            self._output_tokens = output_token_count
            self._embedding_texts = embedding_text_count
            self._started_at = self._monotonic() - elapsed_seconds
        self._assert_current_totals()

    def _reserve_call(self, *, embedding_texts: int) -> None:
        self.check_deadline()
        if embedding_texts < 0:
            raise ValueError("embedding_texts must not be negative")
        with self._lock:
            if self._calls + 1 > self._limits.max_calls:
                raise EvaluationLimitExceededError("evaluation call limit exceeded")
            if (
                self._embedding_texts + embedding_texts
                > self._limits.max_embedding_texts
            ):
                raise EvaluationLimitExceededError(
                    "evaluation embedding text limit exceeded"
                )
            self._calls += 1
            self._embedding_texts += embedding_texts

    def _record_tokens(self, *, input_tokens: int, output_tokens: int) -> None:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("provider token counts must not be negative")
        with self._lock:
            if self._input_tokens + input_tokens > self._limits.max_input_tokens:
                raise EvaluationLimitExceededError(
                    "evaluation input token limit exceeded"
                )
            if self._output_tokens + output_tokens > self._limits.max_output_tokens:
                raise EvaluationLimitExceededError(
                    "evaluation output token limit exceeded"
                )
            self._input_tokens += input_tokens
            self._output_tokens += output_tokens

    def _assert_current_totals(self) -> None:
        with self._lock:
            if (
                self._calls > self._limits.max_calls
                or self._input_tokens > self._limits.max_input_tokens
                or self._output_tokens > self._limits.max_output_tokens
                or self._embedding_texts > self._limits.max_embedding_texts
            ):
                raise EvaluationLimitExceededError(
                    "persisted evaluation usage already exceeds configured limits"
                )
