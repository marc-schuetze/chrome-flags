#!/usr/bin/env python3
"""Every Chromium command-line flag across versions and desktop OSes.

For each Chrome milestone that Chrome for Testing (CfT) publishes, this reads
  - the Chromium source at that tag (sparse clone of the GitHub mirror):
      switches        const char kX[] = "x" in *switch*.{cc,h}        --x
      features        BASE_FEATURE(kX, ...) in *feature*.{cc,h}       --enable-features=X
      blink features  runtime_enabled_features.json5                  --enable-blink-features=X
    with the // comment above each definition, the default state of each
    feature per OS and the "unsupported command-line flag" list
    (chrome/browser/ui/startup/bad_flags_prompt.cc) per OS;
  - the official binary per OS (linux64, mac-arm64, win64): a flag counts as
    available on an OS if its name occurs as a string in that binary.

  ./build.py update [--only 113,155] [--limit N] [--force]   process new or changed milestones -> data/<m>.json.gz
  ./build.py resource [--only 141]                 recompute source metadata of data/, no downloads
  ./build.py site                                  merge data/ -> site/flags.json
"""
import argparse
import bisect
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SITE = ROOT / "site"
CACHE = Path(os.environ.get("CHROME_FLAGS_CACHE", Path.home() / ".cache/chrome-flags-site"))
REPO = "https://github.com/chromium/chromium.git"
CFT = "https://googlechromelabs.github.io/chrome-for-testing/latest-versions-per-milestone-with-downloads.json"
PLATFORMS = {"linux": "linux64", "mac": "mac-arm64", "win": "win64"}
# BUILDFLAG(IS_X) values per OS; any other BUILDFLAG(...) counts as on (desktop defaults)
OS_FLAGS = {"linux": {"LINUX", "POSIX"}, "mac": {"MAC", "APPLE", "POSIX"}, "win": {"WIN"}}
OS_NAMES = {"LINUX", "POSIX", "MAC", "APPLE", "WIN", "ANDROID", "CHROMEOS", "IOS", "FUCHSIA",
            "CHROMEOS_ASH", "CHROMEOS_LACROS", "OZONE"}

PROMPT = "chrome/browser/ui/startup/bad_flags_prompt.cc"
BLINK = "third_party/blink/renderer/platform/runtime_enabled_features.json5"
ABOUT = "chrome/browser/about_flags.cc"
DESCRIPTIONS = ["chrome/browser/flag_descriptions.cc", "chrome/browser/flag_descriptions.h"]
SPARSE = ["*switch*.cc", "*switch*.h", "*feature*.cc", "*feature*.h", "/" + PROMPT, "/" + BLINK, "/" + ABOUT,
          *("/" + d for d in DESCRIPTIONS)]
# kOs* masks in about_flags.cc -> desktop OSes
ABOUT_OS = {"kOsAll": PLATFORMS, "kOsDesktop": PLATFORMS, "kOsLinux": ["linux"], "kOsMac": ["mac"], "kOsWin": ["win"]}

COMMENT = r"((?:^[ \t]*//[^\n]*\n)*)"  # [^\n], not ".": FEATURE runs with re.S
SWITCH = re.compile(COMMENT + r'^[^\n/]*?(?<![\w:])(k\w+)\[\]\s*=\s*"([^"]+)"', re.M)
SWITCH_MACRO = re.compile(COMMENT + r'^[ \t]*(?!BASE_FEATURE)\w+\(\s*(k\w+)\s*,\s*"([^"]+)"\s*\)', re.M)
FEATURE = re.compile(COMMENT + r'^[ \t]*BASE_FEATURE\(\s*(\w+)\s*,', re.M)  # M141: BASE_FEATURE(Name, ...) without k
BLINK_ENTRY = re.compile(r'\{\s*((?:[ \t]*//[^\n]*\n)*)\s*name:\s*"(\w+)"(.*?)\n\s*\},?\n', re.S)
SWITCH_FILE = re.compile(r"(^|_)switch(es|_list)?\.(cc|h)$")  # not *switcher*
BROWSER = re.compile(r"(^|/)(chrome|chrome\.dll|[^/]+ Framework)$")
SWITCH_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")


def run(*cmd, cwd=None):
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


# --- source -------------------------------------------------------------------------------

