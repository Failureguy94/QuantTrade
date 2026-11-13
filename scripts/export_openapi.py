"""Write the API's OpenAPI document to web/openapi.json.

The web application generates its typed client from that file (`pnpm gen:api` in
web/), so run this after changing an endpoint or schema. CI fails when the committed
file is out of date.

    python scripts/export_openapi.py            # write
    python scripts/export_openapi.py --check    # exit 1 if the file is stale
"""

import json
import sys
from pathlib import Path

from app.main import app

TARGET = Path(__file__).resolve().parent.parent / "web" / "openapi.json"


def render() -> str:
    schema = app.openapi()
    # The title comes from APP_NAME, which differs between environments.
    schema["info"]["title"] = "Quant API"
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> None:
    document = render()
    if "--check" in sys.argv:
        if not TARGET.exists() or TARGET.read_text() != document:
            sys.exit(f"{TARGET} is out of date: run python scripts/export_openapi.py")
        print(f"{TARGET} is up to date")
        return
    TARGET.write_text(document)
    print(f"wrote {TARGET}")


if __name__ == "__main__":
    main()
