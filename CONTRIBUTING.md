# Contributing to Magpie

Thanks for helping! Bug reports, ideas, documentation fixes and code are all welcome.

## Before you start

- For anything bigger than a small fix, open an issue first so we can agree on the
  approach.
- Read [AGENTS.md](AGENTS.md): it has the commands, the map of the code, conventions and
  step-by-step recipes (it's written for AI agents, but it's the best guide for people too).

## Development setup

```bash
git clone https://github.com/Coder-ux-coder/magpie.git && cd magpie
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,mcp]"
MAGPIE_DATA_DIR=dev-data python scripts/seed_demo.py             # optional sample notes
MAGPIE_DATA_DIR=dev-data MAGPIE_BRAIN=fake MAGPIE_PASSWORD=dev magpie serve
```

`MAGPIE_BRAIN=fake` replaces Claude with a simple offline stand-in, so you can work on
almost everything without an API key. To try the real thing, set `ANTHROPIC_API_KEY`
instead.

## Pull requests

- Keep each pull request focused on one change.
- Add or update tests. The suite runs offline: see `tests/conftest.py` for the fixtures.
- Run the checks:
  ```bash
  pytest -q
  ruff check src tests scripts && ruff format --check src tests scripts
  node --test "tests/js/*.test.mjs"
  ```
- Update the docs and `CHANGELOG.md` when behaviour changes.
- For UI changes, include a screenshot and check it on a phone-sized screen, in dark mode,
  and with the keyboard only.

## Reporting bugs

Use the bug report template and include your Magpie version, how you run it, and the
server log (with keys, tokens and personal notes removed). Please report security
problems privately; see [SECURITY.md](SECURITY.md).

By contributing you agree that your work is released under the [MIT License](LICENSE) and
that you'll follow the [Code of Conduct](CODE_OF_CONDUCT.md).