def checkout(version):
    """One sparse, blobless clone in the cache; each run fetches just the tag it needs."""
    src = CACHE / "src"
    if not (src / ".git").exists():
        run("git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout",
            "--branch", version, REPO, str(src))
    else:
        run("git", "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", "tag", version, cwd=src)
    subprocess.run(["git", "sparse-checkout", "set", "--no-cone", "--stdin"], cwd=src, check=True,
                   input="\n".join(SPARSE) + "\n", text=True)
    run("git", "checkout", "-q", "--force", version, cwd=src)
    return src


def condition(expr, os_name):
    """Evaluate a preprocessor condition for one desktop OS; unknown parts count as true."""
    def buildflag(m):
        flag = m.group(1)
        if flag.startswith("IS_") and flag[3:] in OS_NAMES:
            return str(flag[3:] in OS_FLAGS[os_name])
        return "True"
    expr = re.sub(r"BUILDFLAG\(\s*(\w+)\s*\)", buildflag, expr)
    expr = re.sub(r"defined\s*\(?\s*\w+\s*\)?", "False", expr)
    expr = expr.replace("&&", " and ").replace("||", " or ")
    expr = re.sub(r"!(?!=)", " not ", expr)
    try:
        return bool(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307 - only True/False/and/or/not left
    except Exception:
        return True


def preprocess(text, os_name):
    """Text with lines in inactive #if branches removed, for one OS."""
    out, stack = [], []  # stack of (branch active, some branch taken, parent active)
    for line in re.sub(r"\\[ \t]*\n", " ", text).splitlines():  # join "#if A || \" continuations
        s = line.strip()
        active = all(a for a, _, _ in stack)
        if s.startswith(("#if ", "#if(", "#ifdef", "#ifndef")):
            if s.startswith("#ifdef"):
                c = False
            elif s.startswith("#ifndef"):
                c = True
            else:
                c = condition(s[3:], os_name)
            stack.append((c, c, active))
        elif s.startswith("#elif") and stack:
            _, taken, parent = stack.pop()
            c = not taken and condition(s[5:], os_name)
            stack.append((c, taken or c, parent))
        elif s.startswith("#else") and stack:
            _, taken, parent = stack.pop()
            stack.append((not taken, True, parent))
        elif s.startswith("#endif") and stack:
            stack.pop()
        elif active:
            out.append(line)
    return "\n".join(out)


def doc(comment):
    return " ".join(line.strip().lstrip("/").strip() for line in comment.splitlines()).strip()


def is_value(const, name, defs):
    """Switch files also hold switch *values*: kProcessTypeController = "controller" next to
    kProcessType = "type", kANGLEImplementationVulkanName = "vulkan". A real switch either
    extends its prefix constant's string too (kDisableGpuCompositing) or names itself."""
    # ponytail: name heuristic, ~1 real switch per build lost (launch-time-ticks); exact needs call sites
    return (not SWITCH_NAME.fullmatch(name)
            or (const.endswith("Name") and name.replace("-", "") != const[1:].lower())
            or any(o != const and const.startswith(o) and not name.startswith(s) for _, o, s in defs))


def features(text):
    """(comment, const, args) per BASE_FEATURE; args up to the matching ")" because #if
    branches may each close the call: BASE_FEATURE(kX,\n#if ...\n ENABLED);\n#else ..."""
    for m in FEATURE.finditer(text):
        depth, i = 1, m.end()
        while depth and i < len(text):
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
        args = text[m.end():i]
        while len(re.findall(r"^\s*#if", args, re.M)) > len(re.findall(r"^\s*#endif", args, re.M)):
            end = text.find("#endif", m.end() + len(args))
            if end < 0:
                break
            args = text[m.end():text.find("\n", end) % (len(text) + 1)]
        yield m.group(1), m.group(2), args


def feature_default(args, os_name):
    """on/off for one OS, or the name of the constant/expression that decides it."""
    args = re.sub(r"//[^\n]*", "", preprocess(args, os_name))
    token = re.sub(r"\s+", " ", args.split(",")[-1]).strip(" ;")
    if token.count(")") > token.count("("):  # the closing paren of BASE_FEATURE( itself
        token = token[:token.rindex(")")].strip()
    token = token.rsplit("::", 1)[-1] if "(" not in token else token
    low = token.lower()
    if m := re.fullmatch(r"enable_if\((.*)\)", token):
        return "on" if condition(re.sub(r"\b(IS_\w+)", r"BUILDFLAG(\1)", m.group(1)), os_name) else "off"
    if low.startswith("enabled_by_default_"):  # desktop_only, desktop_android, non_ios, android_only, ...
        return "on" if "desktop" in low or "non_ios" in low else "off"
    if "disabled" in low:
        return "off"
    if "enabled" in low and re.fullmatch(r"\w+", token) and not token.startswith("k"):
        return "on"
    return token or "?"


def parse(src):
    """{name: {kind, doc, file, default?, status?}} plus const -> name for bad_flags_prompt.cc."""
    flags, consts = {}, {}

    def add(name, kind, text, rel, **meta):
        entry = flags.setdefault(name, {"kind": [], "doc": "", "file": rel})
        if kind not in entry["kind"]:
            entry["kind"].append(kind)
        if text and not entry["doc"]:
            entry["doc"], entry["file"] = text, rel
        entry.update(meta)

    files = [(str(p.relative_to(src)), p.name, p.read_text(errors="replace")) for p in sorted(src.rglob("*"))
             if p.suffix in (".cc", ".h") and ".git" not in p.parts]
    # Wrapper macros that expand to BASE_FEATURE, e.g. DEFINE_FEATURE_WITH_MOBILE_COUNTRY_RESTRICTION(kX)
    wrappers = sorted({m for _, _, t in files for m, body in re.findall(r"#define\s+(\w+)\([^)]*\)((?:[^\n]*\\\n)*[^\n]*)", t)
                       if "BASE_FEATURE(" in body and not m.startswith("BASE_FEATURE")})
    wrapped = re.compile(COMMENT + r"^[ \t]*(" + "|".join(wrappers) + r")\(\s*(k\w+)\s*[,)]", re.M) if wrappers else None
    for rel, filename, text in files:
        for comment, macro, const in wrapped.findall(text) if wrapped else []:
            add(const[1:], "feature", doc(comment), rel, default={o: f"set by {macro}" for o in PLATFORMS})
            consts.setdefault(const, const[1:])
        for comment, const, args in features(text):
            name = re.match(r'\s*"(\w+)"', args)
            name = name.group(1) if name else const[1:] if re.match(r"k[A-Z0-9]", const) else const
            const = const if const.startswith("k") else "k" + const
            add(name, "feature", doc(comment), rel, default={o: feature_default(args, o) for o in PLATFORMS})
            consts.setdefault(const, name)
        if SWITCH_FILE.search(filename):
            defs = SWITCH.findall(text) + SWITCH_MACRO.findall(text)
            for comment, const, name in defs:
                if not is_value(const, name, defs):
                    add(name, "switch", doc(comment), rel)
                    consts.setdefault(const, name)
    about = src / ABOUT  # features chrome://flags references but no *feature* file defines: name = const minus k,
    if about.exists():   # kept only if the binary has that string (update; resource cannot add names)
        for const in set(re.findall(r"FEATURE_(?:WITH_PARAMS_)?VALUE_TYPE\(\s*(?:\w*::)*(k[A-Z]\w*)", about.read_text())):
            if const not in consts and const[1:] not in flags:
                add(const[1:], "feature", "", ABOUT)
    blink = src / BLINK
    for comment, name, body in BLINK_ENTRY.findall(blink.read_text() if blink.exists() else ""):
        status = re.search(r'status:\s*("?\w+"?|\{[^}]*\})', body)
        add(name, "blink", doc(comment), BLINK, status=status.group(1).replace('"', "") if status else "none")
    return flags, consts


def infobar(src, consts, os_name):
    """Names from kBadFlags / kBadFeatureFlagsInAboutFlags active on one OS."""
    path = src / PROMPT
    text = preprocess(path.read_text(), os_name) if path.exists() else ""
    body = " ".join(re.findall(r"\[\]\s*=\s*\{(.*?)\n\s*\};", text, re.S))
    refs = re.findall(r"(?<![\w:])&?(?:\w+::)*(k\w+)", re.sub(r"//[^\n]*", "", body))
    return {consts.get(k, k[1:]) for k in refs}


def about_flags(src, consts, names=()):
    """Target flag name -> chrome://flags entry {id, name, desc, os} from about_flags.cc.
    An entry targets every switch/feature constant in its value macro, for MULTI_VALUE_TYPE
    the ones in its choices array."""
    texts = {}
    for d in DESCRIPTIONS:
        path = src / d
        if path.exists():
            for const, literals in re.findall(r'\b(k\w+)\[\]\s*=\s*((?:"(?:[^"\\]|\\.)*"\s*)+);', path.read_text()):
                texts[const] = "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', literals)).replace('\\"', '"')
    path = src / ABOUT
    text = re.sub(r"//[^\n]*", "", path.read_text()) if path.exists() else ""
    arrays = dict(re.findall(r"(k\w+)\[\]\s*=\s*\{(.*?)\n\};", text, re.S))
    out = {}
    entry = re.compile(r'\{\s*"([\w-]+)",\s*flag_descriptions::(k\w+),\s*flag_descriptions::(k\w+),'
                       r'\s*([\w\s|]+?),\s*(\w+)\((.*?)\)\s*\}', re.S)
    for fid, name_c, desc_c, mask, macro, args in entry.findall(text):
        oses = sorted({o for tok in re.findall(r"kOs\w+", mask) for o in ABOUT_OS.get(tok, [])})
        if not oses:
            continue
        refs = re.findall(r"\b(k[A-Z]\w*)", args)
        if macro == "MULTI_VALUE_TYPE":
            body = " ".join(arrays.get(a, "") for a in refs)
            # choices like {desc, switches::kEnableFeatures, "Feature:param/value"} target the feature
            named = [n for v in re.findall(r'switches::k(?:Enable|Disable)Features,\s*"([^"]*)"', body)
                     for n in re.split(r"[,<]", v) if n]
            refs = re.findall(r"::(k[A-Z]\w*)", body) + ["k" + n.split(":")[0] for n in named]
        for ref in refs:
            target = consts.get(ref) or (ref[1:] if ref[1:] in names else None)  # generated blink:: features
            if target and target not in out:
                out[target] = {"id": fid, "name": texts.get(name_c, ""), "desc": texts.get(desc_c, ""), "os": oses}
    return out


def annotate(src, source, consts, presence):
    """Per-milestone record entries: source metadata for every name present in a binary.
    presence: {name: [os, ...]} from the binaries (update) or an existing record (resource)."""
    bad = {o: infobar(src, consts, o) for o in PLATFORMS}
    about = about_flags(src, consts, source)
    out = {}
    for name, oses in presence.items():
        if name not in source:
            continue
        e = dict(source[name], os=sorted(oses))
        if hits := [o for o in e["os"] if name in bad[o]]:
            e["infobar"] = hits
        if "default" in e:
            e["default"] = {o: v for o, v in e["default"].items() if o in e["os"]}
        if name in about and (aos := [o for o in about[name]["os"] if o in e["os"]]):
            e["about"] = dict(about[name], os=aos)
        out[name] = e
    return out


# --- binaries -----------------------------------------------------------------------------

def binary_names(url):
    """present(name) for one CfT zip: name is a suffix of a NUL-terminated string in the browser
    binary (chrome / chrome.dll / ... Framework; not simply the largest file, M113 ships a 436 MB
    libclearkeycdm.so). Suffix, because the linker merges "WebGPU" into
    the tail of "...UnsafeWebGPU"."""
    with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
        zpath = Path(tmp) / "chrome.zip"
        with urllib.request.urlopen(url, timeout=600) as r, open(zpath, "wb") as f:
            shutil.copyfileobj(r, f)
        with zipfile.ZipFile(zpath) as z:
            data = z.read(max((i for i in z.infolist() if BROWSER.search(i.filename)), key=lambda i: i.file_size))
    rev = sorted({s[::-1] for s in data.split(b"\0") if 2 < len(s) < 300})
    del data

    def present(name):
        key = name.encode()[::-1]
        i = bisect.bisect_left(rev, key)
        return i < len(rev) and rev[i].startswith(key)
    # ponytail: suffix match can admit a short name that only occurs inside a longer string
    return present


# --- per milestone ------------------------------------------------------------------------

def milestone(m, version, downloads):
    src = checkout(version)
    source, consts = parse(src)
    urls = {d["platform"]: d["url"] for d in downloads}
    presence = {}
    for os_name, platform in PLATFORMS.items():
        present = binary_names(urls[platform])
        for name in source:
            if present(name):
                presence.setdefault(name, []).append(os_name)
    return {"milestone": int(m), "version": version, "flags": annotate(src, source, consts, presence)}


def write(record):
    with gzip.open(DATA / f"{record['milestone']}.json.gz", "wt") as f:
        json.dump(record, f, separators=(",", ":"), sort_keys=True)


def resource(only):
    """Recompute all source-derived metadata of existing records; presence stays (no downloads)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    for path in sorted(DATA.glob("*.json.gz"), key=lambda p: int(p.name.split(".")[0])):
        rec = json.load(gzip.open(path))
        if only and rec["milestone"] not in only:
            continue
        src = checkout(rec["version"])
        source, consts = parse(src)
        rec["flags"] = annotate(src, source, consts, {n: e["os"] for n, e in rec["flags"].items()})
        write(rec)
        print(f"M{rec['milestone']}: {len(rec['flags'])} flags, "
              f"{sum('about' in e for e in rec['flags'].values())} in chrome://flags", file=sys.stderr, flush=True)


def update(only, limit, force=False):
    with urllib.request.urlopen(CFT, timeout=60) as r:
        milestones = json.load(r)["milestones"]
    DATA.mkdir(exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    done = 0
    for m in sorted(milestones, key=int):
        info = milestones[m]
        path = DATA / f"{m}.json.gz"
        if only and int(m) not in only:
            continue
        downloads = info["downloads"].get("chrome", [])
        if not all(any(d["platform"] == p for d in downloads) for p in PLATFORMS.values()):
            continue
        if not force and path.exists() and json.load(gzip.open(path))["version"] == info["version"]:
            continue
        if limit and done >= limit:
            break
        print(f"M{m} {info['version']}", file=sys.stderr, flush=True)
        record = milestone(m, info["version"], downloads)
        write(record)
        print(f"M{m}: {len(record['flags'])} flags", file=sys.stderr, flush=True)
        done += 1


# --- merge --------------------------------------------------------------------------------

def ranges(points):
    """[(m, value)] sorted -> [[from, to, value]] over consecutive milestones with equal value."""
    out = []
    for m, value in points:
        if out and out[-1][1] == m - 1 and out[-1][2] == value:
            out[-1][1] = m
        else:
            out.append([m, m, value])
    return out


def merge(records, all_milestones):
    """records ascending by milestone -> {name: meta} with per-OS milestone ranges.
    A gap in all_milestones (not processed) does not split a range."""
    index = {m: i for i, m in enumerate(all_milestones)}
    seen, latest, about = {}, {}, {}
    for rec in records:
        m = index[rec["milestone"]]
        for name, e in rec["flags"].items():
            latest[name] = e
            s = seen.setdefault(name, {"os": {}, "infobar": {}, "default": {}, "about": {}})
            if "about" in e:
                about[name] = e["about"]
                for o in e["about"]["os"]:
                    s["about"].setdefault(o, []).append((m, True))
            for o in e["os"]:
                s["os"].setdefault(o, []).append((m, True))
            for o in e.get("infobar", []):
                s["infobar"].setdefault(o, []).append((m, True))
            for o, state in e.get("default", {}).items():
                s["default"].setdefault(o, []).append((m, state))
    out = {}
    for name in sorted(latest, key=str.lower):
        e, s = latest[name], seen[name]
        def spans(points, with_value):
            return [[all_milestones[a], all_milestones[b]] + ([v] if with_value else [])
                    for a, b, v in ranges(points)]
        meta = {"kind": e["kind"], "doc": e["doc"], "file": e["file"],
                "os": {o: spans(p, False) for o, p in s["os"].items()}}
        if s["infobar"]:
            meta["infobar"] = {o: spans(p, False) for o, p in s["infobar"].items()}
        if s["default"]:
            meta["default"] = {o: spans(p, True) for o, p in s["default"].items()}
        if "status" in e:
            meta["status"] = e["status"]
        if name in about:
            meta["about"] = {k: about[name][k] for k in ("id", "name", "desc")}
            meta["about"]["os"] = {o: spans(p, False) for o, p in s["about"].items()}
        out[name] = meta
    return out


def site():
    records = sorted((json.load(gzip.open(p)) for p in DATA.glob("*.json.gz")), key=lambda r: r["milestone"])
    milestones = [r["milestone"] for r in records]
    payload = {"versions": {r["milestone"]: r["version"] for r in records},
               "flags": merge(records, milestones)}
    SITE.mkdir(exist_ok=True)
    (SITE / "flags.json").write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
    print(f"{len(payload['flags'])} flags over {len(milestones)} milestones", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update")
    u.add_argument("--only", type=lambda s: {int(x) for x in s.split(",")}, default=set())
    u.add_argument("--limit", type=int, default=0, help="max milestones per run (0 = all)")
    u.add_argument("--force", action="store_true", help="reprocess milestones already in data/")
    r = sub.add_parser("resource", help="recompute source metadata of data/ without downloads")
    r.add_argument("--only", type=lambda s: {int(x) for x in s.split(",")}, default=set())
    sub.add_parser("site")
    args = ap.parse_args()
    if args.cmd == "update":
        update(args.only, args.limit, args.force)
    elif args.cmd == "resource":
        resource(args.only)
    else:
        site()


if __name__ == "__main__":
    main()
