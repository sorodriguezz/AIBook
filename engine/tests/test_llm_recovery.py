from types import SimpleNamespace

import httpx
import pytest

from folio_engine.llm import client


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [True, False])
async def test_model_retries_transient_failure_without_returning_untranslated_text(monkeypatch, recover):
    calls = []

    async def no_delay(_):
        pass

    def response(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            raise httpx.ConnectError("connection reset", request=request)
        if len(calls) == 2 or not recover:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Texto traducido."}}]})

    monkeypatch.setattr(client, "asyncio", SimpleNamespace(sleep=no_delay))
    llm = client.LLMClient("http://127.0.0.1:9999")
    await llm.close()
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as llm._http:
        if recover:
            assert (await llm.chat([])).text == "Texto traducido."
        else:
            with pytest.raises(client.LLMUnavailable):
                await llm.chat([])
    assert len(calls) == 3
