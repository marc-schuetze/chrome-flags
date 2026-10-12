# chrome-flags

Every Chromium command-line flag and chrome://flags entry, per Chrome milestone and desktop OS,
on one searchable page.

**Live: https://marc-schuetze.github.io/chrome-flags/**

> **This is an AI-vibe-coded personal helper.** Code, data pipeline and docs were written by an AI
> coding agent under my direction, because I wanted a better way to search Chrome flags: which
> flags exist, what they do, on which version and OS, and why Chrome shows "You are using an
> unsupported command-line flag" for some of them. It is not affiliated with Google or the
> Chromium project. The data is extracted with regexes, not a compiler: use it as an index into the
> Chromium source, not as a specification. Contributions welcome, see
> [CONTRIBUTING.md](CONTRIBUTING.md).

## What you get

- **All flags in one list**, from Chrome 113 to the newest milestone that
  [Chrome for Testing](https://googlechromelabs.github.io/chrome-for-testing/) publishes
  (about 11,000 names in total):
  - switches: `--use-angle=vulkan`
  - features: `--enable-features=Vulkan` / `--disable-features=Vulkan`
  - Blink runtime features: `--enable-blink-features=WebGPUDeveloperFeatures`
- **Per flag:**
  - the `//` comment above its definition in the source, with a link to the file;
  - the milestones in which it exists on Linux, macOS and Windows;
  - the default state of each feature per OS;
  - whether Chrome shows the "unsupported command-line flag" warning (infobar) for it;
  - the chrome://flags entry that sets it, with id, name and description.
- **Filters:** version, OS, kind, listed in chrome://flags or not, shows the infobar or not.
- **Timeline:** per milestone, which flags were added or removed, which feature defaults changed
  and which flags entered or left chrome://flags.
- **Check my flags:** paste a command line; each flag is checked against the selected version and
  OS (unknown, warning, present).

Filters and search are kept in the URL, so a view can be shared, for example
[all WebGPU flags that are in chrome://flags](https://marc-schuetze.github.io/chrome-flags/#q=webgpu&about=yes).

## How it works

A GitHub Action runs every day. For each Chrome milestone that is new or has a new version, it

1. checks out the relevant part of the Chromium source at that version tag (sparse, blobless clone
   of the GitHub mirror, about 2,000 files),
2. extracts switches, features, Blink features, chrome://flags entries and the infobar list,
3. downloads the official Chrome for Testing build for Linux, macOS and Windows and keeps a name
   for an OS only if it occurs as a string in that binary,
4. writes one `data/<milestone>.json.gz`, commits it, merges all milestones into
   `site/flags.json` and deploys `site/` to GitHub Pages.

Details, data format and the parsing rules: [docs/how-it-works.md](docs/how-it-works.md).

## Run it yourself

Python 3 (standard library only) and git.

```sh
./build.py update --only 157      # one milestone -> data/157.json.gz (1-3 min, ~600 MB download)
./build.py update                 # all new or changed milestones
./build.py update --force         # reprocess everything (45 milestones: ~40 min on a GitHub runner)
./build.py resource               # re-parse the source for data/ without downloading binaries
./build.py site                   # data/ -> site/flags.json
python3 -m http.server -d site    # http://localhost:8000
python3 -m unittest test_build    # parser tests, offline
```

The Chromium checkout is cached in `~/.cache/chrome-flags-site` (override with
`CHROME_FLAGS_CACHE`).

## Known gaps

- **Not every flag Chromium reads.** Switches are taken from `*switches*` files, features from
  `BASE_FEATURE` definitions in `*feature*` files and from the files that chrome://flags includes.
  A switch that is defined elsewhere and not offered in chrome://flags is missed
  (`--launch-time-ticks`, for example).
- **Other flag systems are not covered:** V8 (`--js-flags`), Dawn toggles, ANGLE features,
  `--vmodule`, feature parameters (`Feature:param/value`) except where chrome://flags offers them.
- **Desktop only, official builds only.** No Android, ChromeOS or iOS; distribution builds of
  Chromium (Debian, Arch, Mint, ...) can have different build flags.
- **Presence is a string match** against the binary. A short name that only occurs inside a longer
  string can count as present.
- **Defaults are evaluated per OS from `#if BUILDFLAG(IS_X)` branches.** Other build flags count
  as on; about 240 defaults depend on constants and are shown as the constant name.
- **Gaps between milestones are bridged.** A range 113–157 does not prove every milestone in
  between.

## Contributing

Issues and pull requests are welcome, especially for flags that are missing, wrongly named or
linked to the wrong chrome://flags entry. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Code: [MIT](LICENSE). The flag names and descriptions in `data/` and on the site are extracted from
the [Chromium source](https://source.chromium.org/chromium/chromium/src), © The Chromium Authors,
BSD 3-Clause license.
