#!/usr/bin/env python3
"""
resolume_unused_media.py - made with claude.ai, because I'm not smart enough
------------------------
Find media files on disk that are not referenced in any Resolume composition (.avc files).

"""

#CLI OPTIONS - be sure to set variables below

"""
--media          Folder to scan for media files (required; repeat for multiple folders)
--compositions   Override the COMPOSITIONS_FOLDER variable
--output         Override the OUTPUT_FILE variable
--move-folder    Override the MOVE_FOLDER variable
--action         report | move | delete  (default: report)  
--confirm        Actually perform move/delete (omit for dry run) Has no effect when --action is 'report'
--extensions     Comma-separated list of file extensions to consider as media
--debug          Write detailed path matching info to a _debug file

"""


# ---------------------------------------------------------------------------
# CONFIGURE THESE VARIABLES - override with CLI args if needed
# ---------------------------------------------------------------------------

COMPOSITIONS_FOLDER = r"C:\Documents\Resolume Avenue\Compositions"

OUTPUT_FILE = r"C:\Documents\Resolume Avenue\unused_media_report.csv"

MOVE_FOLDER = "resolume unused"


#EXAMPLES

"""
    python resolume_unused_media.py --media "C:/(root media location)"  
        will do a dry run of parsing compositions found in COMPOSITIONS_FOLDER and moving unused files into MOVE_FOLDER, a subfolder of where they are found 
        and then write the results to OUTPUT_FILE
        
    python resolume_unused_media.py --media "C:/(root media location)" --debug > debug_output.txt 2>&1
    
    python resolume_unused_media.py --media "C:/(root media location)" --confirm (with no --action, will be a dry run)
    
    python resolume_unused_media.py --media "C:/(root media location)" --confirm --action "move" (will do it and write the CSV report)
    
Or override any variable from the command line. Run with --help for all options.

"""


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
import argparse
import csv
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_EXTENSIONS = {
    "mp4", "mov", "avi", "mkv", "wmv", "flv", "webm",
    "png", "jpg", "jpeg", "gif", "bmp", "tiff", "tga", "dds", "exr",
    "hap", "dxv",
    "wav", "mp3", "aiff", "aif", "flac", "ogg", "m4a",
}

# Filename prefixes to always ignore (macOS metadata sidecar files etc)
IGNORE_PREFIXES = ("._",)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
_DRIVE_RE = re.compile(r"^[A-Za-z]:[/\\]")
_UNC_RE = re.compile(r"^[/\\]{2}")


def _looks_like_path(value: str) -> bool:
    if not value:
        return False
    if _DRIVE_RE.match(value) or _UNC_RE.match(value):
        return True
    if value.startswith("/"):
        return True
    if ("/" in value or "\\" in value) and "." in os.path.basename(value):
        return True
    return False


def _normalise_path(raw: str) -> str | None:
    try:
        cleaned = raw.strip().replace("\\", "/")
        p = Path(cleaned)
        return str(p).lower().replace("\\", "/")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Parse .avc XML
# ---------------------------------------------------------------------------

# Attributes that Resolume uses to store media file references.
# These are checked by name regardless of whether the value looks like a path,
# which catches bare filenames (no folder) that the heuristic would otherwise miss.
RESOLUME_FILE_ATTRS = {"filename", "value"}


def extract_paths_from_avc(avc_path: Path, debug_lines: list | None = None) -> set[str]:
    """
    Return a set of normalised lowercase path strings found in an .avc file.
    Scans all XML attributes and text nodes. Known Resolume file attributes
    (fileName, value) are always included even if they lack path separators.
    """
    found = set()
    try:
        tree = ET.parse(avc_path)
    except ET.ParseError as e:
        print(f"  [WARN] Could not parse {avc_path}: {e}", file=sys.stderr)
        return found

    root = tree.getroot()

    for elem in root.iter():
        for attr_name, attr_value in elem.attrib.items():
            candidate = attr_value.strip()
            if not candidate:
                continue
            is_known_attr = attr_name.lower() in RESOLUME_FILE_ATTRS
            if is_known_attr:
                # For known file attributes, accept if it looks like a path OR
                # has a media file extension (catches bare filenames with no folder)
                ext = Path(candidate).suffix.lstrip(".").lower()
                if not _looks_like_path(candidate) and ext not in DEFAULT_EXTENSIONS:
                    continue
            elif not _looks_like_path(candidate):
                continue
            normalised = _normalise_path(candidate)
            if normalised:
                if debug_lines is not None:
                    debug_lines.append(f"    [XML attr '{attr_name}'] raw:  {candidate}")
                    debug_lines.append(f"    {'':>28}norm: {normalised}")
                found.add(normalised)

        if elem.text:
            candidate = elem.text.strip()
            if _looks_like_path(candidate):
                normalised = _normalise_path(candidate)
                if normalised:
                    if debug_lines is not None:
                        debug_lines.append(f"    [XML text] raw:  {candidate}")
                        debug_lines.append(f"    {'':>16}norm: {normalised}")
                    found.add(normalised)

    return found


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------
def find_avc_files(folder: Path) -> list[Path]:
    return sorted(folder.rglob("*.avc"))


