- chore: release v2.2.2
- fix(i18n): finalize Japanese translation
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- refactor: remove undefined name from `__all__`
- refactor: remove reimported module
- refactor: remove unnecessary return statement
- fix(cli): hoist i18n imports to global scope to prevent UnboundLocalError
- feat(i18n): Integrate and propagate i18n across core components

- Updated README.md (x7)
- Updated publish.yml (x6)
- Updated conda_build.yml (x5)
- Updated windows-concurrency-test.yml (x4)
- Updated meta-platforms.yaml (x2)
- Updated main → development (auto-merge conflict fixes)
- Updated main → development after auto-merge
- Updated meta-noarch.yaml

```text
.github/workflows/conda_build.yml                 |   820 +-
 .github/workflows/publish.yml                     |    52 +-
 .github/workflows/windows-concurrency-test.yml    |    18 +-
 CHANGELOG.md                                      |   749 +-
 README.md                                         |   683 +-
 pyproject.toml                                    |     5 +-
 src/omnipkg/CondaGuard.py                         |     2 +-
 src/omnipkg/__init__.py                           |     9 +-
 src/omnipkg/__main__.py                           |    18 +-
 src/omnipkg/apis/local_bridge.py                  |    62 +-
 src/omnipkg/cli.py                                |    47 +-
 src/omnipkg/commands/run.py                       |     9 +-
 src/omnipkg/common_utils.py                       |     5 -
 src/omnipkg/conda-recipes/meta-noarch.yaml        |     8 +-
 src/omnipkg/conda-recipes/meta-platforms.yaml     |    34 +-
 src/omnipkg/core.py                               |   141 +-
 src/omnipkg/dispatcher.py                         |   106 +-
 src/omnipkg/i18n.py                               |    80 +-
 src/omnipkg/installation/metadata_cache.py        |    20 +-
 src/omnipkg/installation/verification_hooks.py    |     2 +-
 src/omnipkg/installation/verification_strategy.py |     4 +-
 src/omnipkg/integration/ci_integration.py         |     3 -
 src/omnipkg/isolation/gpu_ipc.py                  |     2 +-
 src/omnipkg/isolation/patchers.py                 |     3 +-
 src/omnipkg/isolation/resource_monitor.py         |    38 +-
 src/omnipkg/isolation/worker_daemon.py            |    64 +-
 src/omnipkg/loader.py                             |    29 +-
 src/omnipkg/locale/am/LC_MESSAGES/omnipkg.mo      |   Bin 40079 -> 219679 bytes
 src/omnipkg/locale/am/LC_MESSAGES/omnipkg.po      |   357 +-
 src/omnipkg/locale/ar/LC_MESSAGES/omnipkg.mo      |   Bin 38938 -> 239024 bytes
 src/omnipkg/locale/ar/LC_MESSAGES/omnipkg.po      |   154 +-
 src/omnipkg/locale/ar_eg/LC_MESSAGES/omnipkg.po   |     2 +-
 src/omnipkg/locale/bn/LC_MESSAGES/omnipkg.po      |     6 +-
 src/omnipkg/locale/da/LC_MESSAGES/omnipkg.mo      |   Bin 33330 -> 32936 bytes
 src/omnipkg/locale/da/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/de/LC_MESSAGES/omnipkg.mo      |   Bin 35025 -> 34578 bytes
 src/omnipkg/locale/de/LC_MESSAGES/omnipkg.po      |    12 +-
 src/omnipkg/locale/es/LC_MESSAGES/omnipkg.mo      |   Bin 35486 -> 35149 bytes
 src/omnipkg/locale/es/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/fr/LC_MESSAGES/omnipkg.mo      |   Bin 35923 -> 35303 bytes
 src/omnipkg/locale/fr/LC_MESSAGES/omnipkg.po      |    16 +-
 src/omnipkg/locale/hi/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/hr/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/id/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/it/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/ja/LC_MESSAGES/omnipkg.mo      |   Bin 38713 -> 187760 bytes
 src/omnipkg/locale/ja/LC_MESSAGES/omnipkg.po      | 10437 ++++++++++++--------
 src/omnipkg/locale/ko/LC_MESSAGES/omnipkg.mo      |   Bin 35864 -> 35489 bytes
 src/omnipkg/locale/ko/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/nl/LC_MESSAGES/omnipkg.mo      |   Bin 34128 -> 33773 bytes
 src/omnipkg/locale/nl/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/no/LC_MESSAGES/omnipkg.mo      |   Bin 33293 -> 32905 bytes
 src/omnipkg/locale/no/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/pl/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/pt_BR/LC_MESSAGES/omnipkg.mo   |   Bin 35016 -> 34521 bytes
 src/omnipkg/locale/pt_BR/LC_MESSAGES/omnipkg.po   |    10 +-
 src/omnipkg/locale/ru/LC_MESSAGES/omnipkg.mo      |   Bin 43249 -> 42785 bytes
 src/omnipkg/locale/ru/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/sv/LC_MESSAGES/omnipkg.mo      |   Bin 33583 -> 31276 bytes
 src/omnipkg/locale/sv/LC_MESSAGES/omnipkg.po      |    76 +-
 src/omnipkg/locale/sw/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/tr/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/vi/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/zh_CN/LC_MESSAGES/omnipkg.mo   |   Bin 14946 -> 197424 bytes
 src/omnipkg/locale/zh_CN/LC_MESSAGES/omnipkg.po   |  1844 ++--
 src/omnipkg/package_meta_builder.py               |    14 +-
 src/omnipkg/utils/flask_port_finder.py            |     2 +-
 src/tests/test_cli_healing.py                     |     2 +-
 src/tests/test_concurrent_install.py              |    42 +-
 src/tests/test_flask_port_finder.py               |     3 +-
 src/tests/test_flask_port_finder_universal.py     |    30 +-
 src/tests/test_loader_stress_test.py              |    61 +-
 src/tests/test_multiverse_healing.py              |     6 +-
 src/tests/test_old_flask.py                       |     2 +-
 src/tests/test_old_rich.py                        |     6 +-
 src/tests/test_swap_install.py                    |     4 +-
 src/tests/test_version_combos.py                  |     2 +-
 77 files changed, 9039 insertions(+), 7138 deletions(-)
```

