import asyncio
from functools import partial

import httpx

from folio_engine.llm import server_manager as sm


def test_generation_rate_from_metrics(monkeypatch):
    samples = iter([(100, 10.0), (400, 20.0)])

    def handler(request):
        tokens, seconds = next(samples)
        body = (f"# HELP x\nllamacpp:tokens_predicted_total {tokens}\n"
                f"llamacpp:tokens_predicted_seconds_total {seconds}\n")
        return httpx.Response(200, text=body)

    monkeypatch.setattr(sm.httpx, "AsyncClient", partial(httpx.AsyncClient, transport=httpx.MockTransport(handler)))
    mgr = sm.LlamaServerManager()
    monkeypatch.setattr(sm.LlamaServerManager, "running", property(lambda self: True))
    mgr.port = 1
    assert asyncio.run(mgr.generation_rate()) == 10.0  # lifetime average on first sample
    mgr._gen_sample = (mgr._gen_sample[0] - 5, *mgr._gen_sample[1:])
    assert asyncio.run(mgr.generation_rate()) == 0.7 * 10 + 0.3 * 30  # 300 tok / 10 s
