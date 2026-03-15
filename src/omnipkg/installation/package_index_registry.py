"""
Package Index Registry - Auto-detection for special package repositories
Handles PyTorch CUDA/ROCm variants, JAX, and custom repositories
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


class PackageIndexRegistry:
    """
    Manages package index URL detection for special variants like PyTorch CUDA builds.
    Supports both built-in rules and user-customizable configurations.
    """

    def __init__(self, omnipkg_home: Path):
        """
        Initialize the registry.

        Args:
            omnipkg_home: Path to the omnipkg home directory (usually ~/.omnipkg)
        """
        self.omnipkg_home = Path(omnipkg_home)
        self.registry_file = self.omnipkg_home / "package_index_registry.json"
        self.registry = self._load_registry()
        self._build_package_index()

    def _load_registry(self) -> Dict[str, Any]:
        """Load the package index registry from disk or use defaults."""
        if self.registry_file.exists():
            try:
                with open(self.registry_file, "r", encoding="utf-8") as f:
                    custom_registry = json.load(f)
                    # Merge with defaults (custom takes precedence)
                    default_registry = self._get_default_registry()
                    default_registry.update(custom_registry)
                    return default_registry
            except Exception:
                # Silent fallback to defaults if file is corrupted
                pass

        return self._get_default_registry()

    def _get_default_registry(self) -> Dict[str, Any]:
        """Built-in default registry for common package ecosystems."""
        return {
            "pytorch_ecosystem": {
                "packages": ["torch", "torchvision", "torchaudio", "torchtext"],
                "rules": [
                    {
                        "pattern": r"\+cu([0-9]{2,3})",
                        "url": "https://download.pytorch.org/whl/cu{0}",
                        "description": "PyTorch CUDA variants (e.g., 2.1.3+cu118)",
                    },
                    {
                        "pattern": r"\+rocm([0-9]+)",
                        "url": "https://download.pytorch.org/whl/rocm{0}",
                        "description": "PyTorch ROCm variants (e.g., 2.1.3+rocm5.4)",
                    },
                    {
                        "pattern": r"\+cpu",
                        "url": "https://download.pytorch.org/whl/cpu",
                        "description": "PyTorch CPU-only variants",
                    },
                ],
            },
            "jax_ecosystem": {
                "packages": ["jax", "jaxlib"],
                "rules": [
                    {
                        "pattern": r"\+cuda([0-9]{2})",
                        "url": "https://storage.googleapis.com/jax-releases/jax_cuda_releases.html",
                        "description": "JAX CUDA variants (e.g., 0.4.13+cuda11)",
                    },
                    {
                        "pattern": r"\+rocm",
                        "url": "https://storage.googleapis.com/jax-releases/jax_rocm_releases.html",
                        "description": "JAX ROCm variants",
                    },
                ],
            },
        }

    def _build_package_index(self):
        """Pre-build lowercase pkg->ecosystem map once. Makes detect_index_url O(1)."""
        self._pkg_ecosystem = {}
        for ecosystem_name, ecosystem_data in self.registry.items():
            if ecosystem_name.startswith("_"):
                continue
            for pkg in ecosystem_data.get("packages", []):
                self._pkg_ecosystem.setdefault(pkg.lower(), ecosystem_data)

    def detect_index_url(
        self, package_name: str, version: Optional[str]
    ) -> Tuple[Optional[str], Optional[str]]:
        """O(1) lookup — returns (None, None) instantly for normal packages."""
        if not version:
            return None, None
        if not hasattr(self, "_pkg_ecosystem"):
            self._build_package_index()
        ecosystem_data = self._pkg_ecosystem.get(package_name.lower())
        if not ecosystem_data:
            return None, None
        for rule in ecosystem_data.get("rules", []):
            pattern = rule.get("pattern", "")
            if not pattern:
                continue
            m = re.search(pattern, version)
            if m:
                url_template = rule.get("url", "")
                if not url_template:
                    continue
                if "{0}" in url_template:
                    index_url = url_template.format(m.group(1) if m.groups() else "")
                else:
                    index_url = url_template
                return None, index_url
        return None, None

    def create_default_config(self) -> bool:
        """
        Create a default package_index_registry.json file for user customization.

        Returns:
            True if created successfully, False otherwise
        """
        if self.registry_file.exists():
            return False

        try:
            # Ensure directory exists
            self.registry_file.parent.mkdir(parents=True, exist_ok=True)

            # Create config with defaults + example custom section
            config = {
                "_comment": "Package Index Registry - Auto-detection rules for special package repositories",
                "_usage": "Customize this file to add your own package index rules",
                **self._get_default_registry(),
                "custom_repositories": {
                    "_comment": "Add your custom repositories here",
                    "example": {
                        "packages": ["my-private-package"],
                        "rules": [
                            {
                                "pattern": ".*",
                                "url": "https://my-repo.com/simple",
                                "description": "Example private repository",
                            }
                        ],
                    },
                },
            }

            with open(self.registry_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)

            return True
        except Exception:
            return False
