# Vendored dashboard provenance

On 9 November 2026 the three existing dashboard scripts were compared byte for
byte with the files in these published npm package archives. All matched; the
scripts were not replaced or upgraded. The exact SHA-256 values and package URLs
are recorded in `dashboard/web/vendor/PROVENANCE.json` and tested locally.

| Script | Published package | License carried beside the file |
| --- | --- | --- |
| `react.production.min.js` | [react 18.3.1](https://registry.npmjs.org/react/-/react-18.3.1.tgz) | `LICENSE.react.txt` — MIT |
| `react-dom.production.min.js` | [react-dom 18.3.1](https://registry.npmjs.org/react-dom/-/react-dom-18.3.1.tgz) | `LICENSE.react-dom.txt` — MIT |
| `htm.umd.js` | [htm 3.1.1](https://registry.npmjs.org/htm/-/htm-3.1.1.tgz) | `LICENSE.htm.txt` — Apache 2.0 |

The React DOM embedded version text includes a prerelease build identifier; byte
comparison establishes its match to the named published package rather than
inferring a package version from that text. The license files were taken from
the matching package archives, and existing script copyright notices remain.

Refresh procedure: review an intended upstream package/version, compare its
published archive and notices, update the asset/hash/license manifest together,
then run the offline dashboard and fixture checks. Keep assets embedded and
available offline; do not reintroduce a CDN dependency. This inventory does not
claim a JavaScript vulnerability scan or a legal opinion about all transitive
notices.

The architecture viewer is generated from `docs/architecture.archify.json`.
Regenerate through the documented Archify workflow, review the source and output
together, then rebuild the embedded Rust binary. Its full generated bundle and
third-party internals were not independently audited here; no generated artifact
was hand-edited during these fixes.
