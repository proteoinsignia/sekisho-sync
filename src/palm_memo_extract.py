#!/usr/bin/env python3
"""
palm_memo_extract.py - Extract Palm MemoDB.pdb into plain text files.

Part of: Sekisho Sync v1.2.2
License: MIT

Key features:
- STREAMING PDB reads (no full file load into RAM)
- Streaming SHA256 computation (only when --state is used)
- Auto-detects latest PDB from Sekisho Sync archive (--sekisho flag)
- Atomic writes + optional fsync
- Lock context manager with stale lock detection
- Signal handlers for graceful cleanup
- Dedupe memos across runs via --memo-state

Optimized for: Raspberry Pi 3B/4/5, Linux ARM/x86
Part of:       Sekisho Sync v1.2.2
Data dir:      /var/lib/sekisho/raw/ (Sekisho Sync default)
"""

import argparse
import hashlib
import json
import logging
import os
import re
import signal
import struct
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

__version__ = "1.2.2"
__platform__ = "linux-arm/x86"

# Palm PDB constants
PDB_HEADER_SIZE = 78
RECORD_ENTRY_SIZE = 8

DEFAULT_MAX_RECORD_SIZE = 5 * 1024 * 1024   # 5MB
DEFAULT_MAX_RECORDS = 25000                 # safe cap for Pi
HASH_CHUNK = 1024 * 1024                    # 1MB chunks for streaming

# Sekisho Sync integration
# SEKISHO_BASE: validated by config.py when run as part of Sekisho Sync.
# When invoked standalone, falls back to the same default.
DEFAULT_SEKISHO_BASE = Path(os.getenv("SEKISHO_BASE", "/var/lib/sekisho"))
DEFAULT_SEKISHO_RAW = DEFAULT_SEKISHO_BASE / "raw"

logger = logging.getLogger("palm_memo_extract")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Global for signal handlers
_cleanup_lock_path: Optional[Path] = None

def be_u16(b: bytes) -> int:
    """Big-endian u16. ARM-safe."""
    return struct.unpack(">H", b)[0]

def be_u32(b: bytes) -> int:
    """Big-endian u32. ARM-safe."""
    return struct.unpack(">I", b)[0]

@dataclass
class RecordEntry:
    record_no: int  # original record number (0-indexed)
    offset: int
    attr: int
    uid: int

