# Secret Migration Tool

**⚠️ WORK IN PROGRESS ⚠️**

This is a command-line utility for migrating secrets between OpenHands instances.

## Status

- ✅ Core CLI functionality implemented
- ⚠️  Multiple retrieval methods (may need simplification)
- ❌ Complete documentation article not yet written
- ❌ Examples need review
- ❌ Testing needed

## Quick Usage

```bash
# From inside a sandbox (recommended)
./oh-secret-migrator.py \
  --source-url $OPENHANDS_API_URL \
  --source-key $OPENHANDS_API_KEY \
  --dest-url https://destination.example.com \
  --dest-key $DEST_API_KEY \
  --from-env
```

## TODO

- [ ] Simplify CLI options
- [ ] Write comprehensive article/guide
- [ ] Add example use cases
- [ ] Test with real instances
- [ ] Document all retrieval methods
- [ ] Add best practices section

## Options

Run with `--help` to see all available options:

```bash
./oh-secret-migrator.py --help
```

Current retrieval methods:
- `--from-env` - Read from environment variables (recommended for sandbox use)
- `--source-service-key` - Use enterprise service API key
- `--values-file` - Load from JSON file
- Manual entry - Interactive prompts (fallback)

## Notes

This tool is functional but documentation and usage patterns need to be finalized before general use.
