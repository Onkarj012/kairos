"""Sealed-test vault helpers."""

import json
from pathlib import Path

def run_sealed_test(selection_path, result_path, evaluator=None, **kwargs):
    selection_path = Path(selection_path)
    result_path = Path(result_path)
    if result_path.exists():
        raise ValueError("sealed test result already exists; holdout can be spent once")
    selection = json.loads(selection_path.read_text(encoding="utf-8")) if selection_path.exists() else {}
    result = evaluator(selection) if evaluator else {"passed": False, "reason": "no_evaluator"}
    payload = {"selection": selection, "sealed_test": result, "spent_once": True}
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload
