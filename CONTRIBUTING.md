# Contributing (internal)

This is a proprietary repository; contributions come from the DeepVertex team and
approved contractors. By contributing you agree your work is owned by DeepVertex.

## Dev environment

```bash./install/install_topside.sh
cd gui_t1.venv/bin/python../tests/test_console.py     # must be PASS before any PR
```

## Ground rules

- **English only** for every artefact: code, comments, docs, commit messages, UI
  strings. The UI ships to international clients; mixed-language UI strings are a bug.
- **Tests before push**: run the suite + the three probes. New features need a new
  numbered check in `tests/test_console.py` following the existing style
  (`try: … print("[n] …: ", ok); lulus &= bool(ok)`).
- **Layout changes**: after touching panels/tables/dialogs run `probe_scroll.py` —
  a panel wider than its rail viewport is a regression (it will be clipped with no
  way to scroll). Fix by restructuring the panel, not by hiding the scrollbar.
- **Safety code is sacred**: dead-man, mapping-page blocking, storage guard and the
  arm pre-flight gate may only be changed with a dedicated test that proves the
  behaviour still holds (see checks [17], [27], [29], [30]).
- **Theme**: never hard-code colors/sizes; use tokens from `theme.py` and `theme.px()`
  so all scales (100/120/140%) stay correct.

## Commit style

Conventional-ish with a scope, e.g.:

```
feat(padmap): live-highlight diagram with named presets
fix(preflight): MAG no longer false-FAULTs before parameter download
docs(install): bundling h264parse without sudo
```

Keep the body about *why*, not *what* (the diff shows what).

## Release process

1. Version bump in `rov_console_real.py` (session.json `versi`) + `CHANGELOG.md`.
2. Full suite + probes green on the dev machine, then on the field laptop with the
   real ROV connected (linkcheck + a quick arm/teleop/record smoke).
3. `git tag vX.Y.Z && git push --tags`; attach notes from the changelog.
4. Packaging for clients (binary-only artifact + license agreement) is produced by
   the closed-source delivery pipeline, not from this repo's tree directly.
