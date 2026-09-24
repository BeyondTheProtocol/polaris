# Running Tests in a Fork

This document explains how to run the Polaris test battery from a fork, what tests skip outside the original machine, and which environment variables you need to set.

**[Versión en español](#)** *(TODO: Add Spanish version link)*

---

## Quick Start

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/polaris.git
cd polaris

# Run the full test battery
bash tests/test_all.sh
```

---

## Environment Variables

Some tests require specific environment variables to be set:

### `BTP_REPO`
Path to the main Polaris repository (not your fork). Used by tests that need access to the original machine state.

```bash
export BTP_REPO=/path/to/original/polaris
```

### `BTP_STATE_DIR`
Path to the state directory where Polaris stores runtime data.

```bash
export BTP_STATE_DIR=/path/to/state/dir
```

**Note:** If you're running tests from a fork without access to the original machine, these tests will SKIP gracefully.

---

## Tests That SKIP Outside the Original Machine

The following tests require specific paths or state that only exists on the original machine:

| Test File | Reason for SKIP |
|-----------|----------------|
| `test_fuga.sh` | Needs to be in `~/claudecode` directory |
| `test_halt.sh` | Requires original machine state and paths |
| *(Add more as discovered)* | |

When these tests skip, you'll see output like:
```
SKIP: test_fuga.sh (needs to be in ~/claudecode)
```

This is **expected behavior** when running from a fork. The test battery is designed to fail closed and skip gracefully when dependencies are missing.

---

## Understanding Test Output

The test battery produces three types of results:

- **PASS**: Test ran successfully
- **FAIL**: Test ran but assertion failed (investigate this)
- **SKIP**: Test couldn't run due to missing dependencies (expected in forks)

### Example Output

```bash
$ bash tests/test_all.sh

Running test_unitarios.sh...
PASS: test_kb.py
PASS: test_borde.py

Running test_fuga.sh...
SKIP: needs to be in ~/claudecode

Running test_halt.sh...
SKIP: requires original machine state

---
Results: 2 PASS, 0 FAIL, 2 SKIP
```

---

## Running Specific Test Groups

You can run individual test scripts if you want to focus on specific areas:

```bash
# Run only unit tests
bash tests/test_unitarios.sh

# Run integration tests
bash tests/test_integracion.sh

# Run a specific test file
bash tests/test_kb.py
```

---

## Adding New Tests

When adding new tests to the battery:

1. **Make them portable**: Avoid hardcoding paths specific to the original machine
2. **Handle missing deps gracefully**: If a dependency is missing, SKIP with a clear message
3. **Document requirements**: If a test needs specific env vars or paths, document them here
4. **Use synthetic data**: Prefer synthetic/mock data over real external services when possible

### Example: Portable Test Pattern

```bash
#!/bin/bash
#tests/test_mi_nuevo_test.sh

# Check for required env var
if [ -z "$BTP_REPO" ]; then
    echo "SKIP: BTP_REPO not set"
    exit 0
fi

# Check for required directory
if [ ! -d "$BTP_REPO/tools" ]; then
    echo "SKIP: $BTP_REPO/tools not found"
    exit 0
fi

# Run test...
echo "PASS: mi_nuevo_test"
```

---

## Troubleshooting

### All tests SKIP

Check that you've set the required environment variables:

```bash
echo $BTP_REPO
echo $BTP_STATE_DIR
```

If they're empty, set them as shown in the [Environment Variables](#environment-variables) section.

### Tests FAIL unexpectedly

1. Check that your fork is up to date with the main repo
2. Verify Python dependencies are installed: `pip install -r requirements.txt`
3. Check for error messages in the test output

### Need help?

Open an issue on GitHub with:
- The test file that's failing
- The full error output
- Your environment (OS, Python version, etc.)

---

## See Also

- [Contributing Guide](../CONTRIBUTING.md)
- [What Is Missing](what-is-missing.md)
- [Main README](../README.md)

---

*Last updated: 2026-09-24*
