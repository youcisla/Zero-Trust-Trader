# Verification record — 9 November 2026

Baseline source revision: `8e08e21310b66ff67b88e115e19a047c68e5b5c8`.

## Executed checks

| Check | Result | What it establishes |
| --- | --- | --- |
| Python unittest discovery with MT5 forcibly stubbed before imports and non-loopback socket connections refused | **137 passed; 0 failures, errors or skips** | Existing covered behavior on this environment; no live terminal/provider required |
| `cargo build --release --locked --offline` in `dashboard/` | **Passed** | Local cached dependency resolution compiles the release application |
| `cargo clippy --locked --offline -- -D warnings` | **Passed** | Existing Rust code satisfies the invoked compiler/lint checks |
| `cargo test --locked --offline` | **3 passed; 0 failed/ignored** | Bounded-tail selection, missing versus empty files, Unicode clipping |
| Ruff isolated correctness selection against baseline tracked Python files | **Passed** | No findings from E9/F63/F7/F82 on those files |
| Broad `python -m ruff check --output-format concise .` | **97 diagnostics, nonzero exit** | Optional wider lint baseline using this installation's discovered configuration; not a committed project policy or 97 security defects |
| pip-audit in an isolated uv tool environment against the installed project dependency closure | **Exit 0; no known vulnerabilities reported for 6 packages** | Advisory-database result for these exact versions at audit time |
| `audit_probes.py` | **9 defect cases reproduced** | Guard, note schema, CLI exit and export defects on synthetic inputs/temp files |
| Isolated safety-agent probes | **2 cases reproduced** | `positions_get=None` becomes healthy-flat; partial close retries original volume |
| Isolated statistics-agent probes | **Horizon, short fill, negative-mean gate and mocked-null examples reproduced** | Specific arithmetic/control-flow defects, not market profitability or empirical false-positive calibration |
| Postman CLI checks | **Version: managed Private Cloud manifest read failure; status: exit 1, unknown command** | No Postman contract run or workspace status could be established |

Environment observed: Python **3.14.6**, Cargo **1.96.0**, rustc **1.96.0**, Ruff **0.17.4**. This is not the CI platform/version matrix. The local Cargo.lock existed and supported `--locked`, but it is not tracked, so success does not establish clean-clone reproducibility.

Final artifact checks: all **37 baseline tracked Python files** parsed successfully with `ast.parse`; the coverage inventory contains **69 baseline tracked files**; register totals reconcile to **14 P1 / 14 P2 / 2 P3**; no broken local Markdown links were found. An automated and independent privacy review found no matched installation paths, protected identifier or key-like tokens in the audit artifacts; this is a scoped disclosure check, not proof of detecting every possible identifier. The final tracked diff was empty and Git reported only the new audit directory as untracked.

The broader Ruff check was not autofixed. Many diagnostics concern import ordering, unused imports, datetime style and broad exceptions. Broad lint should be triaged under a checked-in supported-version configuration; it is lower priority than the semantic defects. No mutation testing was performed on the production execution tree.

## Python test command

The documented entry point is `python -m unittest discover -s tests -v`. The audit used the equivalent discovery/runner API with an extra pre-import stub and network guard:

```python
import sys, socket, unittest
sys.path.insert(0, "tests")
from _mt5stub import install
install()
original_connect = socket.socket.connect
def guarded_connect(sock, address):
    if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError("audit blocked non-loopback network connection")
    return original_connect(sock, address)
socket.socket.connect = guarded_connect
suite = unittest.defaultTestLoader.discover("tests")
result = unittest.TextTestRunner().run(suite)
sys.exit(not result.wasSuccessful())
```

The actual run captured incidental script diagnostics in memory rather than copying them into the report. The unittest runner reported 137 tests in 13.564 seconds; discovery/import and wrapper time was 50.43 seconds. Loopback mock HTTP servers in the existing desk tests were allowed. None of these results constitutes an upstream DeepSeek or bridge contract test.

The isolated Ruff command was constructed from `git ls-files '*.py'` and:

```text
python -m ruff check --isolated --select E9,F63,F7,F82 --output-format concise <tracked Python files>
```

## Dependency advisory check

The audit obtained installed versions through `importlib.metadata`, followed active dependency markers with `packaging.requirements`, wrote the exact closure to a temporary requirements file, and ran:

```text
uvx --from pip-audit pip-audit --disable-pip --no-deps --progress-spinner off --format json -r <temporary exact-version requirements>
```

| Package | Installed version | Reported vulnerabilities |
| --- | --- | --- |
| MetaTrader5 | 5.0.6231 | 0 |
| numpy | 2.5.0 | 0 |
| pandas | 3.0.3 | 0 |
| python-dateutil | 2.9.0.post0 | 0 |
| six | 1.17.0 | 0 |
| tzdata | 2026.2 | 0 |

The tool warned that pinned dependencies should ideally be fully hashed. No production packages were installed or upgraded; uv downloaded an isolated diagnostic environment. The result covers the installed closure, not every version allowed by the broad requirements, Rust crates, browser vendors, the external bridge, the terminal application or unknown vulnerabilities. These limits agree with [pip-audit's security model](https://github.com/pypa/pip-audit#security-model): it checks known Python-package vulnerabilities and is not a static code or malicious-package audit. `cargo-audit` was not installed, and no Rust advisory scan is claimed.

## Reproduction artifacts

Run from the repository root:

```text
python -X utf8 docs/audit-2026-11-09/audit_probes.py
```

The script installs the MT5 stub before importing terminal-facing modules, refuses all socket connections, mocks external observations and uses a temporary directory. It does not read `.env`, the private policy, account state or production ledger. The results in [probe_results.json](probe_results.json) contain synthetic data only.

These are baseline diagnostic reproductions. `defect_reproduced: true` means the current code exhibited the defect. This is deliberately different from a regression suite that requires safe behavior. After fixing each case, add the safe expectation to `tests/` and update the audit disposition rather than preserving the defect to satisfy these observations.

The safety and statistical fragments record additional synthetic inputs and outputs. Their exploratory snippets were not retained as standalone executable artifacts; equivalent missing regression cases and acceptance criteria are documented there. The negative-mean permutation example explicitly mocks null outcomes and is not evidence that a real market produced that null.

## API tooling and unperformed checks

The initial Postman version invocation failed during initialization with: **“A Postman Private Cloud manifest is deployed (registry policy) but could not be read. Contact your IT administrator.”** The final `postman status` check instead returned exit 1 and **“unknown command 'status'”**. API review continued through source-contract inspection and existing local fixtures. No registry/config bypass or cloud initialization was attempted. If Postman execution is required later, resolve the managed manifest access and verify the installed CLI supports the skill's commands; signup or billing changes are not indicated by either error.

Not performed: live trading/terminal/dashboard access, browser interaction or exploitation, external model calls, scheduler or registry inspection, private policy/ledger inspection, published-history scan, repository-protection verification, real-market backtests, packaging execution, full vendor/license audit, recovery drill, performance profiling, Rust dependency advisory scan or formal verification. These limits are attached to the relevant findings and must remain visible in any summary of this audit.
