"""Read bounded MinerU ZIP data without extracting or fetching referenced assets.

Only explicitly recognized schemas are inspectable. This is not a publisher of
ready parses: actual cloud geometry/asset contracts still require live samples.
"""

from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
from pathlib import PurePosixPath
import stat
import zipfile

from scholarship.adapters.providers import ContractError, integer, finite

# USER CONFIGURATION
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_MEMBERS = 2000
MAX_EXPANSION_RATIO = 200


@dataclass(frozen=True)
class BundleInspection:
    sha256: str
    members: tuple[str, ...]
    document: dict
    page_numbers: tuple[int, ...]
    producer_version: str
    publishable: bool = False


def inspect_bundle(raw: bytes, expected_pages: set[int]):
    if (
        not isinstance(raw, bytes)
        or not raw
        or len(raw) > MAX_ARCHIVE_BYTES
        or not expected_pages
        or any(type(p) is not int or not 1 <= p <= 600 for p in expected_pages)
    ):
        raise ContractError("invalid_archive_or_expected_pages")
    files = {}
    total = 0
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBERS:
                raise ContractError("too_many_archive_members")
            seen = set()
            for info in infos:
                name = info.filename
                p = PurePosixPath(name)
                if (
                    not name
                    or "\\" in name
                    or ":" in name
                    or p.is_absolute()
                    or ".." in p.parts
                    or "\x00" in name
                    or stat.S_ISLNK(info.external_attr >> 16)
                    or info.flag_bits & 1
                ):
                    raise ContractError("unsafe_archive_member")
                if p.as_posix() in seen:
                    raise ContractError("duplicate_archive_path")
                seen.add(p.as_posix())
                if info.is_dir():
                    continue
                total += info.file_size
                if (
                    info.file_size > MAX_MEMBER_BYTES
                    or total > MAX_EXPANDED_BYTES
                    or info.file_size > max(1, info.compress_size) * MAX_EXPANSION_RATIO
                ):
                    raise ContractError("archive_expansion_limit")
                with archive.open(info) as stream:
                    content = stream.read(MAX_MEMBER_BYTES + 1)
                    if len(content) != info.file_size:
                        raise ContractError("archive_size_mismatch")
                files[name] = content
    except (zipfile.BadZipFile, RuntimeError, OSError):
        raise ContractError("invalid_zip") from None
    names = [n for n in files if PurePosixPath(n).name in {"middle_json.json", "layout.json"}]
    if len(names) != 1:
        raise ContractError("ambiguous_or_missing_layout")
    try:
        doc = json.loads(files[names[0]])
        if doc.get("schema") != "docvortex.middle" or doc.get("schema_version") != "2.0":
            raise ContractError("unreviewed_cloud_schema")
        producer = doc["metadata"]["producer"]["version"]
        if not isinstance(producer, str) or not producer:
            raise ContractError("missing_producer_version")
        pages = [integer(p["page_idx"]) + 1 for p in doc["pages"]]
        if len(pages) != len(set(pages)) or set(pages) != expected_pages:
            raise ContractError("source_page_map_mismatch")
        geometry = doc["extensions"]["docvortex_layout"]
        if geometry["version"] != 1:
            raise ContractError("unreviewed_geometry_schema")
        sizes = {}
        for p in geometry["pages"]:
            n = integer(p["page_idx"]) + 1
            w = finite(p["width_pt"])
            h = finite(p["height_pt"])
            if n in sizes or w <= 0 or h <= 0:
                raise ContractError("invalid_page_geometry")
            sizes[n] = (w, h)
        if set(sizes) != expected_pages:
            raise ContractError("missing_page_geometry")
        # The authoritative raw JSON is retained. Unknown bbox/asset layouts are
        # never invented or converted to fabricated evidence coordinates.
        for page in doc["pages"]:
            if not isinstance(page["blocks"], list):
                raise ContractError("invalid_blocks")
        return BundleInspection(hashlib.sha256(raw).hexdigest(), tuple(sorted(files)), doc, tuple(pages), producer)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("invalid_layout_json") from None
