"""
Verification Groups Registry

Defines which packages must be tested together because they have tight
interdependencies. When one package in a group is installed/verified,
ALL packages in the group should be tested together.

This prevents false negatives like h11 failing when httpx/httpcore aren't loaded.
"""

from dataclasses import dataclass
from typing import List, Optional, Set


@dataclass
class VerificationGroup:
    """A group of packages that must be tested together."""

    name: str
    packages: Set[str]  # Canonical package names
    primary_package: str  # The main package that drives the group
    reason: str  # Why these must be tested together
    test_order: Optional[List[str]] = None  # If order matters


# ============================================================================
# VERIFICATION GROUPS REGISTRY
# ============================================================================

VERIFICATION_GROUPS = {
    # HTTP/Networking Stack
    "httpx-stack": VerificationGroup(
        name="httpx-stack",
        packages={"httpx", "httpcore", "h11", "h2", "hpack", "hyperframe"},
        primary_package="httpx",
        reason="httpx depends on httpcore which depends on h11/h2. "
        "h11 fails if imported without the full stack present.",
        test_order=["h11", "h2", "httpcore", "httpx"],
    ),
    # TensorFlow Ecosystem
    "tensorflow": VerificationGroup(
        name="tensorflow",
        packages={
            "tensorflow",
            "tensorboard",
            "tensorflow-estimator",
            "keras",
            "tf-keras",
        },
        primary_package="tensorflow",
        reason="TensorBoard and other TF components require TensorFlow to be "
        "loaded first for proper initialization.",
        test_order=["tensorflow", "tensorflow-estimator", "tensorboard", "keras"],
    ),
    # PyTorch Ecosystem
    "torch": VerificationGroup(
        name="torch",
        packages={"torch", "torchvision", "torchaudio", "torchtext"},
        primary_package="torch",
        reason="PyTorch extensions require torch to be imported first.",
        test_order=["torch", "torchvision", "torchaudio", "torchtext"],
    ),
    # Jupyter/IPython
    "jupyter": VerificationGroup(
        name="jupyter",
        packages={
            "jupyter",
            "jupyter-core",
            "jupyter-client",
            "jupyterlab",
            "ipython",
            "ipykernel",
            "ipywidgets",
        },
        primary_package="jupyter",
        reason="Jupyter components have complex interdependencies.",
        test_order=["ipython", "jupyter-core", "jupyter-client", "ipykernel"],
    ),
    # Django
    "django": VerificationGroup(
        name="django",
        packages={
            "django",
            "django-rest-framework",
            "djangorestframework",
            "django-filter",
            "django-cors-headers",
        },
        primary_package="django",
        reason="Django extensions require Django to be imported first.",
        test_order=["django"],
    ),
    # NumPy/SciPy Stack (the classic problematic one)
    "numpy-stack": VerificationGroup(
        name="numpy-stack",
        packages={"numpy", "scipy", "pandas", "scikit-learn", "matplotlib"},
        primary_package="numpy",
        reason="Scientific Python stack has version-sensitive dependencies.",
        test_order=["numpy", "scipy", "pandas", "scikit-learn", "matplotlib"],
    ),
    # AWS SDK
    "boto3": VerificationGroup(
        name="boto3",
        packages={"boto3", "botocore", "s3transfer"},
        primary_package="boto3",
        reason="boto3 requires botocore to be present.",
        test_order=["botocore", "s3transfer", "boto3"],
    ),
    # Requests ecosystem
    "requests": VerificationGroup(
        name="requests",
        packages={"requests", "urllib3", "chardet", "idna", "certifi"},
        primary_package="requests",
        reason="Requests has specific version requirements for its deps.",
        test_order=["urllib3", "chardet", "idna", "certifi", "requests"],
    ),
}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def find_verification_group(package_name: str) -> Optional[VerificationGroup]:
    """
    Find which verification group a package belongs to.

    Args:
        package_name: Canonical package name

    Returns:
        VerificationGroup if found, None otherwise
    """
    canonical = package_name.lower().replace("_", "-")

    for group in VERIFICATION_GROUPS.values():
        if canonical in group.packages:
            return group

    return None


def get_group_members(package_name: str) -> Set[str]:
    """
    Get all packages in the same verification group.

    Args:
        package_name: Canonical package name

    Returns:
        Set of canonical package names in the same group (including the input)
    """
    group = find_verification_group(package_name)
    if group:
        return group.packages.copy()
    return {package_name.lower().replace("_", "-")}


def get_affected_groups(package_names: List[str]) -> List[VerificationGroup]:
    """
    Get all verification groups affected by a list of packages.

    Args:
        package_names: List of package names

    Returns:
        List of unique VerificationGroup objects
    """
    affected = set()

    for pkg in package_names:
        group = find_verification_group(pkg)
        if group:
            affected.add(group.name)

    return [VERIFICATION_GROUPS[name] for name in affected]


def should_test_together(pkg1: str, pkg2: str) -> bool:
    """
    Check if two packages should be tested together.

    Args:
        pkg1, pkg2: Package names

    Returns:
        True if they're in the same verification group
    """
    group1 = find_verification_group(pkg1)
    group2 = find_verification_group(pkg2)

    if group1 and group2:
        return group1.name == group2.name

    return False
