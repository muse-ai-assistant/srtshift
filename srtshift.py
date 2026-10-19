#!/usr/bin/env python3
"""srtshift - shift subtitle timelines and convert between srt/ass/vtt.

Stdlib only, zero dependencies.
"""
import argparse
import glob
import os
import re
import sys

VERSION = "1.0.0"


# ---------------------------------------------------------------- time utils

def parse_offset(text):
    """Parse an offset expression into milliseconds (signed int).

    Accepts: +2.5 / -2.5 / +2.5s / -1500ms / +00:00:01,500 / +01:02:03.456
    """
    s = text.strip()
    sign = 1
    if s.startswith("+"):
        s = s[1:]
    elif s.startswith("-"):
        sign = -1
        s = s[1:]

    # timecode HH:MM:SS,mmm or HH:MM:SS.mmm
    m = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})", s)
    if m:
        h, mi, sec, frac = m.groups()
        frac = (frac + "000")[:3]
        ms = (int(h) * 3600 + int(mi) * 60 + int(sec)) * 1000 + int(frac)
        return sign * ms

    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(ms|s)?", s, re.IGNORECASE)
    if m:
        num, unit = m.groups()
        val = float(num)
        if (unit or "s").lower() == "ms":
            return sign * int(round(val))
        return sign * int(round(val * 1000))

    raise ValueError(f"cannot parse offset: {text!r}")


def fmt_srt(ms):
    ms = max(0, int(round(ms)))
    h, ms = divmod(ms, 3600000)
    mi, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{mi:02d}:{sec:02d},{ms:03d}"


def fmt_vtt(ms):
    return fmt_srt(ms).replace(",", ".")


def fmt_ass(ms):
    ms = max(0, int(round(ms)))
    h, ms = divmod(ms, 3600000)
    mi, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f"{h:d}:{mi:02d}:{sec:02d}.{ms // 10:02d}"


def parse_srt_time(s):
    m = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})", s.strip())
    if not m:
        raise ValueError(f"bad timestamp: {s!r}")
    h, mi, sec, frac = m.groups()
    frac = (frac + "000")[:3]
    return (int(h) * 3600 + int(mi) * 60 + int(sec)) * 1000 + int(frac)


def parse_ass_time(s):
    m = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2})\.(\d{1,2})", s.strip())
    if not m:
        raise ValueError(f"bad ass timestamp: {s!r}")
    h, mi, sec, frac = m.groups()
    frac = (frac + "00")[:2]
    return (int(h) * 3600 + int(mi) * 60 + int(sec)) * 1000 + int(frac) * 10


# ------------------------------------------------------------------ entries
# Entry: (start_ms, end_ms, text)

def read_text(path):
    raw = open(path, "rb").read()
    # BOM sniffing first
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16")
    if raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    # plain utf-8
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # utf-16 without BOM only if it really looks like it (many NUL bytes)
    if raw.count(b"\x00") > len(raw) * 0.1:
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                t = raw.decode(enc)
                if t and all(ord(c) < 0x10000 or c in "\r\n\t" for c in t[:500]):
                    return t
            except (UnicodeDecodeError, ValueError):
                pass
    # gbk fallback (also covers gb2312)
    try:
        return raw.decode("gbk")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def parse_srt(text):
    entries = []
    blocks = re.split(r"\r?\n\r?\n", text.strip().replace("\r\n", "\n"))
    for b in blocks:
        lines = b.strip().split("\n")
        if len(lines) < 2:
            continue
        # first line may be index
        if re.fullmatch(r"\d+", lines[0].strip()):
            lines = lines[1:]
        m = re.match(r"(.+?)\s*-->\s*(.+)", lines[0])
        if not m:
            continue
        try:
            start = parse_srt_time(m.group(1))
            end = parse_srt_time(m.group(2))
        except ValueError:
            continue
        entries.append((start, end, "\n".join(lines[1:])))
    return entries


