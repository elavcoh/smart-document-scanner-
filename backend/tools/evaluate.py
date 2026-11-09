"""Measure unedited extraction through the running HTTP API against a manifest."""
import argparse
import json
import mimetypes
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from time import perf_counter

import requests

PROJECT = Path(__file__).resolve().parents[2]


def normalize(value: str) -> str:
    """Make spaces and Unicode consistent before comparing recognized text."""
    return unicodedata.normalize("NFC", " ".join(value.split()))


def distance(a: str, b: str) -> int:
    """Count the smallest number of character edits between two strings."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, 1):
        current = [i]
        for j, right in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left != right)))
        previous = current
    return previous[-1]


def align(expected: list[list[str]], actual: list[dict], key_columns: list[int]) -> dict[int, int]:
    """Match rows one to one in order, using a literal key or readable source evidence."""
    scores = [[0] * (len(actual) + 1) for _ in range(len(expected) + 1)]
    choices = {}
    for i, target in enumerate(expected, 1):
        reference = normalize(" ".join(target))
        for j, candidate in enumerate(actual, 1):
            cells = candidate["cells"]
            same_key = bool(key_columns) and all(
                k < len(cells) and cells[k] and normalize(cells[k]) == normalize(target[k])
                for k in key_columns
            )
            source = normalize(candidate["source_text"])
            similar = SequenceMatcher(None, reference, source, autojunk=False).ratio()
            score, step = max((scores[i - 1][j], "skip_expected"), (scores[i][j - 1], "skip_actual"))
            if same_key or similar >= 0.62:
                match = scores[i - 1][j - 1] + 1 + (1 if same_key else similar) / 1000
                if match > score:
                    score, step = match, "match"
            scores[i][j] = score
            choices[i, j] = step
    i, j = len(expected), len(actual)
    pairs = {}
    while i and j:
        step = choices[i, j]
        if step == "match":
            pairs[i - 1] = j - 1
        i -= step != "skip_actual"
        j -= step != "skip_expected"
    return pairs


def evaluate(case: dict, folder: Path, api: str, mode: str) -> dict:
    """Send one sample to the API and score the raw OCR result.

    Match rows in order before counting exact cells, missing values, and
    wrong values. Also record transcript errors, warnings, and timing.
    """
    image = folder / case["file"]
    mime = mimetypes.guess_type(image)[0] or "application/octet-stream"
    start = perf_counter()
    with image.open("rb") as source:
        response = requests.post(api + "/api/documents/process", params={"mode": mode}, files={"image": (image.name, source, mime)}, timeout=90)
    elapsed = round((perf_counter() - start) * 1000)
    result = response.json()
    actual = result.get("records", [])
    expected = case.get("rows", [])
    pairs = align(expected, actual, case.get("key_columns", []))
    correct, missing, incorrect = 0, 0, 0
    for i, row in enumerate(expected):
        if i not in pairs:
            missing += len(row)
            continue
        cells = actual[pairs[i]]["cells"]
        for column, value in enumerate(row):
            observed = (cells[column] or "") if column < len(cells) else ""
            if normalize(value) == normalize(observed):
                correct += 1
            elif not observed:
                missing += 1
            else:
                incorrect += 1
    reference = normalize(case.get("raw_text", ""))
    transcript = normalize(result.get("raw_text", ""))
    total = sum(map(len, expected))
    return {
        "file": case["file"],
        "category": case["category"],
        "http_status": response.status_code,
        "error_code": result.get("code"),
        "provider": result.get("provider"),
        "expected_rows": len(expected),
        "extracted_rows": len(actual),
        "matched_rows": len(pairs),
        "record_recall": round(len(pairs) / len(expected), 4) if expected else None,
        "unexpected_rows": len(actual) - len(pairs),
        "expected_fields": total,
        "correct_fields": correct,
        "incorrect_fields": incorrect,
        "missing_fields": missing,
        "field_accuracy": round(correct / total, 4) if total else None,
        "headings_exact": result.get("headings") == case["headings"] if "headings" in case else None,
        "ocr_cer": round(distance(reference, transcript) / len(reference), 4) if reference else None,
        "flagged_fields": sum(len({w["column"] for w in row["warnings"] if w["column"] is not None}) for row in actual),
        "warning_codes": sorted({w["code"] for w in result.get("warnings", [])}
                                | {w["code"] for row in actual for w in row["warnings"]}),
        "server_duration_ms": result.get("duration_ms"),
        "http_duration_ms": elapsed,
        "actual_headings": result.get("headings"),
        "actual_records": [row["cells"] for row in actual],
        "actual_raw_text": result.get("raw_text"),
    }


def main():
    """Read the sample list, run the selected checks, and write the report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--manifest", type=Path, default=PROJECT / "samples" / "expected.json")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--mode", choices=("default", "handwriting"), default="default")
    parser.add_argument("--only", action="append", help="Restrict to selected sample filenames.")
    args = parser.parse_args()
    api = args.url.rstrip("/")
    health = requests.get(api + "/health", timeout=10)
    health.raise_for_status()
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    cases = [case for case in data if not args.only or case["file"] in args.only]
    if not cases:
        parser.error("No selected samples were found in the manifest.")
    report = {"backend": health.json(), "cases": [evaluate(case, args.manifest.parent, api, args.mode) for case in cases]}
    content = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
