# How it works

Everything lives in one script, `build.py` (standard library only), one static page,
`site/index.html`, and the per-milestone data in `data/`.

```
Chrome for Testing JSON ──> milestone list + download URLs
        │
        ├── Chromium source at the version tag ──> parse()        ──┐
        │   (sparse, blobless git clone)           about_flags()     ├─> data/<m>.json.gz ──> merge() ──> site/flags.json
        └── chrome / chrome.dll / Framework     ──> binary_names() ──┘
            for linux64, mac-arm64, win64
```

## 1. Milestones

`update` reads
[`latest-versions-per-milestone-with-downloads.json`](https://googlechromelabs.github.io/chrome-for-testing/latest-versions-per-milestone-with-downloads.json)
from Chrome for Testing (CfT). CfT starts at M113. A milestone is processed when CfT has a `chrome`
download for all three platforms and `data/<m>.json.gz` is missing or holds a different version.
`--force` reprocesses existing milestones, `--only 113,157` and `--limit N` restrict the run.

## 2. Source checkout

`checkout()` keeps one clone of `github.com/chromium/chromium` in the cache
(`~/.cache/chrome-flags-site/src`): `--depth 1 --filter=blob:none`, only the version tag is
fetched. The GitHub mirror is used because Gitiles rate-limits after about 100 files.

The sparse checkout contains:

| pattern | for |
|---|---|
| `*switch*.{cc,h}`, `*feature*.{cc,h}` | switch and feature definitions |
| `chrome/browser/ui/startup/bad_flags_prompt.cc` | the infobar list |
| `third_party/blink/renderer/platform/runtime_enabled_features.json5` | Blink runtime features |
| `chrome/browser/about_flags.cc`, `chrome/browser/flag_descriptions.{cc,h}` | chrome://flags entries |
| every header `about_flags.cc` includes, plus the `.cc` next to it | switches that chrome://flags sets but no switch file defines |

The last row needs a second pass: the include list is only known after `about_flags.cc` is checked
out. Together this is about 2,000 files per milestone.

## 3. Parsing the source

`parse()` reads every checked-out `.cc`/`.h` once and returns `{name: meta}` plus a map from C++
constant to flag name (`kDisableGpu` → `disable-gpu`), which the infobar list and chrome://flags
need.

**Switches** come only from files named `*_switches.cc`, `*_switch.h`, `*_switch_list.cc` and
similar (not `*switcher*`):

```cpp
// Disables GPU hardware acceleration.
const char kDisableGpu[] = "disable-gpu";
NETWORK_SWITCH(kIgnoreCertificateErrors, "ignore-certificate-errors")
```

Switch files also hold values (`kProcessTypeController = "controller"` next to
`kProcessType = "type"`). `is_value()` drops a constant if a shorter constant is its prefix but
its string does not extend that constant's string, or if it ends in `Name` and the string does not
match. This is a heuristic; it loses `launch-time-ticks`.

**Features** come from every `BASE_FEATURE`, in all forms seen between M113 and today:

```cpp
BASE_FEATURE(kVulkan, "Vulkan", base::FEATURE_DISABLED_BY_DEFAULT);
BASE_FEATURE(kVulkan, base::FEATURE_DISABLED_BY_DEFAULT);              // name = constant without k
BASE_FEATURE(AcceleratedVideoDecodeLinuxGL, base::DISABLED);           // M141 transition, no k
BASE_FEATURE(CalcProvider::kCalcProvider, "OmniboxCalcProvider", enable_if(IS_DESKTOP));
DEFINE_FEATURE_WITH_MOBILE_COUNTRY_RESTRICTION(kX)                     // macros wrapping BASE_FEATURE
```

The arguments are read with a bracket counter, so `#if` branches inside the call work. The default
state is evaluated per OS: `preprocess()` resolves `#if/#elif/#else/#endif` with
`BUILDFLAG(IS_LINUX)` etc. set for that OS, other `BUILDFLAG(...)` count as true, `defined(...)`
as false. `feature_default()` then maps the token: `FEATURE_ENABLED_BY_DEFAULT`, `ENABLED`,
`enable_if(...)`, `enabled_by_default_desktop_only`, ... → `on`/`off`. Anything else (a
constant such as `kFeatureDefaultStateControlledByV8`) is kept as text.

**Blink runtime features** come from `runtime_enabled_features.json5`, with the comment above each
entry and its `status`.

The `doc` of a flag is the `//` comment block directly above its definition. Many definitions have
none; the page then says "No comment in source."

## 4. Infobar

`infobar()` reads the arrays `kBadFlags` and `kBadFeatureFlagsInAboutFlags` from
`bad_flags_prompt.cc` after preprocessing for each OS and resolves their constants. These flags
make Chrome show "You are using an unsupported command-line flag". The warning can only be
suppressed by the enterprise policy `CommandLineFlagSecurityWarningsEnabled`.

## 5. chrome://flags

`about_flags()` parses the entries of `about_flags.cc`:

```cpp
{"enable-vulkan", flag_descriptions::kEnableVulkanName,
 flag_descriptions::kEnableVulkanDescription, kOsLinux | kOsAndroid,
 FEATURE_VALUE_TYPE(features::kVulkan)},
```

- `name` and `desc` come from `flag_descriptions.{cc,h}` (adjacent string literals are joined).
- `os` comes from the `kOs*` mask; only Linux, macOS and Windows are kept.
- The target flag is every switch or feature constant in the value macro. For
  `MULTI_VALUE_TYPE(kChoices)` the choices array is read; choices that pass
  `switches::kEnableFeatures, "A,B:param/value"` or a named feature list target those features.
  `ENABLE_DISABLE_VALUE_TYPE_AND_VALUE` is handled the same way.
- If several entries target the same flag, the first one wins.

In M157, 796 of the 803 desktop entries are linked. The remaining 7 target names that are not in
the official binaries (behind `#if !defined(OFFICIAL_BUILD)` or a disabled build flag).

## 6. Binary presence

`binary_names()` downloads the CfT zip per platform, opens the browser binary (`chrome`,
`chrome.dll` or `Google Chrome for Testing Framework`, matched by name), splits it on NUL bytes and
keeps the strings reversed and sorted. A name is present if it is a suffix of one of those strings;
suffix, because the linker merges `"WebGPU"` into the tail of `"...UnsafeWebGPU"`. Lookup is a
binary search.

Only names that are present on at least one OS are written. This removes platform-only code
(Android, ChromeOS), tests and anything not compiled into the official build.

`resource` re-runs the source part for existing `data/` files and keeps their presence, so parser
changes can be applied without downloading 27 GB. It cannot add names that were not present before;
that needs `update --force`.

## 7. Data format

`data/<milestone>.json.gz`, one record per milestone:

```json
{"milestone": 157, "version": "157.0.8097.0", "flags": {
  "Vulkan": {
    "kind": ["feature"], "doc": "Enable Vulkan graphics backend ...", "file": "ui/gl/gl_switches.cc",
    "os": ["linux", "mac", "win"],
    "default": {"linux": "off", "mac": "off", "win": "off"},
    "about": {"id": "enable-vulkan", "name": "Vulkan", "desc": "Use vulkan as the graphics backend.", "os": ["linux"]}
  }}}
```

Optional keys: `infobar` (list of OSes), `default` (features), `status` (Blink), `about`.
A name can have several kinds (a switch and a feature with the same name).

`site` merges all records into `site/flags.json`:

```json
{"versions": {"113": "113.0.5672.63", "...": "..."},
 "flags": {"Vulkan": {
   "kind": ["feature"], "doc": "...", "file": "ui/gl/gl_switches.cc",
   "os": {"linux": [[113, 157]], "mac": [[113, 157]], "win": [[113, 157]]},
   "default": {"linux": [[113, 157, "off"]], "...": []},
   "about": {"id": "enable-vulkan", "name": "Vulkan", "desc": "...", "os": {"linux": [[113, 157]]}}}}}
```

Every per-OS value becomes a list of milestone ranges `[from, to]` or `[from, to, value]`.
`doc`, `file`, `status` and the chrome://flags text come from the newest milestone.

## 8. The page

`site/index.html` is plain HTML and JavaScript, no build step. It loads `flags.json` (about 4 MB)
once and filters in the browser. The version filter shows a flag if one of its ranges covers the
milestone; the OS filter does the same per OS. The timeline compares consecutive milestones.
State is kept in the URL hash.

## 9. GitHub Action

`.github/workflows/update.yml` runs daily at 05:17 UTC and on manual dispatch:

1. restore the Chromium clone from the Actions cache,
2. `build.py update --limit 10` (manual: `limit`, and `rebuild` for `--force`),
3. commit changed `data/` files,
4. `build.py site`, upload `site/` and deploy it to GitHub Pages.

A full rebuild of all milestones takes about 40 minutes on a GitHub runner.

## 10. Changing the parser

1. Add a failing case to `test_build.py` (the tests are offline and take milliseconds).
2. Fix `build.py`.
3. `./build.py resource --only 157` and compare `data/157.json.gz` with `git diff --stat` or a short
   script; `./build.py site` and check the page locally.
4. If the change can add new names, the presence check needs binaries: `./build.py update --only 157 --force`.
   After merging, trigger the workflow with `rebuild` to apply it to all milestones.
