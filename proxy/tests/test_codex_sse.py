import json

from proxy.codex_sse import assistant_text_stream

# Codex désérialise `usage` dans une structure dont ces champs ne sont pas
# optionnels : un objet vide fait échouer le flux entier avec
# « failed to parse ResponseCompleted: missing field `input_tokens` ».
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
    chunks = list(assistant_text_stream(text="peu importe", response_id="resp_1"))

    assert TOKEN_USAGE_FIELDS <= set(_completed_usage(chunks))


def test_a_verdict_produced_without_model_consumes_no_input_token() -> None:
    chunks = list(assistant_text_stream(text="peu importe", response_id="resp_1"))

    assert _completed_usage(chunks)["input_tokens"] == 0


def test_every_chunk_is_a_sse_data_frame() -> None:
    chunks = list(assistant_text_stream(text="peu importe", response_id="resp_1"))

    assert all(c.startswith(b"data: ") and c.endswith(b"\n\n") for c in chunks)
