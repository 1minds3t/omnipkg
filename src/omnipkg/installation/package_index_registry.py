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

    def detect_index_url(
        self, package_name: str, version: Optional[str]
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Detect the appropriate index URL for a package variant.

        Args:
            package_name: The package name (e.g., "torch")
            version: The version string (e.g., "2.1.3+cu118")

        Returns:
            Tuple of (index_url, extra_index_url) or (None, None) if standard PyPI
        """
        if not version:
            return None, None

        pkg_lower = package_name.lower()

        # Check each ecosystem in the registry
        for ecosystem_name, ecosystem_data in self.registry.items():
            # Skip metadata fields
            if ecosystem_name.startswith("_"):
                continue

            # Check if package belongs to this ecosystem
            if "packages" not in ecosystem_data:
                continue

            if pkg_lower not in [p.lower() for p in ecosystem_data["packages"]]:
                continue

            # Try to match against rules
            for rule in ecosystem_data.get("rules", []):
                pattern = rule.get("pattern", "")
                if not pattern:
                    continue

                match = re.search(pattern, version)
                if match:
                    url_template = rule.get("url")
                    if not url_template:
                        continue

                    # Replace {0} with the captured group if present
                    if "{0}" in url_template:
                        captured = match.group(1) if match.groups() else ""
                        index_url = url_template.format(captured)
                    else:
                        index_url = url_template

                    # For now, we don't use extra_index_url, but the API supports it
                    return index_url, None

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
