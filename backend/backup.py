"""File-level backups of the planner database.

Backup naming and pruning live here — not in the CLI or TUI — so every entry
point writes and prunes identically. A backup is the database file copied to
a sibling named ``<name>.<YYYYMMDDHHMMSS>``, with a ``.N`` sequence appended
when two backups land within the same second. Pruning only ever considers
files matching that exact shape: a manual copy like ``planner.db.backup`` or
an unrelated ``planner.db.2`` sitting next to the database is never deleted.
"""

from __future__ import annotations

import glob
import re
import shutil
from datetime import datetime
from pathlib import Path

# Files the tool itself produced: ``<db name>.<14-digit timestamp>[.N]``.
# Anything else — manual copies, similarly named files — must never be pruned.
# The ``.N`` suffix is the same-second collision sequence from backup_db_file.
# ASCII digits only: backup_db_file writes strftime output (ASCII), and \d
# alone would also match Unicode digit shapes no tool file can carry.
_BACKUP_SUFFIX_RE = re.compile(r"\.[0-9]{14}(?:\.[0-9]+)?$")


def is_backup_file(db_path: Path, path: Path) -> bool:
    """Check whether ``path`` is a backup ``backup_db_file`` would have written.

    Args:
        db_path: Path to the SQLite database file.
        path: Candidate path next to it.
    Returns:
        bool: True when the name matches the backup shape exactly.
    """
    name = db_path.name
    return (path.is_file()
            and path.name.startswith(name + ".")
            and _BACKUP_SUFFIX_RE.fullmatch(path.name[len(name):]) is not None)


def backup_db_file(db_path: Path) -> Path:
    """Copy ``db_path`` to a timestamped sibling file and return its path.

    Single source for the backup naming used by ``--rotate-backup``,
    ``plan backup``, the pre-import safety backup, and the TUI. When the
    timestamped name already exists (two backups within the same second) a
    ``.N`` sequence is appended rather than silently overwriting the earlier
    backup.

    Args:
        db_path: Path to the SQLite database file.
    Returns:
        Path of the newly written backup.
    Raises:
        OSError: if the copy fails.
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = db_path.with_suffix(f"{db_path.suffix}.{timestamp}")
    n = 1
    while backup_path.exists():
        backup_path = db_path.with_suffix(f"{db_path.suffix}.{timestamp}.{n}")
        n += 1
    shutil.copy2(db_path, backup_path)
    return backup_path


def _backup_order_key(path: Path, db_name: str) -> tuple[int, int]:
    """Creation-embedded sort key for a backup file: (timestamp, collision n).

    The name encodes creation order, so sorting is deterministic and immune
    to file mtimes (which copy2 borrows from the database, and which tie for
    same-second ``.N`` collisions).

    Args:
        path: Backup path next to the database.
        db_name: The database file's name (the shared prefix).
    Returns:
        tuple[int, int]: (the YYYYMMDDHHMMSS timestamp, the collision sequence
        n; a bare <ts> file means n = 0).
    """
    parts = path.name[len(db_name):].split(".")
    return int(parts[1]), int(parts[2]) if len(parts) > 2 else 0


def prune_backups(db_path: Path, keep: int) -> list[Path]:
    """Delete the oldest backups for ``db_path`` beyond ``keep``.

    Only files matching the backup naming (:func:`is_backup_file`) are
    candidates, so unrelated files next to the database are never touched.
    The ``keep`` most recent backups — by the creation order embedded in
    their names, not file mtimes — survive; call this *after*
    :func:`backup_db_file` so the backup just taken always counts.

    Args:
        db_path: Path to the SQLite database file.
        keep: Number of most recent backups to keep (0 prunes all backups;
            negative values are treated the same as 0 by the backend, but the
            CLI rejects them before calling).
    Returns:
        list of the deleted paths, most recent pruned candidate first
        (matching the embedded-timestamp ordering used to choose them).
    Raises:
        OSError: if a candidate cannot be removed.
    """
    candidates = [
        p for p in db_path.parent.glob(f"{glob.escape(db_path.name)}.*")
        if is_backup_file(db_path, p)]
    candidates.sort(key=lambda p: _backup_order_key(p, db_path.name),
                    reverse=True)
    pruned = candidates[max(keep, 0):]
    for old in pruned:
        old.unlink()
    return pruned
