# Security Policy

## Reporting a Vulnerability

The omnipkg team takes security seriously and appreciates the security community's efforts to responsibly disclose vulnerabilities. If you have discovered a security vulnerability in omnipkg itself, please report it to us as soon as possible.

**Please do not open a public issue.** Instead, send an email to:

📧 **1minds3t@proton.me**

We will acknowledge your email within 48 hours and will work with you to address the vulnerability.

## Responsible Disclosure Policy

We follow a standard responsible disclosure process. Our commitment to you:

* We will acknowledge receipt of your vulnerability report in a timely manner.
* We will provide you with a timeframe for addressing the vulnerability.
* We will credit you for your discovery after the vulnerability has been patched and publicly released, unless you prefer to remain anonymous.
* We ask that you do not disclose the vulnerability publicly until we have released a fix. We will work with you to agree on a coordinated public disclosure date.

## Guidelines for Reporters

We ask you to adhere to these guidelines when researching and reporting vulnerabilities:

* Provide a clear, detailed description of the vulnerability, including steps to reproduce it.
* Do not disclose the vulnerability or any details about it to third parties or on public forums until we have resolved it and agreed on a public disclosure date.
* Do not exploit the vulnerability to access, modify, or destroy user data or system integrity.
* Do not engage in activities that could compromise the confidentiality, integrity, or availability of our systems.

## Scope

This security policy applies to the official omnipkg open-source repository and its published packages.

---

## Known CVE Status for Legacy Python Versions

omnipkg maintains compatibility with Python 3.7 and 3.8 for users who cannot upgrade. However, some upstream dependencies have **dropped support** for these Python versions, leaving known CVEs unpatched in their final releases.

### Our LTS Strategy

We are actively addressing this through our **omnipkg LTS package program**, where we backport critical security fixes to legacy Python versions. Below is the current status of each affected dependency.

---

### 🔒 Dependencies with LTS Packages (Patched)

These dependencies have **active LTS packages** that backport security fixes:

