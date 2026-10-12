# Contributing

This is a personal helper, built with an AI coding agent. Contributions are welcome, from a one-line
issue to a parser fix.

## Reporting a wrong or missing flag

Open an issue with:

- the flag (`--some-switch`, `SomeFeature`, or the chrome://flags id),
- the Chrome version and OS,
- what the page shows and what is right, ideally with a link to the definition on
  [source.chromium.org](https://source.chromium.org/chromium/chromium/src).

## Changing the code

- Read [docs/how-it-works.md](docs/how-it-works.md) first; section 10 describes the workflow.
- Every parser change comes with a case in `test_build.py`: `python3 -m unittest test_build`.
- Standard library only, no new dependencies. The page stays one HTML file without a build step.
- Do not commit `data/` from a local run in the same pull request as a parser change; the
  workflow regenerates `data/` after the merge (`rebuild`).

## License

By contributing you agree that your contribution is licensed under the MIT license of this
repository.
