import json
from pathlib import Path

DATASET_PATH = Path(__file__).parent / "datasets" / "groundedness_cases.jsonl"


def load_cases(case_type: str = None, path: Path = DATASET_PATH) -> list:
    """
    Load golden-dataset cases from a JSONL file, one JSON object per line.
    Args:
        case_type: if given, only return cases whose "case_type" matches
            (e.g. "answer_groundedness" or "sql_generation").
        path: override the dataset file (defaults to datasets/groundedness_cases.jsonl).
    """

    cases = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            case = json.loads(line)
            if case_type is None or case.get("case_type") == case_type:
                cases.append(case)

    return cases

print(load_cases("answer_groundedness"))

