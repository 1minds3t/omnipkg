"""
Dependency Constraint Registry
Hard-coded knowledge about packages that require specific dependency versions
to avoid binary incompatibility issues (especially numpy ABI changes).
"""
from typing import Dict, List, Optional, Tuple
from packaging.version import Version, parse as parse_version


# Registry of packages with specific numpy version requirements
NUMPY_CONSTRAINTS = {
    # pandas versions and their numpy requirements
    "pandas": [
        ("2.0.0", "2.1.99", ">=1.21.0,<2.0"),  # pandas 2.0.x needs numpy <2.0
        ("2.2.0", "2.2.99", ">=1.23.5,<2.3"),  # pandas 2.2.x supports numpy 2.x
        ("2.3.0", "2.9.99", ">=1.26.0,<2.3"),  # pandas 2.3+ supports numpy 2.x
    ],
    "scipy": [
        ("1.10.0", "1.10.99", ">=1.21.0,<1.28"),
        ("1.11.0", "1.13.99", ">=1.21.6,<2.1"),
    ],
    "scikit-learn": [
        ("1.3.0", "1.3.99", ">=1.17.3,<2.0"),
        ("1.4.0", "1.5.99", ">=1.19.5,<2.1"),
    ],
    "numba": [
        ("0.50.0", "0.60.99", ">=1.18,<1.25"),
        ("0.61.0", "0.61.99", ">=1.24,<2.3"),
    ],
}


# Registry of packages that have breaking API changes in newer versions.
# When the healer detects an ImportError FROM one of these packages, it pins
# to the last known-good version rather than installing latest.
#
# Format: pkg_name -> pinned_version (exact version to install as ==X.Y.Z)
#
# How to add entries:
#   If "ImportError: cannot import name 'Foo' from 'bar'" appears and bar>=X broke it,
#   add "bar": "X-1.patch" (last version before the break).
IMPORT_COMPAT_CONSTRAINTS: Dict[str, str] = {
    # h11 0.15+ removed Headers from h11._headers, breaking wsproto/hypercorn on older setups
    "h11": "0.14.0",

    # Add more as discovered, e.g.:
    # "somelib": "1.2.3",  # 1.3.0 removed SomeAPI used by dependent
}


def get_import_compat_spec(pkg_name: str) -> Optional[str]:
    """
    Returns a pinned pip spec (e.g. 'h11==0.14.0') if there's a known
    compatibility constraint for this package, else None.
    """
    pinned = IMPORT_COMPAT_CONSTRAINTS.get(pkg_name.lower())
    if pinned:
        return f"{pkg_name}=={pinned}"
    return None


# Packages where newer versions have breaking API changes.
# Maps pkg_name -> last known-good pinned version.
# The healer uses this to emit "pkg==X.Y.Z" instead of bare "pkg" (which resolves to latest/broken).
IMPORT_COMPAT_CONSTRAINTS: Dict[str, str] = {
    # h11 0.15+ removed Headers from h11._headers, breaking wsproto/hypercorn
    "h11": "0.14.0",
}


def get_import_compat_spec(pkg_name: str) -> Optional[str]:
    """
    Returns a pinned pip spec (e.g. 'h11==0.14.0') if there's a known
    compatibility constraint, else None.
    """
    pinned = IMPORT_COMPAT_CONSTRAINTS.get(pkg_name.lower())
    if pinned:
        return f"{pkg_name}=={pinned}"
    return None


def get_numpy_constraint(package_name: str, version: str) -> Optional[str]:
    """
    Get the numpy version constraint for a specific package version.
    Args:
        package_name: Name of the package (e.g., 'pandas')
        version: Version of the package (e.g., '2.0.3')
    Returns:
        Numpy version constraint (e.g., '>=1.21.0,<2.0') or None
    """
    canonical_name = package_name.lower().replace("_", "-")
    if canonical_name not in NUMPY_CONSTRAINTS:
        return None
    try:
        pkg_version = parse_version(version)
    except:
        return None

    # Find matching constraint
    for min_ver, max_ver, constraint in NUMPY_CONSTRAINTS[canonical_name]:
        try:
            if parse_version(min_ver) <= pkg_version <= parse_version(max_ver):
                return constraint
        except:
            continue
    return None


def apply_dependency_constraints(
    package_name: str,
    version: str,
    dependencies: List[str]
) -> List[str]:
    """
    Apply known dependency constraints to a dependency list.
    Args:
        package_name: Name of the package being installed
        version: Version of the package being installed
        dependencies: List of dependency specs
    Returns:
        Modified dependency list with constraints applied
    """
    numpy_constraint = get_numpy_constraint(package_name, version)
    if not numpy_constraint:
        return dependencies

    # Check if numpy is already in dependencies
    has_numpy = any(
        dep.lower().startswith("numpy")
        for dep in dependencies
    )
    if has_numpy:
        # Replace existing numpy constraint
        new_deps = []
        for dep in dependencies:
            if dep.lower().startswith("numpy"):
                new_deps.append(f"numpy{numpy_constraint}")
            else:
                new_deps.append(dep)
        return new_deps
    else:
        # Add numpy constraint
        return dependencies + [f"numpy{numpy_constraint}"]


def get_all_constraints_for_package(
    package_name: str,
    version: str
) -> Dict[str, str]:
    """
    Get all dependency constraints for a package.
    Returns:
        Dict mapping dependency name to constraint
    """
    constraints = {}
    numpy_constraint = get_numpy_constraint(package_name, version)
    if numpy_constraint:
        constraints["numpy"] = numpy_constraint
    return constraints