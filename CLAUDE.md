# reaper-mcp — development rules

Read README.md first: architecture, the preset codecs, and the gotchas section
(each one cost a hung bridge to learn).

## Workflow for any change

1. Test against the live REAPER, not just by reading code:
   `uv run python smoke_test.py`, `test_save_adopt.py`, `test_presets.py`
   (the last needs Keys/Bass/Drums tracks holding Keyscape/Trilian/BFD).
   Use a throwaway project tab; never touch the user's songs while testing.
2. Privacy check before every commit — this repo is **public**:
   no local paths, usernames, emails or library locations in tracked files.
3. Commit + push to `origin main` (github.com/Oisub/reaper-mcp).
4. Tell the user to run `/mcp` to reconnect.

## Scope

- This repo is the tool. Songs live in the user's own song folder (see the user-level
  CLAUDE.md), never in `demo/`.
  `demo/` is gitignored scratch for tests; delete test projects when done.
- `song_*.py` are examples of driving the bridge, kept for reference.
- New tools only when `eval_lua` cannot reach the thing (as with plugin presets).
