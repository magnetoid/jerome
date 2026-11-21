"""Run the proofreading passes on seeded-error text and score them.

    uv run python -m eval.run --language en --passes languagetool ai consistency \\
        --max-cost 3 --seed 1

Prints the cost estimate and asks before spending anything. Results go to eval/results/
(gitignored).
"""

import argparse
import json
import random
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from eval.fetch_texts import HERE, load_paragraphs, load_sources
from eval.inject import InjectedError, inject_errors
from eval.score import score
from jerome.config import get_settings
from jerome.jobs.estimate import ALL_PASSES, ai_cost_range
from jerome.models import Registry, build_registry, cost
from jerome.models.base import BilledError
from jerome.proofread.ai_pass import chunk_blocks, run_ai_pass
from jerome.proofread.consistency import consistency_candidates
from jerome.proofread.languagetool import LanguageToolClient, languagetool_candidates
from jerome.proofread.merge import merge_candidates
from jerome.types import DEFAULT_QUOTES, BlockText, Candidate, Category, StyleSheet, Variant

RESULTS = HERE / "results"


def build_sample(
    paragraphs: list[str],
    language: Literal["en", "de"],
    *,
    seed: int,
    per_category: int,
    clean: int,
) -> tuple[list[str], list[InjectedError]]:
    """Inject errors into the whole text, then keep the changed paragraphs, ``clean``
    untouched ones, and a few untouched paragraphs that show the majority spelling of every
    injected consistency error. Truth indices refer to the returned sample."""
    out, truth = inject_errors(paragraphs, language, seed=seed, per_category=per_category)
    rng = random.Random(seed)
    touched = {t.paragraph for t in truth}
    untouched = [i for i in range(len(out)) if i not in touched]
    keep = set(touched)
    keep.update(rng.sample(untouched, min(clean, len(untouched))))
    for t in truth:
        if t.category != Category.CONSISTENCY:
            continue
        form = t.correct.lower()
        context = [i for i in untouched if form in out[i].lower()]
        keep.update(context[:2])
    order = sorted(keep)
    new_index = {old: new for new, old in enumerate(order)}
    sample = [out[i] for i in order]
    remapped = [
        InjectedError(new_index[t.paragraph], t.start, t.end, t.wrong, t.correct, t.category)
        for t in truth
    ]
    return sample, remapped


def run_eval(
    paragraphs: Sequence[str],
    truth: Sequence[InjectedError],
    *,
    language: Literal["en", "de"],
    variant: Variant,
    passes: Sequence[str],
    registry: Registry,
    lt_client: Any,
    max_cost: Decimal,
) -> dict:
    """Run the passes directly on the paragraphs (block ids are their indices) and score
    them. Refuses to start when the estimate's high total is above ``max_cost``, and stops
    sending AI chunks once the actual cost reaches it."""
    style = StyleSheet(variant, DEFAULT_QUOTES[variant], {}, [])
    blocks = [BlockText(i, p) for i, p in enumerate(paragraphs)]
    block_ids = [b.id for b in blocks]
    chunks = chunk_blocks(blocks)
    estimate = ai_cost_range(registry, style, chunks) if "ai" in passes else None
    if estimate is not None and estimate.high > max_cost:
        raise ValueError(f"estimate {estimate.high} is above the max cost {max_cost}")

    per_pass: dict[str, list[Candidate]] = {}
    spent = Decimal(0)
    stopped = False
    model = None
    ai_errors = 0
    if "languagetool" in passes:
        per_pass["languagetool"], _ = languagetool_candidates(blocks, style, lt_client)
    if "ai" in passes:
        resolved = registry.for_pass("ai", local_only=False)
        per_pass["ai"] = []
        if resolved is not None:
            provider, cfg = resolved
            model = cfg.model
            price = registry.price(cfg.model)
            for chunk in chunks:
                if spent >= max_cost:
                    stopped = True
                    break
                try:
                    res = run_ai_pass(chunk, style, provider, cfg.model, cfg.effort)
                except BilledError as exc:  # refusal or unusable output: billed, no candidates
                    ai_errors += 1
                    if exc.usage is not None:
                        spent += cost(price, exc.usage)
                    continue
                spent += cost(price, res.result.usage)
                per_pass["ai"] += res.candidates
    if "consistency" in passes:
        per_pass["consistency"] = consistency_candidates(blocks, style)

    merged = merge_candidates([c for found in per_pass.values() for c in found])

    def scored(found: Sequence[Candidate]) -> dict:
        return score(found, truth, block_ids, paragraphs=paragraphs)

    return {
        "language": language,
        "variant": variant,
        "passes": list(passes),
        "model": model,
        "paragraphs": len(paragraphs),
        "injected": len(truth),
        "estimate": None
        if estimate is None
        else {"low": str(estimate.low), "high": str(estimate.high)},
        "cost": str(spent),
        "stopped_at_cost_cap": stopped,
        "ai_errors": ai_errors,
        "scores": {
            "merged": scored(merged),
            "passes": {name: scored(found) for name, found in per_pass.items()},
        },
    }