#### ✅ urllib3-lts
- **Status**: 🟢 **Patched via urllib3-lts**
- **Python 3.7-3.14**: All users receive patched version `>=2025.66471.3`
- **CVEs Fixed**: CVE-2025-66471, CVE-2025-66418, CVE-2025-50181, CVE-2024-37891
- **PyPI**: [`urllib3-lts`](https://pypi.org/project/urllib3-lts/)

#### ✅ filelock-lts
- **Status**: 🟢 **Patched via filelock-lts**
- **Python 3.7-3.9**: Uses `filelock-lts>=2025.68146`
- **Python 3.10+**: Uses official `filelock>=3.20.1`
- **CVEs Fixed**: CVE-2025-68146
- **PyPI**: [`filelock-lts`](https://pypi.org/project/filelock-lts/)

---

### ⚠️ Dependencies with Known CVEs (LTS Planned)

These dependencies have **unpatched CVEs** on legacy Python versions. LTS packages are planned:

#### ⚠️ aiohttp

**Python 3.7**
- **Version**: 3.8.6 (last version supporting Python 3.7)
- **Status**: 🔴 **20 unpatched CVEs**
- **Upstream**: Dropped Python 3.7 support after 3.8.6
- **Plan**: `aiohttp-lts-py37` package in development

**Python 3.8**
- **Version**: 3.10.11 (last version supporting Python 3.8)
- **Status**: 🟠 **9 unpatched CVEs**
- **Upstream**: Dropped Python 3.8 support after 3.10.11
- **Plan**: `aiohttp-lts-py38` package in development

**Python 3.9+**
- **Version**: 3.13.3+
- **Status**: ✅ **All CVEs patched**
- **No action needed** - upstream is fully supported

**Impact Analysis**:
- aiohttp is used internally by omnipkg for async operations
- Most users are not directly exposed to aiohttp's HTTP server functionality
- Risk is primarily for users who use omnipkg in web-facing applications on Python 3.7-3.8

#### ⚠️ authlib

**Python 3.7**
- **Version**: 1.2.1 (last stable for Python 3.7)
- **Status**: 🟠 **Unknown CVEs** (needs audit)
- **Plan**: `authlib-lts-py37` package under evaluation

**Python 3.8**
- **Version**: 1.3.2 (max support for Python 3.8)
- **Status**: 🟠 **Unknown CVEs** (needs audit)
- **Plan**: `authlib-lts-py38` package under evaluation

**Python 3.9+**
- **Version**: 1.6.5+
- **Status**: ✅ **Patched for recent CVEs**

#### ⚠️ safety

**Python 3.7-3.9, 3.14+**
- **Status**: 🟠 **Not installed** (feature not available)
- **Reason**: safety requires Python 3.10-3.13
- **Mitigation**: Uses `pip-audit` as fallback scanner
- **Plan**: May create `safety-lts` if critical vulnerabilities emerge

---

### 🛡️ Mitigation Strategies

If you're using omnipkg on **Python 3.7 or 3.8**, we recommend:

1. **🎯 Upgrade to Python 3.9+** (recommended)
   - All dependencies receive active security updates
   - You are completely protected from the CVEs listed above

2. **🔒 Use in isolated/trusted environments**
   - Minimize exposure to untrusted network input
   - Deploy behind firewalls and authentication layers
   - Use for development/CI environments only

3. **👀 Monitor for LTS packages**
   - We are actively developing `aiohttp-lts` and `authlib-lts` packages
   - Subscribe to releases at https://github.com/1minds3t/omnipkg

4. **🐳 Use Docker containers**
   - Our official Docker images use Python 3.11+ by default
   - Pre-built images available at: https://hub.docker.com/r/1minds3t/omnipkg

---

### 📊 LTS Package Development Priorities

Based on download volume and CVE severity, our LTS backporting priorities are:

| Priority | Package | Python Versions | CVE Count | Market Size |
|----------|---------|-----------------|-----------|-------------|
| ✅ Done | urllib3-lts | 3.7-3.8 | 4 | 933M downloads/month |
| ✅ Done | filelock-lts | 3.7-3.9 | 1 | 290M downloads/month |
| 🟡 In Progress | aiohttp-lts | 3.7-3.8 | 20+9 | 170M downloads/month |
| 🟡 Evaluating | authlib-lts | 3.7-3.8 | TBD | TBD |
| 🟡 Monitoring | safety-lts | 3.7-3.9, 3.14+ | TBD | N/A (optional) |

**Timeline**: We aim to release `aiohttp-lts` packages by Q1 2026.

---

### 🔍 Transparency & Verification

All LTS packages:
- Are published to PyPI with clear version numbering (e.g., `2025.66471.3`)
- Include detailed changelogs documenting which CVEs are patched
- Link back to original upstream commits where fixes were sourced
- Are fully open-source (AGPL-3.0) matching omnipkg's license
- Include attribution to original package maintainers

You can verify LTS package sources at:
- GitHub: https://github.com/1minds3t/omnipkg
- PyPI Project Pages: Search for `*-lts` packages

---

## 🔒 Web Bridge Security

The OmniPkg web bridge implements multiple security layers:

- **Command Validation**: Only safe commands are allowed via web
- **URL Blocking**: Git URLs and custom package indexes are disabled
- **Path Sanitization**: File system paths cannot be accessed
- **Output Redaction**: Sensitive info is automatically removed
- **CORS Protection**: Only authorized origins can send commands

### Allowed Commands
✅ `status`, `list`, `info`, `check`, `config`, `doctor`, `swap`, `python`

### Blocked Commands
❌ `run`, `shell`, `exec`, `uninstall`, `prune`, Remote URLs, Custom indexes

For package installation, use the dedicated "Install OmniPkg" button.

---

### 📢 Security Updates

To stay informed about security updates:

1. **Watch the GitHub repository**: https://github.com/1minds3t/omnipkg
2. **Subscribe to releases**: Get notified when new LTS packages are published
3. **Follow the changelog**: CHANGELOG.md documents all security-related updates
4. **Check Dependabot**: GitHub Security tab shows real-time vulnerability status

---

### ✅ Python 3.9+ Users

If you're using **Python 3.9 or newer**, you are **fully protected**:
- ✅ All dependencies receive upstream security updates
- ✅ No known unpatched CVEs in any dependencies
- ✅ Automatic updates through standard `pip install -U omnipkg`

---

## Development Security

For contributors and developers:

- **Code scanning**: We use CodeQL for automated security analysis
- **Dependency scanning**: Dependabot monitors all dependencies
- **Supply chain**: All dependencies are verified and pinned with minimum versions
- **Testing**: Security-related fixes must include regression tests

---

Thank you for helping us keep omnipkg secure! 🔒