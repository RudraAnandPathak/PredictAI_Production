"""Entry point of the isolated extraction process: python -I extract_main.py <format> <input> <output.json> <max_pages>.
Reads the file, extracts, writes JSON, exits 0; on a known failure writes {"error": {...}} and exits 0; any other failure exits non-zero."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract as ex  # noqa: E402  (sibling module; this process must not import the application)


def main() -> int:
    fmt, src, dst, pages = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
    with open(src, "rb") as f:
        data = f.read()
    try:
        result = {"ok": ex.extract(fmt, data, max_pages=pages, base_url=os.environ.get("RI_BASE_URL", "")).to_json()}
    except ex.ExtractError as e:
        result = {"error": {"code": e.code, "message": e.message}}
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(result, f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
