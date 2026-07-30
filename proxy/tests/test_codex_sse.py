import json

from proxy.codex_sse import assistant_text_stream


def _payload(chunk: bytes) -> dict[str, object]:
    decoded: dict[str, object] = json.loads(chunk.removeprefix(b"data: "))
    return decoded


def test_stream_ends_with_the_mandatory_completed_event() -> None:
    chunks = list(assistant_text_stream(text='{"outcome":"allow"}', response_id="resp_1"))

    assert _payload(chunks[-1])["type"] == "response.completed"


def test_text_is_carried_by_a_done_output_item() -> None:
    chunks = list(assistant_text_stream(text='{"outcome":"allow"}', response_id="resp_1"))

    carried = [json.dumps(_payload(c)) for c in chunks if _payload(c)["type"] == "response.output_item.done"]

    assert '{\\"outcome\\":\\"allow\\"}' in carried[0]


def test_every_chunk_is_a_sse_data_frame() -> None:
    chunks = list(assistant_text_stream(text="peu importe", response_id="resp_1"))

    assert all(c.startswith(b"data: ") and c.endswith(b"\n\n") for c in chunks)
