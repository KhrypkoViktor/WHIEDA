import json
import sys
from pathlib import Path


SUMMARY_NODES = [
    "Code: Normalize Payload",
    "Code: Merge Envelope + Session",
    "Code: Validate Dify Response",
    "Postgres: Write Review Queue",
]


def is_ref_string(value, graph_length):
    return isinstance(value, str) and value.isdigit() and 0 <= int(value) < graph_length


def decode_graph(graph):
    memo = {}
    in_progress = set()

    def resolve_value(value):
        if is_ref_string(value, len(graph)):
            return resolve_index(int(value))
        if isinstance(value, list):
            return [resolve_value(item) for item in value]
        if isinstance(value, dict):
            return {key: resolve_value(item) for key, item in value.items()}
        return value

    def resolve_index(index):
        if index in memo:
            return memo[index]
        if index in in_progress:
            return f"[Circular:{index}]"
        in_progress.add(index)
        resolved = resolve_value(graph[index])
        memo[index] = resolved
        in_progress.remove(index)
        return resolved

    return resolve_index(0)


def repair_string(value):
    if not isinstance(value, str):
        return value
    if not any(marker in value for marker in ("Ã", "Ð", "Ñ", "Â", "вЂ")):
        return value
    for src, dst in (("latin1", "utf-8"), ("cp1252", "utf-8")):
        try:
            repaired = value.encode(src).decode(dst)
            if repaired:
                return repaired
        except Exception:
            continue
    return value


def repair_strings(value):
    if isinstance(value, dict):
        return {repair_strings(key): repair_strings(item) for key, item in value.items()}
    if isinstance(value, list):
        return [repair_strings(item) for item in value]
    if isinstance(value, str):
        return repair_string(value)
    return value


def get_latest_json(run_data, node_name):
    runs = run_data.get(node_name)
    if not isinstance(runs, list) or not runs:
        return None
    last_run = runs[-1]
    main = (((last_run or {}).get("data") or {}).get("main"))
    if not isinstance(main, list) or not main or not isinstance(main[0], list) or not main[0]:
        return None
    last_item = main[0][-1]
    if not isinstance(last_item, dict):
        return None
    return last_item.get("json")


def extract_encoded_graph(raw_text):
    marker = '"storedAt" : "db", "data" : "'
    start = raw_text.find(marker)
    if start < 0:
        raise ValueError("db data marker not found")
    start += len(marker)

    end = raw_text.rfind(']"}}')
    if end < 0:
        raise ValueError("db data end marker not found")

    payload = raw_text[start : end + 1]
    stage1 = payload.encode("utf-8").decode("unicode_escape")
    stage2 = stage1.encode("utf-8").decode("unicode_escape")
    return stage2


def build_summary(decoded):
    run_data = (((decoded or {}).get("resultData") or {}).get("runData")) or {}
    return {
        "normalize_payload": get_latest_json(run_data, SUMMARY_NODES[0]),
        "merge_envelope_session": get_latest_json(run_data, SUMMARY_NODES[1]),
        "validate_dify_response": get_latest_json(run_data, SUMMARY_NODES[2]),
        "write_review_queue": get_latest_json(run_data, SUMMARY_NODES[3]),
    }


def main():
    if len(sys.argv) < 2:
        print("usage: decode_db_execution.py <execution_file> [output_file]")
        return 1

    input_path = Path(sys.argv[1])
    output_path = (
        Path(sys.argv[2])
        if len(sys.argv) > 2
        else input_path.with_name(input_path.stem + ".decoded-summary.json")
    )

    raw_text = input_path.read_text(encoding="utf-8-sig")
    graph = json.loads(extract_encoded_graph(raw_text))
    decoded = repair_strings(decode_graph(graph))
    summary = build_summary(decoded)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
