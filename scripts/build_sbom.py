from __future__ import annotations

import importlib.metadata
import json
import re
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "packaging" / "sbom.spdx.json"


def _spdx_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]+", "-", value).strip("-")


def _license_id(metadata: importlib.metadata.PackageMetadata) -> str:
    expression = metadata.get("License-Expression")
    if expression:
        return expression
    value = (metadata.get("License") or "").strip().lower()
    if value.startswith("apache"):
        return "Apache-2.0"
    if value.startswith("mit"):
        return "MIT"
    if value in {"bsd-3-clause", "bsd 3-clause", "bsd"}:
        return "NOASSERTION"
    if "affero" in value and ("gpl 3" in value or "general public license version 3" in value):
        return "AGPL-3.0-only"
    if value.startswith(("bsd 3-clause", "bsd-3-clause")):
        return "BSD-3-Clause"
    return "NOASSERTION"


def build_document() -> dict:
    packages = []
    for distribution in sorted(
        importlib.metadata.distributions(),
        key=lambda item: (item.metadata.get("Name") or "").casefold(),
    ):
        name = distribution.metadata.get("Name")
        version = distribution.version
        if not name or not version:
            continue
        packages.append(
            {
                "name": name,
                "SPDXID": f"SPDXRef-Package-{_spdx_id(name)}-{_spdx_id(version)}",
                "versionInfo": version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": _license_id(distribution.metadata),
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": (
                            "pkg:pypi/" + re.sub(r"[-_.]+", "-", name).lower() + "@" + version
                        ),
                    }
                ],
            }
        )
    created = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "Newsroom Windows build environment",
        "documentNamespace": f"https://channelpress.local/sbom/{created}",
        "creationInfo": {
            "created": created,
            "creators": ["Tool: Newsroom scripts/build_sbom.py"],
        },
        "packages": packages,
    }


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    document = build_document()
    OUTPUT.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({len(document['packages'])} distributions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
