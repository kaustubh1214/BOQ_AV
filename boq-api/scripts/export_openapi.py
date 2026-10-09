"""Writes openapi.json (the API contract) next to boq-api/. Used to generate the Dart client for Flutter:

python scripts/export_openapi.py
openapi-generator generate -i openapi.json -g dart-dio -o ../flutter_client
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402

out = Path(__file__).resolve().parents[1] / "openapi.json"
out.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
print(f"wrote {out} ({len(app.openapi()['paths'])} paths)")
