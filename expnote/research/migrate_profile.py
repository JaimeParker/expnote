"""The Off2On profile note written by `migrate profile` (design 3.4 and 11.1).

The text is data (`migrate_data/off2on-profile.md`), not code: it is the parity copy's
profile extended with the optional keys the write layer reads (`images.dir`,
`templates.*`, `labels.remark_prefix`, `split.*`) and a `migration` section holding the
Chinese words the one-shot migration looks for.
"""

from __future__ import annotations

from pathlib import Path

DATA_DIR = Path(__file__).parent / "migrate_data"


def read_data(name: str) -> str:
    return (DATA_DIR / name).read_text(encoding="utf-8")


OFF2ON_PROFILE_TEXT = read_data("off2on-profile.md")
