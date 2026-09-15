"""Safe tar archive validation before staging static extract (P0.3.5.5.1)."""

from __future__ import annotations

import sys
import tarfile
from pathlib import Path, PurePosixPath

ALLOWED_MEMBER_TYPES = frozenset(
    {
        tarfile.REGTYPE,
        tarfile.AREGTYPE,
        tarfile.DIRTYPE,
    }
)


class UnsafeArchiveError(ValueError):
    """Tar archive contains unsafe members."""

    code = "unsafe_archive"

    def __init__(self, reason: str, *, member: str = "") -> None:
        self.reason = reason
        self.member = member
        detail = f"unsafe_archive:{reason}"
        if member:
            detail = f"{detail}:{member}"
        super().__init__(detail)


def validate_tar_member_path(name: str) -> None:
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        raise UnsafeArchiveError("absolute_path", member=name)

    parts = PurePosixPath(normalized).parts
    if ".." in parts:
        raise UnsafeArchiveError("path_traversal", member=name)
    if any(part == "" for part in parts):
        raise UnsafeArchiveError("empty_path_segment", member=name)


def validate_tar_member(member: tarfile.TarInfo) -> None:
    if member.issym() or member.islnk():
        raise UnsafeArchiveError("symlink_or_hardlink", member=member.name)
    if member.isdev() or member.isfifo() or member.ischr() or member.isblk():
        raise UnsafeArchiveError("special_file", member=member.name)
    if member.type not in ALLOWED_MEMBER_TYPES:
        raise UnsafeArchiveError("unsupported_member_type", member=member.name)
    if not (member.isfile() or member.isdir()):
        raise UnsafeArchiveError("unsupported_member_type", member=member.name)
    validate_tar_member_path(member.name)


def validate_tar_archive(archive_path: str | Path) -> None:
    """Validate every tar entry is a safe relative file/dir only."""
    path = Path(archive_path)
    if not path.is_file():
        raise UnsafeArchiveError("archive_missing", member=str(path))

    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        if not members:
            raise UnsafeArchiveError("empty_archive")
        for member in members:
            validate_tar_member(member)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: wwc-admin-staging-archive-verify <archive.tgz>", file=sys.stderr)
        return 2
    try:
        validate_tar_archive(args[0])
    except UnsafeArchiveError as exc:
        print(f"deploy_gate_rejected: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
