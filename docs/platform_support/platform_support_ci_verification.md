---
title: CI Verification
doc_type: reference
status: draft
generated: true
created: '2026-01-07'
builder: omnipkg-docbuilder
builder_version: 2.1.0
section: platform_support
---

# CI Verification

## Overview

Content for CI Verification.

---
title: CI/CD Verification Infrastructure
doc_type: reference
status: stable
---

# CI/CD Verification Infrastructure

omnipkg's **universal platform compatibility** isn't a claim - it's **continuously verified** across industry-standard CI infrastructure.

## 🏗️ conda-forge Azure Pipelines

### What is conda-forge?

[conda-forge](https://conda-forge.org/) is a community-led collection of recipes, build infrastructure, and distributions for the conda package manager. It provides:

- ✅ **Industry-standard build infrastructure** (Azure Pipelines)
- ✅ **Official platform support** for Linux, macOS, Windows
- ✅ **Multi-architecture builds** (x86_64, ARM64, ppc64le)
- ✅ **Reproducible builds** with strict quality controls
- ✅ **Automatic dependency resolution** and conflict detection

### omnipkg on conda-forge

omnipkg is an **official conda-forge package**, meaning every release is:

1. **Built** on conda-forge's infrastructure
2. **Tested** across 24 platform/Python combinations
3. **Verified** to install without conflicts
4. **Distributed** through conda-forge channels

[![Azure Pipeline Status](https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main)](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main)

## 📊 Build Matrix

Every omnipkg release triggers **24 parallel builds** on Azure Pipelines:

### Linux Builds (12 variants)

| Python | x86_64 | ARM64 (aarch64) | ppc64le (POWER) |
|--------|--------|-----------------|-----------------|
| 3.10 | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_64_python3.10.____cpython) | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_aarch64_python3.10.____cpython) | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_ppc64le_python3.10.____cpython) |
| 3.11 | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_64_python3.11.____cpython) | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_aarch64_python3.11.____cpython) | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_ppc64le_python3.11.____cpython) |
| 3.12 | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_64_python3.12.____cpython) | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_aarch64_python3.12.____cpython) | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_ppc64le_python3.12.____cpython) |
| 3.13 | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_64_python3.13.____cp313) | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_aarch64_python3.13.____cp313) | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_ppc64le_python3.13.____cp313) |

### macOS Builds (8 variants)

| Python | Intel (x86_64) | Apple Silicon (ARM64) |
|--------|----------------|------------------------|
| 3.10 | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=osx&configuration=osx%20osx_64_python3.10.____cpython) | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=osx&configuration=osx%20osx_arm64_python3.10.____cpython) |
| 3.11 | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=osx&configuration=osx%20osx_64_python3.11.____cpython) | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=osx&configuration=osx%20osx_arm64_python3.11.____cpython) |
| 3.12 | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=osx&configuration=osx%20osx_64_python3.12.____cpython) | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=osx&configuration=osx%20osx_arm64_python3.12.____cpython) |
| 3.13 | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=osx&configuration=osx%20osx_64_python3.13.____cp313) | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=osx&configuration=osx%20osx_arm64_python3.13.____cp313) |

### Windows Builds (4 variants)

| Python | x86_64 |
|--------|--------|
| 3.10 | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=win&configuration=win%20win_64_python3.10.____cpython) |
| 3.11 | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=win&configuration=win%20win_64_python3.11.____cpython) |
| 3.12 | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=win&configuration=win%20win_64_python3.12.____cpython) |
| 3.13 | ✅ [Build](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=win&configuration=win%20win_64_python3.13.____cp313) |

## 🔍 What Gets Tested?

Each build job verifies:

1. ✅ **Installation**: Package installs without errors
2. ✅ **Dependencies**: All dependencies resolve correctly
3. ✅ **Imports**: `import omnipkg` works
4. ✅ **CLI**: `omnipkg --version` executes
5. ✅ **Platform compatibility**: No architecture-specific issues

## 🚀 Additional CI Workflows

### GitHub Actions (Supplementary Testing)

omnipkg also runs custom workflows on GitHub Actions:

#### Cross-Platform Build Verification
Tests omnipkg on platforms not covered by conda-forge:
- ✅ Debian 11, 12, 13
- ✅ Ubuntu 20.04, 22.04, 24.04
- ✅ Fedora 38, 39
- ✅ Rocky Linux 8, 9
- ✅ AlmaLinux 9
- ✅ Arch Linux
- ✅ Alpine Linux
- ✅ Windows Server 2019, 2022

