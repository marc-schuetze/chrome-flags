# chrome-flags

Every Chromium command-line flag, per Chrome milestone and desktop OS, as one searchable page.

Each flag is a key with metadata attached:

```json
"enable-unsafe-webgpu": {
  "kind": ["switch"], "doc": "...", "file": "gpu/config/gpu_switches.cc",
  "os": {"linux": [[113, 157]], "mac": [[113, 157]], "win": [[113, 157]]},
  "infobar": {"linux": [[113, 157]], "mac": [[113, 157]], "win": [[113, 157]]}
}
```

- `kind`: `switch` (`--x`), `feature` (`--enable-features=X`), `blink` (`--enable-blink-features=X`).
- `doc`, `file`: the `//` comment above the definition and its file, from the newest milestone.
- `os`: milestone ranges in which the name occurs in the official binary of that OS
  (Chrome for Testing linux64, mac-arm64, win64).
- `infobar`: ranges in which `chrome/browser/ui/startup/bad_flags_prompt.cc` lists the flag
  for that OS, i.e. Chrome shows "You are using an unsupported command-line flag".
- `default` (features): `on`/`off` per OS and range, from the `#if` branches of `BASE_FEATURE`.
- `status` (Blink): from `runtime_enabled_features.json5`, newest milestone.
- `about`: the chrome://flags entry that sets this flag (`chrome/browser/about_flags.cc`):
  `id` (`chrome://flags/#id`), `name` and `desc` from `flag_descriptions`, `os` ranges from its `kOs*` mask.

## Build

```sh
./build.py update --only 155      # one milestone -> data/155.json.gz (~2.5 min, ~600 MB download, ~3 GB RAM)
./build.py update --limit 10      # next 10 new or changed milestones
./build.py resource               # recompute source metadata of data/ without downloads (~25 s/milestone)
./build.py site                   # data/ -> site/flags.json
python3 -m http.server -d site    # http://localhost:8000
python3 -m unittest test_build
```

The GitHub Action in `.github/workflows/update.yml` runs this daily and deploys `site/` to Pages.

## Limits

- Presence is a string match against the binary, so a short name that only occurs inside
  a longer string can count as present.
- Switch files also hold switch *values* (`kProcessTypeController = "controller"`); a naming
  heuristic drops them and loses about one real switch per build (`launch-time-ticks`).
- Switches and features defined outside `*switch*` / `*feature*` files are missed, except features
  that chrome://flags references (checked by name against the binary).
- No Android, ChromeOS or iOS: Chrome for Testing has no binaries for them.
- Official Chrome builds only. Distribution Chromium (Debian, Mint, Arch) can differ.
- Milestones not processed are bridged: a range 113-157 does not prove every milestone in between.