def find_media_files(folders: list[Path], extensions: set[str]) -> list[Path]:
    media = []
    for folder in folders:
        for root, _dirs, files in os.walk(folder):
            for fname in files:
                # Skip macOS sidecar files and other ignored prefixes
                if any(fname.startswith(p) for p in IGNORE_PREFIXES):
                    continue
                ext = Path(fname).suffix.lstrip(".").lower()
                if ext in extensions:
                    media.append(Path(root) / fname)
    return sorted(media)


# ---------------------------------------------------------------------------
# Output file helpers
# ---------------------------------------------------------------------------
def _stem_path(output_path: Path, suffix: str) -> Path:
    """Return a sibling file with a suffix appended to the stem."""
    return output_path.parent / f"{output_path.stem}{suffix}{output_path.suffix}"


def _open_output(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    return open(path, "w", newline="", encoding="utf-8")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------
def run(args):
    comp_folder = Path(args.compositions)
    media_folders = [Path(m) for m in args.media]
    output_path = Path(args.output)
    action = args.action
    dry_run = not args.confirm
    move_folder_name = args.move_folder
    debug = args.debug

    # Auto-named sibling output files
    dryrun_path = _stem_path(output_path, "_dryrun")
    debug_path  = _stem_path(output_path, "_debug")

    extensions = set(
        e.strip().lower().lstrip(".")
        for e in args.extensions.split(",")
        if e.strip()
    )

    if not comp_folder.is_dir():
        sys.exit(f"ERROR: Compositions folder not found: {comp_folder}")
    for mf in media_folders:
        if not mf.is_dir():
            sys.exit(f"ERROR: Media folder not found: {mf}")

    # ---------------------------------------------------------------------------
    # Step 1: Parse all .avc files
    # ---------------------------------------------------------------------------
    avc_files = find_avc_files(comp_folder)
    print(f"Found {len(avc_files)} composition file(s) in: {comp_folder}")

    debug_lines = [] if debug else None
    referenced_paths: set[str] = set()

    for avc in avc_files:
        if debug_lines is not None:
            debug_lines.append(f"\n  Parsing: {avc.name}")
        paths = extract_paths_from_avc(avc, debug_lines=debug_lines)
        print(f"  {avc.name}: {len(paths)} path reference(s)")
        referenced_paths.update(paths)

    print(f"\nTotal unique referenced paths across all compositions: {len(referenced_paths)}")

    # Build sets for fast lookup
    referenced_full   = referenced_paths
    referenced_names  = {Path(r).name for r in referenced_paths}

    # ---------------------------------------------------------------------------
    # Step 2: Find media files on disk
    # ---------------------------------------------------------------------------
    media_files = find_media_files(media_folders, extensions)
    print(f"Total media files found on disk:                      {len(media_files)}")

    # ---------------------------------------------------------------------------
    # Step 3: Classify used vs unused
    # ---------------------------------------------------------------------------
    unused = []
    used   = []

    for mf in media_files:
        normalised     = _normalise_path(str(mf))
        filename_lower = mf.name.lower()

        full_match     = normalised in referenced_full
        filename_match = filename_lower in referenced_names

        in_refs = full_match or filename_match

        if debug_lines is not None:
            if full_match:
                reason = "FULL PATH MATCH"
            elif filename_match:
                reason = "FILENAME-ONLY MATCH"
            else:
                reason = "NO MATCH"
            debug_lines.append(f"  [{reason}] {mf.name}")
            debug_lines.append(f"    disk path (norm): {normalised}")
            if not full_match and filename_match:
                triggers = [r for r in referenced_paths if Path(r).name == filename_lower]
                for t in triggers:
                    debug_lines.append(f"    matched against:  {t}")

        if in_refs:
            used.append(mf)
        else:
            unused.append(mf)

    print(f"\nUsed media files:   {len(used)}")
    print(f"Unused media files: {len(unused)}")

    # ---------------------------------------------------------------------------
    # Step 4: Write CSV report
    # ---------------------------------------------------------------------------
    _write_csv(output_path, unused, used)
    print(f"\nCSV report:  {output_path.resolve()}")

    # ---------------------------------------------------------------------------
    # Step 5: Write debug file if requested
    # ---------------------------------------------------------------------------
    if debug and debug_lines is not None:
        with _open_output(debug_path) as f:
            f.write("\n".join(debug_lines))
        print(f"Debug log:   {debug_path.resolve()}")

    # ---------------------------------------------------------------------------
    # Step 6: Perform or preview action
    # ---------------------------------------------------------------------------
    if not unused:
        print("\nNo unused files found. Nothing to do.")
        return

    if action == "report":
        print("\nAction: report only. Done.")
        return

    if dry_run:
        print(f"\nDRY RUN — previewing '{action}' action (no files changed).")
        print(f"Dry run log: {dryrun_path.resolve()}")
        print("Add --confirm to apply for real.\n")

    if action == "move":
        log_lines = _do_move(unused, move_folder_name, dry_run)
    elif action == "delete":
        log_lines = _do_delete(unused, dry_run)
    else:
        log_lines = []

    # Write dry run log to file instead of screen
    if dry_run and log_lines:
        with _open_output(dryrun_path) as f:
            f.write("\n".join(log_lines))
        print(f"Dry run log written to: {dryrun_path.resolve()}")


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------
def _write_csv(output_path: Path, unused: list[Path], used: list[Path]):
    with _open_output(output_path) as f:
        writer = csv.writer(f)
        writer.writerow(["Status", "File Path", "Size (bytes)", "Extension", "Parent Folder"])
        for mf in unused:
            try:
                size = mf.stat().st_size
            except OSError:
                size = ""
            writer.writerow(["UNUSED", str(mf), size, mf.suffix.lstrip(".").lower(), str(mf.parent)])
        for mf in used:
            try:
                size = mf.stat().st_size
            except OSError:
                size = ""
            writer.writerow(["USED", str(mf), size, mf.suffix.lstrip(".").lower(), str(mf.parent)])


# ---------------------------------------------------------------------------
# Move action
# ---------------------------------------------------------------------------
def _do_move(unused: list[Path], move_folder_name: str, dry_run: bool) -> list[str]:
    lines = []
    moved = 0
    errors = 0
    label = "WOULD MOVE" if dry_run else "MOVING"

    for mf in unused:
        dest_dir = mf.parent / move_folder_name
        dest = dest_dir / mf.name
        lines.append(f"{label}: {mf}")
        lines.append(f"     -> {dest}")
        if not dry_run:
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    stem, suffix, counter = mf.stem, mf.suffix, 1
                    while dest.exists():
                        dest = dest_dir / f"{stem}_{counter}{suffix}"
                        counter += 1
                shutil.move(str(mf), str(dest))
                moved += 1
            except Exception as e:
                msg = f"  [ERROR] Could not move {mf}: {e}"
                lines.append(msg)
                print(msg, file=sys.stderr)
                errors += 1
        else:
            moved += 1

    summary = f"\n{'Would move' if dry_run else 'Moved'} {moved} file(s). Errors: {errors}"
    lines.append(summary)
    print(summary.strip())
    return lines


# ---------------------------------------------------------------------------
# Delete action
# ---------------------------------------------------------------------------
def _do_delete(unused: list[Path], dry_run: bool) -> list[str]:
    lines = []
    deleted = 0
    errors = 0
    label = "WOULD DELETE" if dry_run else "DELETING"

    for mf in unused:
        lines.append(f"{label}: {mf}")
        if not dry_run:
            try:
                mf.unlink()
                deleted += 1
            except Exception as e:
                msg = f"  [ERROR] Could not delete {mf}: {e}"
                lines.append(msg)
                print(msg, file=sys.stderr)
                errors += 1
        else:
            deleted += 1

    summary = f"\n{'Would delete' if dry_run else 'Deleted'} {deleted} file(s). Errors: {errors}"
    lines.append(summary)
    print(summary.strip())
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(
        description="Find media files not referenced in any Resolume composition (.avc).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--compositions", default=COMPOSITIONS_FOLDER,
        help=f"Folder to search for .avc files (default: {COMPOSITIONS_FOLDER})"
    )
    parser.add_argument(
        "--media", required=True, action="append",
        help="Folder to search for media files (repeat for multiple folders)"
    )
    parser.add_argument(
        "--output", default=OUTPUT_FILE,
        help=f"CSV output path (default: {OUTPUT_FILE})"
    )
    parser.add_argument(
        "--action", choices=["report", "move", "delete"], default="report",
        help="What to do with unused files (default: report)"
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="Disable dry-run and actually perform move/delete"
    )
    parser.add_argument(
        "--extensions",
        default=",".join(sorted(DEFAULT_EXTENSIONS)),
        help="Comma-separated file extensions to consider as media"
    )
    parser.add_argument(
        "--move-folder", default=MOVE_FOLDER,
        help=f"Subfolder name for moved files (default: {MOVE_FOLDER})"
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Write detailed path matching info to a _debug file alongside the CSV"
    )
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
