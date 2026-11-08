from types import SimpleNamespace

from app.providers.streaming import collect


def _chunk(text=None, finish=None, usage=None):
    choices = [SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=finish)]
    return SimpleNamespace(choices=choices if text or finish else [], usage=usage)


def _usage(prompt, completion):
    return SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion)


def test_글자를_이어_붙이고_마지막_사용량만_쓴다():
    reply = collect(
        [
            _chunk("가", usage=_usage(10, 1)),
            _chunk("나", usage=_usage(10, 2)),
            _chunk(finish="stop", usage=_usage(10, 3)),
        ]
    )
    assert reply.content == "가나"
    assert reply.finish_reason == "stop"
    assert (reply.prompt_tokens, reply.completion_tokens) == (10, 3)


def test_사용량이_없어도_글자는_받는다():
    reply = collect([_chunk("가")])
    assert reply.content == "가"
    assert reply.prompt_tokens is None