def parse_vtt(text):
    text = text.replace("\r\n", "\n")
    # drop WEBVTT header and NOTE/STYLE blocks
    lines = text.split("\n")
    body = []
    i = 0
    if lines and lines[0].strip().upper().startswith("WEBVTT"):
        i = 1
    while i < len(lines):
        if lines[i].strip().upper() in ("NOTE", "STYLE", "REGION"):
            i += 1
            while i < len(lines) and lines[i].strip():
                i += 1
        else:
            body.append(lines[i])
        i += 1
    return parse_srt("\n".join(body))


ASS_TAG_RE = re.compile(r"\{[^}]*\}")


def parse_ass(text):
    entries = []
    in_events = False
    for line in text.splitlines():
        s = line.strip()
        if s.lower() == "[events]":
            in_events = True
            continue
        if s.startswith("[") and s.endswith("]"):
            in_events = False
            continue
        if in_events and s.lower().startswith("dialogue:"):
            payload = s[len("dialogue:"):].strip()
            # Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
            parts = payload.split(",", 9)
            if len(parts) < 10:
                continue
            try:
                start = parse_ass_time(parts[1])
                end = parse_ass_time(parts[2])
            except ValueError:
                continue
            txt = ASS_TAG_RE.sub("", parts[9])
            txt = txt.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
            entries.append((start, end, txt))
    return entries


def detect_format(path, text):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".srt":
        return "srt"
    if ext == ".vtt":
        return "vtt"
    if ext in (".ass", ".ssa"):
        return "ass"
    head = text[:2000]
    if "[Events]" in head or "Dialogue:" in head:
        return "ass"
    if head.lstrip().upper().startswith("WEBVTT"):
        return "vtt"
    return "srt"


def render_srt(entries):
    out = []
    for i, (s, e, t) in enumerate(entries, 1):
        out.append(f"{i}\n{fmt_srt(s)} --> {fmt_srt(e)}\n{t}\n")
    return "\n".join(out)


def render_vtt(entries):
    out = ["WEBVTT\n"]
    for s, e, t in entries:
        out.append(f"{fmt_vtt(s)} --> {fmt_vtt(e)}\n{t}\n")
    return "\n".join(out) + "\n"


