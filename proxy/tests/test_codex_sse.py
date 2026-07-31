import json

from proxy.codex_sse import assistant_text_stream, function_call_stream
from proxy.json_types import JSONDict

# Codex deserialises `usage` into a struct whose fields are not optional: an
# empty object fails the whole stream with
# "failed to parse ResponseCompleted: missing field `input_tokens`".
TOKEN_USAGE_FIELDS = {
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
}


def _payload(chunk: bytes) -> dict[str, object]:
    decoded: dict[str, object] = json.loads(chunk.removeprefix(b"data: "))
    return decoded


def _completed_usage(chunks: list[bytes]) -> dict[str, object]:
    response = _payload(chunks[-1])["response"]
    assert isinstance(response, dict)
    usage: dict[str, object] = response["usage"]
    return usage


def test_stream_ends_with_the_mandatory_completed_event() -> None:
    chunks = list(assistant_text_stream(text='{"outcome":"allow"}', response_id="resp_1"))

    assert _payload(chunks[-1])["type"] == "response.completed"


def test_text_is_carried_by_a_done_output_item() -> None:
    chunks = list(assistant_text_stream(text='{"outcome":"allow"}', response_id="resp_1"))

    carried = [json.dumps(_payload(c)) for c in chunks if _payload(c)["type"] == "response.output_item.done"]

    assert '{\\"outcome\\":\\"allow\\"}' in carried[0]


def test_completed_event_declares_every_token_usage_field() -> None:
    chunks = list(assistant_text_stream(text="whatever", response_id="resp_1"))

    assert TOKEN_USAGE_FIELDS <= set(_completed_usage(chunks))


def test_a_verdict_produced_without_model_consumes_no_input_token() -> None:
    chunks = list(assistant_text_stream(text="whatever", response_id="resp_1"))

    assert _completed_usage(chunks)["input_tokens"] == 0


def test_every_chunk_is_a_sse_data_frame() -> None:
    chunks = list(assistant_text_stream(text="whatever", response_id="resp_1"))

    assert all(c.startswith(b"data: ") and c.endswith(b"\n\n") for c in chunks)


def _done_item(chunks: list[bytes]) -> JSONDict:
    for chunk in chunks:
        payload = _payload(chunk)
        if payload["type"] == "response.output_item.done":
            item = payload["item"]
            assert isinstance(item, dict)
            return item
    raise AssertionError("the stream carries no done item")


def _call_stream() -> list[bytes]:
    return list(
        function_call_stream(
            name="exec_command",
            arguments={"cmd": "git status --porcelain"},
            call_id="call_1",
            response_id="resp_1",
        )
    )


def test_the_done_item_is_a_function_call() -> None:
    assert _done_item(_call_stream())["type"] == "function_call"


def test_the_tool_name_is_carried() -> None:
    assert _done_item(_call_stream())["name"] == "exec_command"


def test_arguments_are_carried_as_a_json_string() -> None:
    """Codex reads `arguments` as a string it parses itself, never as an object."""
    arguments = _done_item(_call_stream())["arguments"]
    assert isinstance(arguments, str)


def test_the_carried_arguments_survive_a_round_trip() -> None:
    arguments = _done_item(_call_stream())["arguments"]
    assert isinstance(arguments, str)

    assert json.loads(arguments) == {"cmd": "git status --porcelain"}


def test_the_call_id_is_carried() -> None:
    """Codex pairs the eventual output back to the call through this id."""
    assert _done_item(_call_stream())["call_id"] == "call_1"


def test_a_function_call_stream_ends_with_the_mandatory_completed_event() -> None:
    assert _payload(_call_stream()[-1])["type"] == "response.completed"


def test_a_function_call_stream_declares_every_token_usage_field() -> None:
    assert TOKEN_USAGE_FIELDS <= set(_completed_usage(_call_stream()))
