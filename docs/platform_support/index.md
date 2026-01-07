---
title: Platform Support
doc_type: reference
status: stable
generated: true
created: '2026-01-07'
builder: omnipkg-docbuilder
builder_version: 2.1.0
section: platform_support
---

# Platform Support

Welcome to the Platform Support section.

## Overview

# Platform & Architecture Support

**omnipkg** is a **pure Python package (noarch)** with **no C-extensions**, ensuring universal compatibility across all platforms and architectures.

## 🎯 Verified Platform Matrix

[![Platforms Verified](https://img.shields.io/badge/platforms-22%20verified-success?logo=linux&logoColor=white)](https://github.com/1minds3t/omnipkg/actions/workflows/cross-platform-build-verification.yml)

### ✅ Officially Verified via conda-forge Azure Pipelines

omnipkg is built and tested on **conda-forge's official infrastructure**, providing enterprise-grade verification across:

| Platform Category | Architectures | Python Versions | Status |
|-------------------|---------------|-----------------|--------|
| **Linux** | x86_64, ARM64, ppc64le | 3.10, 3.11, 3.12, 3.13 | ✅ [Verified](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main) |
| **macOS** | x86_64 (Intel), ARM64 (Apple Silicon) | 3.10, 3.11, 3.12, 3.13 | ✅ [Verified](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main) |
| **Windows** | x86_64 | 3.10, 3.11, 3.12, 3.13 | ✅ [Verified](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main) |

**Total Verified Combinations**: 24 platform × Python version builds running continuously on conda-forge infrastructure.

## 🐍 Python Version Support Matrix

| Python Version | Status | Notes |
|----------------|--------|-------|
| **3.7** | ✅ Supported | Legacy support with backports |
| **3.8** | ✅ Supported | Full feature set |
| **3.9** | ✅ Supported | Full feature set |
| **3.10** | ✅ Supported | Full feature set |
| **3.11** | ✅ Supported | Full feature set |
| **3.12** | ✅ Supported | Full feature set |
| **3.13** | ✅ Supported | Latest stable |
| **3.14** | ✅ Supported | Beta/RC releases |

## 🏗️ Architecture Support

Because omnipkg is **pure Python (noarch)**, it runs on **any architecture** where Python is available:

| Architecture | Status | Verification Method |
|--------------|--------|---------------------|
| **x86_64** (Intel/AMD) | ✅ Verified | conda-forge Azure Pipelines |
| **ARM64** (aarch64) | ✅ Verified | conda-forge Azure + QEMU |
| **ARM32** (armv6/v7) | ✅ Verified | [piwheels](https://www.piwheels.org/project/omnipkg/) |
| **ppc64le** (POWER) | ✅ Verified | conda-forge Azure Pipelines |
| **RISC-V** | 🟡 Untested | Should work (pure Python) |
| **s390x** (IBM Z) | 🟡 Untested | Should work (pure Python) |

## 📊 Detailed Platform Support

### Linux Distributions

#### ✅ Debian/Ubuntu Family
| Distribution | Architecture | Python | Status | Installation Notes |
|--------------|--------------|--------|--------|-------------------|
| Debian 13 (Trixie) | x86_64, ARM64 | 3.11+ | ✅ | `--break-system-packages` required |
| Debian 12 (Bookworm) | x86_64, ARM64 | 3.11 | ✅ | `--break-system-packages` required |
| Debian 11 (Bullseye) | x86_64, ARM64 | 3.9 | ✅ | Standard install |
| Ubuntu 24.04 (Noble) | x86_64, ARM64 | 3.12 | ✅ | `--break-system-packages` required |
| Ubuntu 22.04 (Jammy) | x86_64, ARM64 | 3.10 | ✅ | Standard install |
| Ubuntu 20.04 (Focal) | x86_64, ARM64 | 3.8 | ✅ | Standard install |

#### ✅ RHEL/Fedora Family
| Distribution | Architecture | Python | Status | Installation Notes |
|--------------|--------------|--------|--------|-------------------|
| Fedora 39 | x86_64, ARM64 | 3.12 | ✅ | Standard install |
| Fedora 38 | x86_64, ARM64 | 3.11 | ✅ | Standard install |
| Rocky Linux 9 | x86_64, ARM64 | 3.9 | ✅ | Standard install |
| Rocky Linux 8 | x86_64, ARM64 | 3.6 → 3.9 | ✅ | Requires Python 3.9+ upgrade |
| AlmaLinux 9 | x86_64, ARM64 | 3.9 | ✅ | Standard install |

#### ✅ Other Linux
| Distribution | Architecture | Python | Status | Installation Notes |
|--------------|--------------|--------|--------|-------------------|
| Arch Linux | x86_64, ARM64 | Latest | ✅ | `--break-system-packages` required |
| Alpine Linux | x86_64, ARM64 | 3.11+ | ✅ | Requires build deps (gcc, musl-dev) |

### macOS

| Version | Architecture | Python | Status | Installation Notes |
|---------|--------------|--------|--------|-------------------|
| macOS 14 (Sonoma) | ARM64 (M1/M2/M3) | 3.10+ | ✅ | Native Apple Silicon |
| macOS 13 (Ventura) | ARM64, x86_64 | 3.10+ | ✅ | Universal support |
| macOS 12 (Monterey) | ARM64, x86_64 | 3.9+ | ✅ | Universal support |
| macOS 11 (Big Sur) | ARM64, x86_64 | 3.9+ | ✅ | Universal support |

### Windows

| Version | Architecture | Python | Status | Installation Notes |
|---------|--------------|--------|--------|-------------------|
| Windows 11 | x86_64 | 3.7+ | ✅ | Native support |
| Windows 10 | x86_64 | 3.7+ | ✅ | Native support |
| Windows Server 2022 | x86_64 | 3.7+ | ✅ | Server environments |
| Windows Server 2019 | x86_64 | 3.7+ | ✅ | Server environments |

### ARM Platforms

#### ✅ ARM64 (aarch64)
**Verified via conda-forge Azure Pipelines + QEMU emulation**

| Platform | Status | Verification |
|----------|--------|--------------|
| Debian 12 ARM64 | ✅ | QEMU + Azure |
| Ubuntu 24.04 ARM64 | ✅ | QEMU + Azure |
| Ubuntu 22.04 ARM64 | ✅ | QEMU + Azure |
| Fedora 39 ARM64 | ✅ | QEMU + Azure |
| Rocky Linux 9 ARM64 | ✅ | QEMU + Azure |
| Alpine ARM64 | ✅ | QEMU + Azure |

#### ✅ ARM32 (Raspberry Pi)
**Verified via [piwheels](https://www.piwheels.org/project/omnipkg/)**

| Platform | Architecture | Status |
|----------|--------------|--------|
| Raspberry Pi OS (Bullseye) | armv7l | ✅ |
| Raspberry Pi OS (Bookworm) | armv7l | ✅ |
| Raspberry Pi 4/5 | armv7l, aarch64 | ✅ |
| Raspberry Pi 3 | armv7l | ✅ |
| Raspberry Pi Zero 2 W | armv7l | ✅ |

## 🐳 Docker Multi-Architecture Support

omnipkg Docker images support automatic architecture detection:
```bash
# Automatically pulls correct architecture
docker pull 1minds3t/omnipkg:latest
```

**Available architectures:**
- ✅ `linux/amd64` (x86_64)
- ✅ `linux/arm64` (aarch64)

## 📦 Installation Methods by Platform

### Universal (All Platforms)
```bash
pip install omnipkg
```

### Debian/Ubuntu 24.04+ (PEP 668)
```bash
pip install --break-system-packages omnipkg
```

### Rocky/Alma Linux 8 (Python 3.6 → 3.9)
```bash
sudo dnf install -y python39 python39-pip
sudo ln -sf /usr/bin/python3.9 /usr/bin/python3
pip3 install omnipkg
```

### Alpine Linux (Build Dependencies)
```bash
apk add --no-cache gcc python3-dev musl-dev linux-headers
pip install omnipkg
```

### Raspberry Pi (Optimized Wheels)
```bash
pip install --index-url=https://www.piwheels.org/simple/ omnipkg
```

### conda-forge (All Platforms)
```bash
conda install -c conda-forge omnipkg
```

## 🔗 CI/CD Verification Links

### conda-forge Azure Pipelines (Live Status)

omnipkg is built on conda-forge's official infrastructure with **24 parallel build variants**:

[![Azure Pipeline Status](https://dev.azure.com/conda-forge/feedstock-builds/_apis/build/status/omnipkg-feedstock?branchName=main)](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main)

**View live builds:**

| Platform | Python 3.10 | Python 3.11 | Python 3.12 | Python 3.13 |
|----------|-------------|-------------|-------------|-------------|
| **Linux x86_64** | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_64_python3.10.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_64_python3.11.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_64_python3.12.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_64_python3.13.____cp313) |
| **Linux ARM64** | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_aarch64_python3.10.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_aarch64_python3.11.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_aarch64_python3.12.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_aarch64_python3.13.____cp313) |
| **Linux ppc64le** | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_ppc64le_python3.10.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_ppc64le_python3.11.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_ppc64le_python3.12.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=linux&configuration=linux%20linux_ppc64le_python3.13.____cp313) |
| **macOS Intel** | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=osx&configuration=osx%20osx_64_python3.10.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=osx&configuration=osx%20osx_64_python3.11.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=osx&configuration=osx%20osx_64_python3.12.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=osx&configuration=osx%20osx_64_python3.13.____cp313) |
| **macOS ARM64** | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=osx&configuration=osx%20osx_arm64_python3.10.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=osx&configuration=osx%20osx_arm64_python3.11.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=osx&configuration=osx%20osx_arm64_python3.12.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=osx&configuration=osx%20osx_arm64_python3.13.____cp313) |
| **Windows x86_64** | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=win&configuration=win%20win_64_python3.10.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=win&configuration=win%20win_64_python3.11.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=win&configuration=win%20win_64_python3.12.____cpython) | [✅](https://dev.azure.com/conda-forge/feedstock-builds/_build/latest?definitionId=26533&branchName=main&jobName=win&configuration=win%20win_64_python3.13.____cp313) |

### GitHub Actions CI

- ✅ [Cross-Platform Build Verification](https://github.com/1minds3t/omnipkg/actions/workflows/cross-platform-build-verification.yml)
- ✅ [ARM64 QEMU Verification](https://github.com/1minds3t/omnipkg/actions/workflows/arm64-verification.yml)

### piwheels (ARM32 Verification)

- ✅ [piwheels Build Status](https://www.piwheels.org/project/omnipkg/)

## 🎯 Why Universal Compatibility Matters

### 1. **No Vendor Lock-In**
Deploy anywhere without worrying about architecture or OS constraints.

### 2. **CI/CD Flexibility**
Test on x86_64, deploy on ARM64 - seamlessly.

### 3. **Edge Computing Ready**
Run on Raspberry Pi, AWS Graviton, or Apple Silicon without code changes.

### 4. **Future-Proof**
Pure Python means omnipkg will run on RISC-V, new ARM versions, and architectures that don't exist yet.

## 🛡️ Quality Assurance

Every omnipkg release is:
- ✅ Built on 24+ platform/Python combinations
- ✅ Tested on conda-forge's official infrastructure
- ✅ Verified on ARM32 via piwheels
- ✅ QEMU-tested for ARM64 compatibility
- ✅ Docker multi-arch builds tested

**No other Python package manager has this level of platform verification.**

## 📚 Platform-Specific Guides

- [Linux Installation Guide](linux-platforms.md) - Detailed instructions for all major distros
- [macOS Installation Guide](macos-platforms.md) - Intel and Apple Silicon specifics
- [Windows Installation Guide](windows-platforms.md) - Windows 10/11 and Server
- [ARM Architectures](arm-architectures.md) - ARM64 and ARM32 (Raspberry Pi)
- [Python Compatibility Matrix](python-compatibility.md) - Version-specific features and limitations
- [CI/CD Verification](ci-verification.md) - Deep dive into testing infrastructure

## 🆘 Platform-Specific Issues?
```bash
omnipkg doctor  # Diagnose platform-specific issues
```

Or visit our [GitHub Issues](https://github.com/1minds3t/omnipkg/issues) with:
- Platform: `uname -a`
- Python: `python --version`
- omnipkg: `omnipkg --version`