ASS_HEADER = """[Script Info]
Title: converted by srtshift
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def render_ass(entries):
    out = [ASS_HEADER.rstrip("\n")]
    for s, e, t in entries:
        txt = t.replace("\n", "\\N")
        out.append(f"Dialogue: 0,{fmt_ass(s)},{fmt_ass(e)},Default,,0,0,0,,{txt}")
    return "\n".join(out) + "\n"


RENDER = {"srt": render_srt, "vtt": render_vtt, "ass": render_ass}
PARSE = {"srt": parse_srt, "vtt": parse_vtt, "ass": parse_ass}


# -------------------------------------------------------------------- main

def transform(entries, offset_ms=0, fps_scale=None):
    out = []
    for s, e, t in entries:
        if fps_scale is not None:
            s = s * fps_scale
            e = e * fps_scale
        s = max(0, s + offset_ms)
        e = max(0, e + offset_ms)
        out.append((s, e, t))
    return out


def describe_change(old, new, fmt):
    return f"{fmt(old[0])} --> {fmt(old[1])}  =>  {fmt(new[0])} --> {fmt(new[1])}"


def process_file(path, offset_ms, fps_scale, to_fmt, dry_run, out_path=None):
    text = read_text(path)
    src_fmt = detect_format(path, text)
    entries = PARSE[src_fmt](text)
    if not entries:
        print(f"! {path}: no subtitle entries found (detected {src_fmt})", file=sys.stderr)
        return 0
    new_entries = transform(entries, offset_ms, fps_scale)
    dst_fmt = to_fmt or src_fmt

    if dry_run:
        fmt_fn = {"srt": fmt_srt, "vtt": fmt_vtt, "ass": fmt_ass}[dst_fmt]
        print(f"== {path} ({src_fmt} -> {dst_fmt}, {len(entries)} entries)")
        for idx, (o, n) in enumerate(zip(entries, new_entries)):
            if idx >= 5:
                break
            print("   " + describe_change(o, n, fmt_fn))
        if len(entries) > 5:
            print(f"   ... and {len(entries) - 5} more")
        return len(entries)

    if out_path is None:
        base, _ = os.path.splitext(path)
        out_path = f"{base}.{dst_fmt}"
        if os.path.abspath(out_path) == os.path.abspath(path):
            out_path = f"{base}.shifted.{dst_fmt}"
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(RENDER[dst_fmt](new_entries))
    print(f"ok {path} -> {out_path} ({len(entries)} entries, {src_fmt}->{dst_fmt})")
    return len(entries)


def expand_patterns(patterns):
    files = []
    for p in patterns:
        hits = sorted(glob.glob(p))
        if hits:
            files.extend(h for h in hits if os.path.isfile(h))
        elif os.path.isfile(p):
            files.append(p)
        else:
            print(f"! not found: {p}", file=sys.stderr)
    # dedupe, keep order
    return list(dict.fromkeys(files))


OFFSET_RE = re.compile(r"^[+-](\d|:)")


def extract_offset(argv):
    """Pull the offset token out of argv before argparse sees it.

    Needed because argparse's greedy nargs="+" would swallow a positional
    offset, and a negative offset like -1500ms looks like an option flag.
    """
    rest = []
    offset = None
    for tok in argv:
        if offset is None and OFFSET_RE.match(tok):
            try:
                parse_offset(tok)
                offset = tok
                continue
            except ValueError:
                pass
        rest.append(tok)
    return rest, offset


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="srtshift",
        description="Shift subtitle timelines and convert between srt/ass/vtt.",
    )
    ap.add_argument("files", nargs="+", help="subtitle files (globs ok)")
    ap.add_argument("--shift", metavar="OFFSET",
                    help="time shift, e.g. +2.5 / -1500ms / +00:00:01,500 "
                         "(also accepted as bare positional arg)")
    ap.add_argument("--set-fps", type=float, metavar="FPS",
                    help="source fps for fps conversion")
    ap.add_argument("--to-fps", type=float, metavar="FPS",
                    help="target fps for fps conversion")
    ap.add_argument("-o", "--output", help="output file (extension decides format)")
    ap.add_argument("--to", choices=["srt", "ass", "vtt"],
                    help="force output format")
    ap.add_argument("--dry-run", action="store_true",
                    help="preview first 5 entries, write nothing")
    ap.add_argument("--version", action="version", version=f"srtshift {VERSION}")
    argv = list(argv) if argv is not None else sys.argv[1:]
    argv, bare_offset = extract_offset(argv)
    args = ap.parse_args(argv)

    offset_text = args.shift or bare_offset
    offset_ms = 0
    if offset_text:
        try:
            offset_ms = parse_offset(offset_text)
        except ValueError as e:
            ap.error(str(e))

    fps_scale = None
    if args.set_fps or args.to_fps:
        if not (args.set_fps and args.to_fps):
            ap.error("--set-fps and --to-fps must be used together")
        fps_scale = args.to_fps / args.set_fps

    to_fmt = args.to
    if args.output:
        ext = os.path.splitext(args.output)[1].lower().lstrip(".")
        if ext in ("srt", "vtt", "ass", "ssa"):
            to_fmt = "ass" if ext == "ssa" else ext

    if offset_ms == 0 and fps_scale is None and not to_fmt and not args.dry_run:
        ap.error("nothing to do: give an offset, --set-fps/--to-fps, or -o/--to")

    files = expand_patterns(args.files)
    if not files:
        ap.error("no input files")

    if args.output and len(files) > 1 and not args.dry_run:
        ap.error("-o with multiple input files: output names would collide")

    total = 0
    for path in files:
        out = args.output if len(files) == 1 else None
        total += process_file(path, offset_ms, fps_scale, to_fmt, args.dry_run, out)
    if not args.dry_run:
        print(f"done: {len(files)} file(s), {total} entries")


if __name__ == "__main__":
    main()