- chore: release v2.2.2
- fix(i18n): finalize Japanese translation
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- refactor: remove undefined name from `__all__`
- refactor: remove reimported module
- refactor: remove unnecessary return statement
- fix(cli): hoist i18n imports to global scope to prevent UnboundLocalError
- feat(i18n): Integrate and propagate i18n across core components

- Updated README.md (x7)
- Updated publish.yml (x6)
- Updated conda_build.yml (x5)
- Updated windows-concurrency-test.yml (x4)
- Updated meta-platforms.yaml (x2)
- Updated main → development (auto-merge conflict fixes)
- Updated main → development after auto-merge
- Updated meta-noarch.yaml

```text
.github/workflows/conda_build.yml                 |   820 +-
 .github/workflows/publish.yml                     |    52 +-
 .github/workflows/windows-concurrency-test.yml    |    18 +-
 CHANGELOG.md                                      |   749 +-
 README.md                                         |   683 +-
 pyproject.toml                                    |     5 +-
 src/omnipkg/CondaGuard.py                         |     2 +-
 src/omnipkg/__init__.py                           |     9 +-
 src/omnipkg/__main__.py                           |    18 +-
 src/omnipkg/apis/local_bridge.py                  |    62 +-
 src/omnipkg/cli.py                                |    47 +-
 src/omnipkg/commands/run.py                       |     9 +-
 src/omnipkg/common_utils.py                       |     5 -
 src/omnipkg/conda-recipes/meta-noarch.yaml        |     8 +-
 src/omnipkg/conda-recipes/meta-platforms.yaml     |    34 +-
 src/omnipkg/core.py                               |   141 +-
 src/omnipkg/dispatcher.py                         |   106 +-
 src/omnipkg/i18n.py                               |    80 +-
 src/omnipkg/installation/metadata_cache.py        |    20 +-
 src/omnipkg/installation/verification_hooks.py    |     2 +-
 src/omnipkg/installation/verification_strategy.py |     4 +-
 src/omnipkg/integration/ci_integration.py         |     3 -
 src/omnipkg/isolation/gpu_ipc.py                  |     2 +-
 src/omnipkg/isolation/patchers.py                 |     3 +-
 src/omnipkg/isolation/resource_monitor.py         |    38 +-
 src/omnipkg/isolation/worker_daemon.py            |    64 +-
 src/omnipkg/loader.py                             |    29 +-
 src/omnipkg/locale/am/LC_MESSAGES/omnipkg.mo      |   Bin 40079 -> 219679 bytes
 src/omnipkg/locale/am/LC_MESSAGES/omnipkg.po      |   357 +-
 src/omnipkg/locale/ar/LC_MESSAGES/omnipkg.mo      |   Bin 38938 -> 239024 bytes
 src/omnipkg/locale/ar/LC_MESSAGES/omnipkg.po      |   154 +-
 src/omnipkg/locale/ar_eg/LC_MESSAGES/omnipkg.po   |     2 +-
 src/omnipkg/locale/bn/LC_MESSAGES/omnipkg.po      |     6 +-
 src/omnipkg/locale/da/LC_MESSAGES/omnipkg.mo      |   Bin 33330 -> 32936 bytes
 src/omnipkg/locale/da/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/de/LC_MESSAGES/omnipkg.mo      |   Bin 35025 -> 34578 bytes
 src/omnipkg/locale/de/LC_MESSAGES/omnipkg.po      |    12 +-
 src/omnipkg/locale/es/LC_MESSAGES/omnipkg.mo      |   Bin 35486 -> 35149 bytes
 src/omnipkg/locale/es/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/fr/LC_MESSAGES/omnipkg.mo      |   Bin 35923 -> 35303 bytes
 src/omnipkg/locale/fr/LC_MESSAGES/omnipkg.po      |    16 +-
 src/omnipkg/locale/hi/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/hr/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/id/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/it/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/ja/LC_MESSAGES/omnipkg.mo      |   Bin 38713 -> 187760 bytes
 src/omnipkg/locale/ja/LC_MESSAGES/omnipkg.po      | 10437 ++++++++++++--------
 src/omnipkg/locale/ko/LC_MESSAGES/omnipkg.mo      |   Bin 35864 -> 35489 bytes
 src/omnipkg/locale/ko/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/nl/LC_MESSAGES/omnipkg.mo      |   Bin 34128 -> 33773 bytes
 src/omnipkg/locale/nl/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/no/LC_MESSAGES/omnipkg.mo      |   Bin 33293 -> 32905 bytes
 src/omnipkg/locale/no/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/pl/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/pt_BR/LC_MESSAGES/omnipkg.mo   |   Bin 35016 -> 34521 bytes
 src/omnipkg/locale/pt_BR/LC_MESSAGES/omnipkg.po   |    10 +-
 src/omnipkg/locale/ru/LC_MESSAGES/omnipkg.mo      |   Bin 43249 -> 42785 bytes
 src/omnipkg/locale/ru/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/sv/LC_MESSAGES/omnipkg.mo      |   Bin 33583 -> 31276 bytes
 src/omnipkg/locale/sv/LC_MESSAGES/omnipkg.po      |    76 +-
 src/omnipkg/locale/sw/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/tr/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/vi/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/zh_CN/LC_MESSAGES/omnipkg.mo   |   Bin 14946 -> 197424 bytes
 src/omnipkg/locale/zh_CN/LC_MESSAGES/omnipkg.po   |  1844 ++--
 src/omnipkg/package_meta_builder.py               |    14 +-
 src/omnipkg/utils/flask_port_finder.py            |     2 +-
 src/tests/test_cli_healing.py                     |     2 +-
 src/tests/test_concurrent_install.py              |    42 +-
 src/tests/test_flask_port_finder.py               |     3 +-
 src/tests/test_flask_port_finder_universal.py     |    30 +-
 src/tests/test_loader_stress_test.py              |    61 +-
 src/tests/test_multiverse_healing.py              |     6 +-
 src/tests/test_old_flask.py                       |     2 +-
 src/tests/test_old_rich.py                        |     6 +-
 src/tests/test_swap_install.py                    |     4 +-
 src/tests/test_version_combos.py                  |     2 +-
 77 files changed, 9039 insertions(+), 7138 deletions(-)
```