def markdown(result: dict) -> str:
    lines = [
        f"# Seeded-error evaluation ({result['language']}, {result['variant']})",
        "",
        f"- model: {result['model']}",
        f"- paragraphs: {result['paragraphs']}, injected errors: {result['injected']}",
        f"- cost: {result['cost']} (stopped at cost cap: {result['stopped_at_cost_cap']})",
        "",
        "| scope | key | precision | recall |",
        "|---|---|---|---|",
    ]
    scopes = [("merged", result["scores"]["merged"])]
    scopes += list(result["scores"]["passes"].items())
    for scope, table in scopes:
        for key, v in table.items():
            lines.append(f"| {scope} | {key} | {v['precision']:.3f} | {v['recall']:.3f} |")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=["en", "de"], required=True)
    parser.add_argument("--passes", nargs="+", choices=ALL_PASSES, default=list(ALL_PASSES))
    parser.add_argument("--max-cost", type=Decimal, required=True)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--per-category", type=int, default=10)
    parser.add_argument("--clean", type=int, default=40)
    parser.add_argument("--source", type=int, help="Gutenberg id (default: the first listed)")
    args = parser.parse_args(argv)

    entries = load_sources()[args.language]
    entry = next((e for e in entries if e["id"] == args.source), entries[0])
    paragraphs = load_paragraphs(entry["id"])
    sample, truth = build_sample(
        paragraphs,
        args.language,
        seed=args.seed,
        per_category=args.per_category,
        clean=args.clean,
    )
    settings = get_settings()
    registry = build_registry(settings)
    lt = LanguageToolClient(settings.languagetool_url)
    variant: Variant = entry["variant"]
    style = StyleSheet(variant, DEFAULT_QUOTES[variant], {}, [])
    blocks = [BlockText(i, p) for i, p in enumerate(sample)]
    estimate = ai_cost_range(registry, style, chunk_blocks(blocks)) if "ai" in args.passes else None
    if estimate is None:
        print("Estimated cost: 0 (no AI pass)")
    else:
        print(f"Estimated cost: {estimate.low} to {estimate.high} USD")
        if estimate.high > args.max_cost:
            print(f"Refusing: estimate is above --max-cost {args.max_cost}", file=sys.stderr)
            return 2
    if input("Run? [y/N] ").strip().lower() != "y":
        print("Aborted.")
        return 1

    result = run_eval(
        sample,
        truth,
        language=args.language,
        variant=variant,
        passes=args.passes,
        registry=registry,
        lt_client=lt,
        max_cost=args.max_cost,
    )
    result["args"] = {
        "language": args.language,
        "passes": args.passes,
        "max_cost": str(args.max_cost),
        "seed": args.seed,
        "per_category": args.per_category,
        "clean": args.clean,
        "source": entry["id"],
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base: Path = RESULTS / stamp
    base.with_suffix(".json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    base.with_suffix(".md").write_text(markdown(result), encoding="utf-8")
    print(f"Wrote {base}.json and .md; cost {result['cost']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