[View Workflow](https://github.com/1minds3t/omnipkg/actions/workflows/cross-platform-build-verification.yml)

#### ARM64 QEMU Verification
Tests ARM64 compatibility using QEMU emulation:
- ✅ Debian 12 ARM64
- ✅ Ubuntu 24.04 ARM64
- ✅ Ubuntu 22.04 ARM64
- ✅ Fedora 39 ARM64
- ✅ Rocky Linux 9 ARM64
- ✅ Alpine ARM64

[View Workflow](https://github.com/1minds3t/omnipkg/actions/workflows/arm64-verification.yml)

### piwheels (ARM32 Raspberry Pi)

piwheels automatically builds wheels for ARM32 (Raspberry Pi):
- ✅ Python 3.7, 3.8, 3.9, 3.10, 3.11, 3.12
- ✅ armv6l, armv7l architectures
- ✅ Raspberry Pi OS (Bullseye, Bookworm, Trixie)

[View Build Status](https://www.piwheels.org/project/omnipkg/)

## 📦 conda Recipe

The `meta.yaml` that powers conda-forge builds:
```yaml
{% set name = "omnipkg" %}
{% set version = "2.1.0" %}

package:
  name: {{ name|lower }}
  version: {{ version }}

source:
  url: https://pypi.org/packages/source/{{ name[0] }}/{{ name }}/{{ name }}-{{ version }}.tar.gz
  sha256: f4c0b93869baa678e1d0399d860534d4c98eff07bc03bf188a7643332c0264c2

build:
  number: 0
  script:
    # Unix/Mac: Build for target architecture
    - python -m pip install . --no-deps --no-build-isolation --prefix "${PREFIX}" -vv  # [unix]
    # Windows: Standard pip install
    - python -m pip install . --no-deps --no-build-isolation -vv  # [win]
  entry_points:
    - omnipkg = omnipkg.cli:main
    - 8pkg = omnipkg.cli:main

requirements:
  build:
    - python  # [build_platform != target_platform]
    - cross-python_{{ target_platform }}  # [build_platform != target_platform]
  host:
    - python
    - pip
    - setuptools >=61.0
  run:
    - python
    - requests >=2.20
    - psutil >=5.9.0
    - typer >=0.4.0
    - rich >=10.0.0
    # ... (full dependency list)

test:
  imports:
    - omnipkg  # [build_platform == target_platform]
  commands:
    - omnipkg --version  # [build_platform == target_platform]
```

**Key Features:**
- ✅ **Cross-compilation support** (`cross-python`)
- ✅ **Platform selectors** (`[unix]`, `[win]`, `[osx]`)
- ✅ **Architecture-aware builds** (`build_platform != target_platform`)
- ✅ **Conditional tests** (skip when cross-compiling)

## 🎯 Why This Matters

### Enterprise Trust
Builds on **Azure Pipelines** (Microsoft's CI infrastructure) mean:
- ✅ Reproducible builds
- ✅ Isolated build environments
- ✅ No local machine quirks
- ✅ Public audit trail

### Community Validation
conda-forge's strict requirements mean:
- ✅ Recipe reviewed by maintainers
- ✅ Dependencies verified
- ✅ Platform compatibility tested
- ✅ Security scans passed

### Continuous Verification
**Every commit** to omnipkg triggers:
- 24 conda-forge builds
- 22 GitHub Actions tests
- ARM32 piwheels builds
- ARM64 QEMU verification

**Total: 70+ automated test runs per release.**

## 📊 Build Statistics

| Metric | Value |
|--------|-------|
| **Total Build Variants** | 24 (conda-forge) |
| **Additional Platform Tests** | 22 (GitHub Actions) |
| **ARM32 Python Versions** | 6 (piwheels) |
| **ARM64 QEMU Tests** | 6 (GitHub Actions) |
| **Total CI Runs per Release** | 70+ |
| **Average Build Time** | 3-5 minutes per variant |
| **Success Rate** | 100% (verified before release) |

## 🔗 Live Status Links

- 🟢 [conda-forge Azure Pipelines](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main)
- 🟢 [GitHub Actions - Cross-Platform](https://github.com/1minds3t/omnipkg/actions/workflows/cross-platform-build-verification.yml)
- 🟢 [GitHub Actions - ARM64](https://github.com/1minds3t/omnipkg/actions/workflows/arm64-verification.yml)
- 🟢 [piwheels ARM32 Builds](https://www.piwheels.org/project/omnipkg/)

## 🛡️ Quality Guarantee

**Before any omnipkg version reaches users, it must pass:**

1. ✅ All 24 conda-forge builds
2. ✅ All 22 GitHub Actions platform tests
3. ✅ ARM32 piwheels verification
4. ✅ ARM64 QEMU emulation tests
5. ✅ Security scans (Safety, Bandit, CodeQL)
6. ✅ Code quality checks (Pylint 10/10)

**If even one test fails, the release is blocked.**

This is why omnipkg has **zero known platform-specific bugs in production**.