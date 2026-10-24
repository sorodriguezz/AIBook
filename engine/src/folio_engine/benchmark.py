"""Measures a model's real speed on this machine with a fixed literary passage."""

import time

from .llm.client import LLMClient
from .translate import prompts
from .translate.segments import Segment, estimate_tokens

# Original passage written for Folio; dialogue, idiom and imagery on purpose.
PASSAGE = [
    "The storm had been circling Greywater for three days, like a dog that couldn't decide where to lie down.",
    "“If you’re going to sulk,” said Aunt Wenna, slamming a pot onto the stove, “do it somewhere I can’t hear you breathe.”",
    "Lio pulled his knees to his chest. He had not meant to break the mirror, and he certainly had not meant for "
    "the thing inside it to climb out, blink twice, and ask politely for a cup of tea.",
    "Outside, the bells of the old chapel began to ring, although no one had rung them in forty years, and the "
    "ravens on the fence rose all at once, as if someone had pulled a thread.",
    "“Well,” said the stranger from the mirror, smoothing a coat that was older than the house, “it seems I’ve "
    "arrived just in time for the end of the world. How fortunate. I do hate missing things.”",
]

# Average words per page in a typical novel, for the "300 pages" estimate.
TOKENS_PER_PAGE = 380


async def run(client: LLMClient, tgt: str = "es") -> dict:
    segs = [Segment(str(i), 0, "para", p) for i, p in enumerate(PASSAGE)]
    system = prompts.translator_system("en", tgt, None, [], [], "adapt")
    user = prompts.translate_user(segs, [], "", "en", tgt)
    # Warm-up: the first request pays for loading weights into GPU memory.
    await client.chat([{"role": "user", "content": "Hi"}], max_tokens=4, temperature=0)
    t0 = time.monotonic()
    comp = await client.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.3, max_tokens=700,
    )
    wall = time.monotonic() - t0
    prompt_tps = comp.prompt_tokens / comp.prompt_seconds if comp.prompt_seconds > 0 else None
    gen_tps = comp.completion_tokens / comp.gen_seconds if comp.gen_seconds > 0 else None
    src_tok = sum(estimate_tokens(p) for p in PASSAGE)
    # Seconds per source token for the full best-quality pipeline: ~2.2 generated
    # tokens per source token (draft + review) plus prompt reading.
    per_token = 0.0
    if gen_tps:
        per_token = 2.2 * 1.1 / gen_tps + (3.5 / prompt_tps if prompt_tps else 0)
    book_tokens = TOKENS_PER_PAGE * 300 * 1.3
    return {
        "prompt_tps": round(prompt_tps, 1) if prompt_tps else None,
        "gen_tps": round(gen_tps, 1) if gen_tps else None,
        "wall_seconds": round(wall, 2),
        "sample_output": comp.text[:1200],
        "source_tokens": src_tok,
        "est_hours_300_pages": round(book_tokens * per_token / 3600, 1) if per_token else None,
        "measured_at": time.time(),
    }