class LockFile:
    """POSIX non-blocking lock file with stale lock detection."""
    
    def __init__(self, path: Optional[Path]):
        self.path = path
        self.fp = None
        self.locked = False

    def __enter__(self):
        if not self.path:
            return self
        
        try:
            import fcntl
            
            # Check for stale locks
            if self.path.exists():
                try:
                    with self.path.open('r') as f:
                        old_pid = int(f.read().strip())
                    # Check if process exists
                    if not os.path.exists(f'/proc/{old_pid}'):
                        logger.warning(f"Removing stale lock (PID {old_pid} not running)")
                        self.path.unlink()
                except (ValueError, FileNotFoundError):
                    # Corrupt lock file, remove it
                    logger.warning("Removing corrupt lock file")
                    self.path.unlink()
            
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.fp = self.path.open("w")
            fcntl.flock(self.fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fp.write(f"{os.getpid()}\n")
            self.fp.flush()
            self.locked = True
            logger.debug(f"Acquired lock: {self.path}")
            return self
            
        except IOError as e:
            if self.fp:
                try:
                    self.fp.close()
                except Exception:
                    pass
            logger.error(f"Failed to acquire lock {self.path}: {e}")
            logger.error("Another instance may be running. Use --force to override.")
            raise RuntimeError(f"Lock acquisition failed: {e}")

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.locked and self.fp and self.path:
            try:
                import fcntl
                fcntl.flock(self.fp.fileno(), fcntl.LOCK_UN)
                self.fp.close()
                # Always try to clean up lock file
                if self.path.exists():
                    self.path.unlink()
                logger.debug(f"Released lock: {self.path}")
            except Exception as e:
                logger.warning(f"Lock cleanup failed: {e}")

def setup_signal_handlers(lock_path: Optional[Path]):
    """Setup SIGTERM/SIGINT handlers for graceful cleanup."""
    global _cleanup_lock_path
    _cleanup_lock_path = lock_path
    
    def signal_handler(signum, frame):
        logger.warning(f"Received signal {signum}, cleaning up...")
        if _cleanup_lock_path and _cleanup_lock_path.exists():
            try:
                _cleanup_lock_path.unlink()
                logger.info(f"Cleaned up lock file: {_cleanup_lock_path}")
            except Exception as e:
                logger.warning(f"Failed to clean lock: {e}")
        sys.exit(128 + signum)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

def sanitize_folder_name(s: str, max_len: int = 80) -> str:
    """Sanitize string for safe filesystem use."""
    s = s.strip().replace("\r", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-zA-Z0-9 _\-.\(\)\[\]]+", "_", s)
    s = s.strip(" ._")
    
    # Prevent hidden files
    if s.startswith("."):
        s = "_" + s
    
    return (s or "memo")[:max_len]

def atomic_write_text(path: Path, text: str, encoding: str = "utf-8", do_fsync: bool = True) -> None:
    """Atomic write with optional fsync for SD card durability."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding=encoding) as f:
        f.write(text)
        f.flush()
        if do_fsync:
            os.fsync(f.fileno())
    os.replace(tmp, path)

def atomic_write_json(path: Path, obj: Dict, do_fsync: bool = True) -> None:
    """Atomic JSON write with optional fsync."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.flush()
        if do_fsync:
            os.fsync(f.fileno())
    os.replace(tmp, path)

def stream_sha256(file_path: Path) -> str:
    """Compute SHA256 via streaming (memory-efficient for large files)."""
    h = hashlib.sha256()
    with file_path.open("rb") as f:
        while True:
            chunk = f.read(HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()

def load_state_hash(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"Failed to load state hash from {path}: {e}")
        return None

def save_state_hash(path: Path, value: str, do_fsync: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, value + "\n", do_fsync=do_fsync)

def load_memo_state(path: Path) -> Set[str]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and "memo_ids" in obj and isinstance(obj["memo_ids"], list):
            return {str(x) for x in obj["memo_ids"]}
        if isinstance(obj, list):
            return {str(x) for x in obj}
    except FileNotFoundError:
        return set()
    except Exception as e:
        logger.warning(f"Failed to load memo state from {path}: {e}")
        logger.warning("Will treat as empty state (may re-extract memos)")
        return set()
    return set()

def save_memo_state(path: Path, memo_ids: Set[str], do_fsync: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {"memo_ids": sorted(memo_ids)}, do_fsync=do_fsync)

def check_pilotlink_integration(pdb_path: Path) -> Dict[str, str]:
    """Detect if PDB came from pilot-xfer and log useful info."""
    info = {}
    
    # Check if running on system with pilot-link installed
    try:
        result = subprocess.run(
            ["pilot-xfer", "--version"],
            capture_output=True,
            text=True,
            timeout=2
        )
        if result.returncode == 0:
            version = result.stdout.strip().split("\n")[0]
            info["pilot_link"] = version
            logger.debug(f"Detected pilot-link: {version}")
    except Exception:
        pass
    
    # Check file metadata
    try:
        stat = pdb_path.stat()
        info["file_size"] = stat.st_size
        info["mtime"] = datetime.fromtimestamp(stat.st_mtime).isoformat()
    except Exception:
        pass
    
    return info

def find_latest_sekisho_pdb(sekisho_raw: Path, db_name: str = "MemoDB") -> Optional[Path]:
    """
    Find most recent PDB from Sekisho Sync daemon.
    
    Sekisho structure: /var/lib/sekisho/raw/YYYY-MM-DD/HHMMSS_ffffff/MemoDB.pdb
    Returns the most recent PDB file or None if not found.
    """
    if not sekisho_raw.exists():
        return None
    
    try:
        # Find all matching PDBs
        pdbs = list(sekisho_raw.rglob(f"{db_name}.pdb"))
        
        if not pdbs:
            return None
        
        # Sort by modification time (most recent first)
        pdbs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        
        latest = pdbs[0]
        logger.info(f"Found latest Sekisho PDB: {latest}")
        return latest
        
    except Exception as e:
        logger.warning(f"Error searching Sekisho directory: {e}")
        return None

def read_header_and_table(
    fp, 
    filesize: int, 
    max_records: int, 
    strict: bool
) -> Tuple[str, int, int, List[RecordEntry]]:
    """
    Read PDB header and record table via streaming (no full file load).
    
    Returns: (name, flags, num_records, entries)
    """
    fp.seek(0)
    header = fp.read(PDB_HEADER_SIZE)
    if len(header) < PDB_HEADER_SIZE:
        raise ValueError(f"File too small: {len(header)} bytes (need {PDB_HEADER_SIZE})")

    # Parse header
    name = header[0:32].split(b"\x00", 1)[0].decode("latin-1", errors="replace")
    flags = be_u16(header[32:34])
    num_records = be_u16(header[76:78])

    if num_records > max_records:
        raise ValueError(f"Suspicious record count: {num_records} (max {max_records})")

    # Read record table
    table_size = num_records * RECORD_ENTRY_SIZE
    fp.seek(PDB_HEADER_SIZE)
    table = fp.read(table_size)
    if len(table) != table_size:
        raise ValueError(f"Corrupt PDB: record table truncated (need {table_size}, got {len(table)})")

    entries: List[RecordEntry] = []
    for i in range(num_records):
        entry = table[i * RECORD_ENTRY_SIZE:(i + 1) * RECORD_ENTRY_SIZE]
        off = be_u32(entry[0:4])
        attr = entry[4]
        uid = int.from_bytes(entry[5:8], "big")

        if off >= filesize:
            msg = f"Record {i}: offset {off} exceeds file size {filesize}"
            if strict:
                raise ValueError(msg)
            logger.warning(f"⚠️  {msg} (skipping)")
            continue

        entries.append(RecordEntry(record_no=i, offset=off, attr=attr, uid=uid))

    return name, flags, num_records, entries

def decode_memo_text(blob: bytes, encoding: str = "auto") -> str:
    """Decode memo text from raw bytes."""
    raw = blob.split(b"\x00", 1)[0]
    # Strip common control garbage (PilotLink sometimes adds this)
    raw = raw.lstrip(b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0b\x0c\x0e\x0f")

    if encoding == "utf8":
        return raw.decode("utf-8", errors="replace")
    if encoding == "cp1252":
        return raw.decode("cp1252", errors="replace")
    if encoding == "latin1":
        return raw.decode("latin-1", errors="replace")

    # auto
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1252")
        except Exception:
            return raw.decode("latin-1", errors="replace")

def main():
    ap = argparse.ArgumentParser(
        description="Extract Palm MemoDB.pdb records into one-folder-per-memo.",
        epilog=f"Version {__version__} - Part of Sekisho Sync v1.2.2"
    )
    ap.add_argument("pdb", nargs="?", help="Path to MemoDB.pdb (optional if using --sekisho)")
    ap.add_argument("--out", required=True, help="Output directory (processed)")
    ap.add_argument("--sekisho", action="store_true", 
                    help="Auto-detect latest PDB from Sekisho Sync archive ($SEKISHO_BASE/raw/)")
    ap.add_argument("--sekisho-base", type=str, default=str(DEFAULT_SEKISHO_BASE),
                    help=f"Sekisho base directory (default: {DEFAULT_SEKISHO_BASE})")
    ap.add_argument("--prefix-date", action="store_true", help="Prefix folder names with YYYY-MM-DD")
    ap.add_argument("--state", default=None, help="State file to skip processing if PDB unchanged (SHA256)")
    ap.add_argument("--memo-state", default=None, help="Optional JSON state to dedupe memos across runs")
    ap.add_argument("--strict", action="store_true", help="Fail on any inconsistency")
    ap.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    ap.add_argument("--force", action="store_true", help="Ignore --state and bypass lock")
    ap.add_argument("--dry-run", action="store_true", help="Do not write files, only report")
    ap.add_argument("--encoding", default="auto", choices=["auto", "utf8", "cp1252", "latin1"], 
                    help="Text decoding (auto=try utf8→cp1252→latin1)")
    ap.add_argument("--max-record-size", type=int, default=DEFAULT_MAX_RECORD_SIZE, 
                    help=f"Max record size in bytes (default: {DEFAULT_MAX_RECORD_SIZE})")
    ap.add_argument("--max-records", type=int, default=DEFAULT_MAX_RECORDS, 
                    help=f"Max record count (default: {DEFAULT_MAX_RECORDS})")
    ap.add_argument("--lock", default=None, help="Optional lock file path to avoid concurrent runs")
    ap.add_argument("--no-fsync", action="store_true", help="Disable fsync (faster, less safe on SD)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    args = ap.parse_args()
    
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Determine PDB source
    if args.sekisho:
        sekisho_base = Path(args.sekisho_base)
        sekisho_raw = sekisho_base / "raw"
        logger.info(f"Sekisho mode: searching {sekisho_raw}")
        
        pdb_path = find_latest_sekisho_pdb(sekisho_raw)
        if not pdb_path:
            logger.error(f"No MemoDB.pdb found in {sekisho_raw}")
            logger.error("Is Sekisho Sync daemon running? Has it done any backups?")
            sys.exit(1)
    else:
        if not args.pdb:
            logger.error("Error: Either provide PDB path or use --sekisho")
            logger.error("Examples:")
            logger.error("  palm_memo_extract.py /path/to/MemoDB.pdb --out extracted/")
            logger.error("  palm_memo_extract.py --sekisho --out extracted/")
            sys.exit(1)
        
        pdb_path = Path(args.pdb)
        if not pdb_path.exists():
            logger.error(f"Input file not found: {pdb_path}")
            sys.exit(1)

    out_dir = Path(args.out)
    do_fsync = not args.no_fsync
    
    # Setup signal handlers for graceful cleanup
    lock_path = Path(args.lock) if args.lock else None
    if lock_path and not args.force:
        setup_signal_handlers(lock_path)

    try:
        with LockFile(lock_path if not args.force else None):
            filesize = pdb_path.stat().st_size
            logger.info(f"Reading PDB: {pdb_path} ({filesize} bytes)")

            # Compute SHA only if we need --state (memory efficient)
            pdb_sha256 = None
            if args.state:
                logger.debug("Computing SHA256 via streaming...")
                pdb_sha256 = stream_sha256(pdb_path)
                logger.debug(f"PDB SHA256: {pdb_sha256}")
                
                if not args.force:
                    prev = load_state_hash(Path(args.state))
                    if prev == pdb_sha256:
                        logger.info("No changes detected (MemoDB SHA256 unchanged). Exiting.")
                        return

            # Check PilotLink integration
            pilot_info = check_pilotlink_integration(pdb_path)

            memo_seen: Set[str] = set()
            if args.memo_state:
                memo_seen = load_memo_state(Path(args.memo_state))
                logger.debug(f"Loaded {len(memo_seen)} previously seen memos")

            stamp = datetime.now().strftime("%Y-%m-%d") if args.prefix_date else ""

            index: Dict = {
                "version": __version__,
                "platform": __platform__,
                "source_pdb": str(pdb_path),
                "extracted_at": datetime.now().isoformat(timespec="seconds"),
                "pilot_link": pilot_info,
                "sekisho_mode": args.sekisho,
            }
            
            # Only include SHA256 if computed
            if pdb_sha256:
                index["pdb_sha256"] = pdb_sha256

            index["memos"] = []

            extracted = 0
            skipped_empty = 0
            skipped_dupe = 0

            if not args.dry_run:
                out_dir.mkdir(parents=True, exist_ok=True)

            # STREAMING: Open file once, seek for each record
            with pdb_path.open("rb") as fp:
                name, flags, num_records, entries = read_header_and_table(
                    fp, filesize=filesize, max_records=args.max_records, strict=args.strict
                )
                logger.info(f"PDB Name: '{name}', Records: {num_records}, Flags: 0x{flags:04x}")
                
                index["pdb_name"] = name
                index["total_records"] = num_records
                index["valid_records"] = len(entries)

                # Compute lengths safely by offset order
                entries_sorted = sorted(entries, key=lambda e: e.offset)
                skipped_corrupt = 0

                for idx, e in enumerate(entries_sorted):
                    off = e.offset
                    next_off = entries_sorted[idx + 1].offset if idx + 1 < len(entries_sorted) else filesize

                    if next_off < off:
                        msg = f"Record {e.record_no}: backwards offset (this={off}, next={next_off})"
                        if args.strict:
                            raise ValueError(msg)
                        logger.warning(f"⚠️  {msg} (skipping)")
                        skipped_corrupt += 1
                        continue

                    length = next_off - off
                    if length > args.max_record_size:
                        msg = f"Record {e.record_no}: suspiciously large ({length} bytes, max={args.max_record_size})"
                        if args.strict:
                            raise ValueError(msg)
                        logger.warning(f"⚠️  {msg} (skipping)")
                        skipped_corrupt += 1
                        continue

                    # STREAMING: Seek to record and read only that record
                    fp.seek(off)
                    blob = fp.read(length)

                    text = decode_memo_text(blob, encoding=args.encoding).strip()
                    if not text:
                        skipped_empty += 1
                        continue

                    memo_id = hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()
                    if args.memo_state and memo_id in memo_seen:
                        skipped_dupe += 1
                        continue

                    title = text.splitlines()[0].strip() if text else f"memo_{e.record_no}"
                    
                    # Extract first word for filename
                    first_word = title.split()[0] if title.split() else f"memo_{e.record_no}"
                    first_word = sanitize_folder_name(first_word, max_len=40)
                    
                    # Flat structure: r00000_firstword.txt
                    if stamp:
                        filename = f"{stamp}_r{e.record_no:05d}_{first_word}.txt"
                    else:
                        filename = f"r{e.record_no:05d}_{first_word}.txt"

                    if not args.dry_run:
                        file_path = out_dir / filename
                        atomic_write_text(file_path, text + "\n", do_fsync=do_fsync)

                    index["memos"].append({
                        "id": memo_id,
                        "filename": filename,
                        "record_no": e.record_no,
                        "uid": e.uid,
                        "attr": e.attr,
                        "length_bytes": length,
                        "first_line": title[:200],
                    })

                    extracted += 1
                    if args.memo_state:
                        memo_seen.add(memo_id)

                if skipped_corrupt > 0:
                    logger.warning(f"Skipped {skipped_corrupt} corrupt records")

            # Write index
            if not args.dry_run:
                atomic_write_json(out_dir / "index.json", index, do_fsync=do_fsync)

            # Save state files
            if args.state and not args.dry_run and pdb_sha256:
                save_state_hash(Path(args.state), pdb_sha256, do_fsync=do_fsync)

            if args.memo_state and not args.dry_run:
                save_memo_state(Path(args.memo_state), memo_seen, do_fsync=do_fsync)

            logger.info(f"✓ Extracted {extracted} memos into: {out_dir}")
            if skipped_empty:
                logger.info(f"  (Skipped {skipped_empty} empty records)")
            if skipped_dupe:
                logger.info(f"  (Skipped {skipped_dupe} duplicates via --memo-state)")

    except RuntimeError as e:
        logger.error(str(e))
        sys.exit(1)
    except ValueError as e:
        logger.error(f"PDB parsing error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
