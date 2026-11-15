"""One real extraction per PDF through the app's own call_model + accept_fields; records usage.

usage: eval_runner.py REPO OUT_JSON PDF [PDF ...]
"""
import json
import logging
import os
import sys
import time

repo, out_path, pdfs = sys.argv[1], sys.argv[2], sys.argv[3:]
sys.path.insert(0, repo)
os.chdir(repo)


class Capture(logging.Handler):
    records = []

    def emit(self, record):
        self.records.append(record)


cap = Capture()
lg = logging.getLogger("app.extraction")
lg.setLevel(logging.INFO)
lg.addHandler(cap)

from app import extraction  # noqa: E402

results = []
for path in pdfs:
    n_before = len(cap.records)
    t = time.time()
    entry = {"file": os.path.basename(path)}
    try:
        raw = extraction.call_model(open(path, "rb").read())
        fields, issues = extraction.accept_fields(raw)
        entry.update(raw=raw, accepted={k: (str(v) if v is not None else None) for k, v in fields.items()},
                     issues=issues, status="needs_review" if issues else "complete")
    except extraction.ExtractionError as e:
        entry.update(error=f"{e.status_code} {e.detail}")
    entry["seconds"] = round(time.time() - t, 1)
    usage = [r.args for r in cap.records[n_before:] if r.msg.startswith("Extraction call")]
    if usage:
        model, stop, tin, tout, rid = usage[-1]
        entry.update(model=model, stop_reason=stop, input_tokens=tin, output_tokens=tout, request_id=rid,
                     est_cost_usd=round(tin * 4e-6 + tout * 20e-6, 4))
    errs = [r.getMessage() for r in cap.records[n_before:] if r.levelno >= logging.WARNING]
    if errs:
        entry["log_errors"] = errs
    results.append(entry)
    print(f"{entry['file']}: {entry.get('status', entry.get('error'))} "
          f"in {entry['seconds']}s, tokens {entry.get('input_tokens')}/{entry.get('output_tokens')}", flush=True)

json.dump(results, open(out_path, "w"), indent=2)
print("total est cost: $%.4f" % sum(r.get("est_cost_usd", 0) for r in results))
