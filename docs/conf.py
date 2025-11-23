# Configuration file for the Sphinx documentation builder.

# -- Project information -----------------------------------------------------
project = 'omnipkg'
copyright = '2025, 1minds3t'
author = '1minds3t'
release = '1.6.2'

# -- General configuration ---------------------------------------------------
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinx.ext.githubpages',
    'myst_parser',  # For markdown support
]

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# -- Options for HTML output -------------------------------------------------
# Modern, beautiful theme - pick one:
html_theme = 'furo'  # Recommended - clean, modern, mobile-friendly
# html_theme = 'sphinx_rtd_theme'  # Alternative - ReadTheDocs style
# html_theme = 'pydata_sphinx_theme'  # Alternative - PyData style

html_static_path = ['_static']

# Theme options
html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
}

# Logo and favicon (create these if you have them)
# html_logo = "_static/logo.png"
# html_favicon = "_static/favicon.ico"

html_title = "omnipkg Documentation"

# Sidebar settings
html_sidebars = {
    "**": [
        "sidebar/brand.html",
        "sidebar/search.html",
        "sidebar/scroll-start.html",
        "sidebar/navigation.html",
        "sidebar/scroll-end.html",
    ]
}
