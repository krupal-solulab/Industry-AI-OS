"""Export the gateway's GraphQL schema to SDL. Run: python deploy/scripts/export_graphql_sdl.py

Publishes docs/api/graphql.schema.graphql for the frontend track.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "services/gateway/src"))

from gateway.graphql_schema import schema  # noqa: E402

out = pathlib.Path(__file__).resolve().parents[2] / "docs/api/graphql.schema.graphql"
out.write_text(schema.as_str(), encoding="utf-8")
print(f"wrote {out}")
