# Changelog

## v1.1.0
2025-8-21
Localization support for 24 additional languages.

## v1.0.13 - 2025-08-17
### Features
- **Pip in Jail Easter Egg**: Added fun status messages like “Pip is in jail, crying silently. 😭🔒” to `omnipkg status` for a delightful user experience.
- **AGPL License**: Adopted GNU Affero General Public License v3 or later for full open-source compliance.
- **Commercial License Option**: Added `COMMERCIAL_LICENSE.md` for proprietary use cases, with contact at omnipkg@proton.me.
- **Improved License Handling**: Updated `THIRD_PARTY_NOTICES.txt` to list only direct dependencies, with license texts in `licenses/`.

### Bug Fixes
- Reduced deduplication to properly handle binaries, as well as ensuring python modules are kept safe. 

### Improvements
- Added AGPL notice to `omnipkg/__init__.py` with dynamic version and dependency loading.
- Enhanced `generate_licenses.py` to preserve existing license files and moved it to `scripts/`.
- Removed `examples/testflask.py` and `requirements.txt` for a leaner package.
- Updated `MANIFEST.in` to include only necessary files and exclude `examples/`, `scripts/`, and `tests/`.

### Notes
- Direct dependencies: `redis==6.4.0`, `packaging==25.0`, `requests==2.32.4`, `python-magic==0.4.27`, `aiohttp==3.12.15`, `tqdm==4.67.1`.
- Transitive dependency licenses available in `licenses/` for transparency.

## v1.0.9 - 2025-08-11
### Notes
- Restored stable foundation of v1.0.7.
- Removed experimental features from v1.0.8 for maximum stability.
- Recommended for production use.