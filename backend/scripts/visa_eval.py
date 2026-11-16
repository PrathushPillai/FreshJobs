"""Score the visa signals against hand-labelled postings (data/visa_eval.jsonl, 145 real For-you postings labelled on
2026-11-15: 68 rule out sponsorship, 28 say they sponsor, 8 unclear, 41 say nothing).

    python scripts/visa_eval.py                     # stored flags and today's rules (free)
    python scripts/visa_eval.py --ai MODEL [--ai MODEL2]   # also the AI visa reader (costs money; needs ANTHROPIC_API_KEY)

Two questions matter to a user: "does this job rule me out?" (no_sponsor) and "does it say it sponsors?"
(sponsors_text). Each is scored by precision (of the jobs flagged, how many are right) and recall (of the jobs that
should be flagged, how many are). For the AI, the four-way sponsorship answer and OPT/CPT are scored too.
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.pipeline import visa_reader  # noqa: E402
from app.pipeline.visa_signals import derive  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data" / "visa_eval.jsonl"


def rules_out(gold: dict) -> bool:
    return gold["sponsorship"] == "no" or gold["us_persons_only"] or gold["clearance_required"] or gold["opt_cpt"] == "excluded"


def pr(pairs: list[tuple[bool, bool]]) -> str:
    tp = sum(1 for want, got in pairs if want and got)
    fp = sum(1 for want, got in pairs if got and not want)
    fn = sum(1 for want, got in pairs if want and not got)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return f"precision {precision:6.1%} ({tp}/{tp + fp})   recall {recall:6.1%} ({tp}/{tp + fn})"


def report(name: str, items: list[dict], verdicts: list[dict]) -> None:
    print(f"\n== {name}")
    print("  rules out sponsorship:", pr([(rules_out(x["gold"]), v["no_sponsor"]) for x, v in zip(items, verdicts)]))
    print("  says it sponsors:     ", pr([(x["gold"]["sponsorship"] == "yes", v["sponsors_text"]) for x, v in zip(items, verdicts)]))
    opt = [(x["gold"]["opt_cpt"], v.get("student_visa") or "not_mentioned") for x, v in zip(items, verdicts)]
    print(f"  OPT/CPT right:         {sum(a == b for a, b in opt)}/{len(opt)}")
    misses = [(x, v) for x, v in zip(items, verdicts)
              if rules_out(x["gold"]) != v["no_sponsor"] or (x["gold"]["sponsorship"] == "yes") != v["sponsors_text"]]
    for x, v in misses[:12]:
        print(f"    miss: {x['title'][:40]} @ {x['company'][:25]}: gold {x['gold']['sponsorship']}, "
              f"got no_sponsor={v['no_sponsor']} sponsors={v['sponsors_text']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai", action="append", default=[], metavar="MODEL", help="a Claude model (paid)")
    ap.add_argument("--groq", action="append", default=[], metavar="MODEL", help="a Groq model (free tier)")
    args = ap.parse_args()
    items = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"{len(items)} hand-labelled postings")

    report("stored flags (as saved in the database)", items,
           [{"no_sponsor": x["stored"]["no_sponsor"], "sponsors_text": x["stored"]["sponsors_text"],
             "student_visa": x["stored"]["student_visa"]} for x in items])
    rules = [derive(x["passage"], "") for x in items]
    report("today's rules", items, rules)

    for model in args.groq:
        import httpx

        t = time.time()
        results, tokens, failed = [], 0, 0
        with httpx.Client() as http:
            for i, x in enumerate(items):
                for attempt in range(6):
                    try:
                        v, used = visa_reader.groq_read(http, x["title"], x["company"], x["passage"], model)
                        tokens += used
                        break
                    except httpx.HTTPStatusError as e:
                        if e.response.status_code != 429 or attempt == 5:
                            print("   error:", e.response.status_code, e.response.text[:150])
                            v = None
                            break
                        time.sleep(float(e.response.headers.get("retry-after", "20")) + 1)
                failed += v is None
                results.append(v)
                time.sleep(visa_reader.GROQ_SPACING_SECONDS)
                if i % 25 == 24:
                    print(f"   {i + 1} read, {tokens:,} tokens", flush=True)
        report(f"rules + Groq {model}", items, [visa_reader.overlay(r, v) for r, v in zip(rules, results)])
        report(f"Groq {model} alone", items, [visa_reader.overlay(
            {"no_sponsor": False, "sponsors_text": False, "student_visa": None}, v) for v in results])
        four = sum(1 for x, v in zip(items, results) if v and v["sponsorship"] == x["gold"]["sponsorship"])
        print(f"  four-way sponsorship answer right: {four}/{len(items)}; failed reads: {failed}; "
              f"{tokens:,} tokens ({tokens // max(1, len(items))} per posting); {time.time() - t:.0f} s")
        (DATA.parent / f"visa_eval_groq_{model.replace('/', '_')}.json").write_text(json.dumps(
            [{"id": x["id"], "verdict": v} for x, v in zip(items, results)], indent=1))

    for model in args.ai:
        t = time.time()

        def read(x, model=model):
            for attempt in range(4):
                try:
                    message = visa_reader.client().messages.create(
                        **visa_reader.request_params(x["title"], x["company"], x["passage"], model))
                    return visa_reader.parse_message(message), message.usage
                except Exception as e:  # noqa: BLE001  (an eval run reports and keeps going)
                    if attempt == 3:
                        print("   error:", type(e).__name__, str(e)[:120])
                        return None, None
                    time.sleep(2 * (attempt + 1))

        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(read, items))
        verdicts = [visa_reader.overlay(r, v) for r, (v, _) in zip(rules, results)]
        report(f"rules + AI reader ({model})", items, verdicts)
        ai_only = [visa_reader.overlay({"no_sponsor": False, "sponsors_text": False, "student_visa": None}, v)
                   for v, _ in results]
        report(f"AI reader alone ({model})", items, ai_only)
        four = sum(1 for x, (v, _) in zip(items, results) if v and v["sponsorship"] == x["gold"]["sponsorship"])
        failed = sum(1 for v, _ in results if v is None)
        tokens_in = sum((u.input_tokens + (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0))
                        for _, u in results if u)
        tokens_out = sum(u.output_tokens for _, u in results if u)
        print(f"  four-way sponsorship answer right: {four}/{len(items)}; failed reads: {failed}")
        print(f"  tokens: {tokens_in:,} in, {tokens_out:,} out ({tokens_out / max(1, len(items)):.0f} out per posting); "
              f"{time.time() - t:.0f} s")


if __name__ == "__main__":
    main()