- fix(i18n): finalize Japanese translation
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- chore: release v2.2.2
- refactor: remove undefined name from `__all__`
- refactor: remove reimported module
- refactor: remove unnecessary return statement
- fix(cli): hoist i18n imports to global scope to prevent UnboundLocalError
- feat(i18n): Integrate and propagate i18n across core components

- Updated README.md (x7)
- Updated publish.yml (x6)
- Updated conda_build.yml (x5)
- Updated windows-concurrency-test.yml (x4)
- Updated meta-platforms.yaml (x2)
- Updated main → development (auto-merge conflict fixes)
- Updated main → development after auto-merge
- Updated meta-noarch.yaml

```text
.github/workflows/conda_build.yml                 |   820 +-
 .github/workflows/publish.yml                     |    52 +-
 .github/workflows/windows-concurrency-test.yml    |    18 +-
 CHANGELOG.md                                      |  1255 +++
 README.md                                         |   683 +-
 pyproject.toml                                    |     5 +-
 src/omnipkg/CondaGuard.py                         |     2 +-
 src/omnipkg/__init__.py                           |     9 +-
 src/omnipkg/__main__.py                           |    18 +-
 src/omnipkg/apis/local_bridge.py                  |    62 +-
 src/omnipkg/cli.py                                |    47 +-
 src/omnipkg/commands/run.py                       |     9 +-
 src/omnipkg/common_utils.py                       |     5 -
 src/omnipkg/conda-recipes/meta-noarch.yaml        |     8 +-
 src/omnipkg/conda-recipes/meta-platforms.yaml     |    34 +-
 src/omnipkg/core.py                               |   141 +-
 src/omnipkg/dispatcher.py                         |   106 +-
 src/omnipkg/i18n.py                               |    80 +-
 src/omnipkg/installation/metadata_cache.py        |    20 +-
 src/omnipkg/installation/verification_hooks.py    |     2 +-
 src/omnipkg/installation/verification_strategy.py |     4 +-
 src/omnipkg/integration/ci_integration.py         |     3 -
 src/omnipkg/isolation/gpu_ipc.py                  |     2 +-
 src/omnipkg/isolation/patchers.py                 |     3 +-
 src/omnipkg/isolation/resource_monitor.py         |    38 +-
 src/omnipkg/isolation/worker_daemon.py            |    64 +-
 src/omnipkg/loader.py                             |    29 +-
 src/omnipkg/locale/am/LC_MESSAGES/omnipkg.mo      |   Bin 40079 -> 219679 bytes
 src/omnipkg/locale/am/LC_MESSAGES/omnipkg.po      |   357 +-
 src/omnipkg/locale/ar/LC_MESSAGES/omnipkg.mo      |   Bin 38938 -> 239024 bytes
 src/omnipkg/locale/ar/LC_MESSAGES/omnipkg.po      |   154 +-
 src/omnipkg/locale/ar_eg/LC_MESSAGES/omnipkg.po   |     2 +-
 src/omnipkg/locale/bn/LC_MESSAGES/omnipkg.po      |     6 +-
 src/omnipkg/locale/da/LC_MESSAGES/omnipkg.mo      |   Bin 33330 -> 32936 bytes
 src/omnipkg/locale/da/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/de/LC_MESSAGES/omnipkg.mo      |   Bin 35025 -> 34578 bytes
 src/omnipkg/locale/de/LC_MESSAGES/omnipkg.po      |    12 +-
 src/omnipkg/locale/es/LC_MESSAGES/omnipkg.mo      |   Bin 35486 -> 35149 bytes
 src/omnipkg/locale/es/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/fr/LC_MESSAGES/omnipkg.mo      |   Bin 35923 -> 35303 bytes
 src/omnipkg/locale/fr/LC_MESSAGES/omnipkg.po      |    16 +-
 src/omnipkg/locale/hi/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/hr/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/id/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/it/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/ja/LC_MESSAGES/omnipkg.mo      |   Bin 38713 -> 187760 bytes
 src/omnipkg/locale/ja/LC_MESSAGES/omnipkg.po      | 10437 ++++++++++++--------
 src/omnipkg/locale/ko/LC_MESSAGES/omnipkg.mo      |   Bin 35864 -> 35489 bytes
 src/omnipkg/locale/ko/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/nl/LC_MESSAGES/omnipkg.mo      |   Bin 34128 -> 33773 bytes
 src/omnipkg/locale/nl/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/no/LC_MESSAGES/omnipkg.mo      |   Bin 33293 -> 32905 bytes
 src/omnipkg/locale/no/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/pl/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/pt_BR/LC_MESSAGES/omnipkg.mo   |   Bin 35016 -> 34521 bytes
 src/omnipkg/locale/pt_BR/LC_MESSAGES/omnipkg.po   |    10 +-
 src/omnipkg/locale/ru/LC_MESSAGES/omnipkg.mo      |   Bin 43249 -> 42785 bytes
 src/omnipkg/locale/ru/LC_MESSAGES/omnipkg.po      |    10 +-
 src/omnipkg/locale/sv/LC_MESSAGES/omnipkg.mo      |   Bin 33583 -> 31276 bytes
 src/omnipkg/locale/sv/LC_MESSAGES/omnipkg.po      |    76 +-
 src/omnipkg/locale/sw/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/tr/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/vi/LC_MESSAGES/omnipkg.po      |     2 +-
 src/omnipkg/locale/zh_CN/LC_MESSAGES/omnipkg.mo   |   Bin 14946 -> 197424 bytes
 src/omnipkg/locale/zh_CN/LC_MESSAGES/omnipkg.po   |  1844 ++--
 src/omnipkg/package_meta_builder.py               |    14 +-
 src/omnipkg/utils/flask_port_finder.py            |     2 +-
 src/tests/test_cli_healing.py                     |     2 +-
 src/tests/test_concurrent_install.py              |    42 +-
 src/tests/test_flask_port_finder.py               |     3 +-
 src/tests/test_flask_port_finder_universal.py     |    30 +-
 src/tests/test_loader_stress_test.py              |    61 +-
 src/tests/test_multiverse_healing.py              |     6 +-
 src/tests/test_old_flask.py                       |     2 +-
 src/tests/test_old_rich.py                        |     6 +-
 src/tests/test_swap_install.py                    |     4 +-
 src/tests/test_version_combos.py                  |     2 +-
 77 files changed, 10187 insertions(+), 6496 deletions(-)
```

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
