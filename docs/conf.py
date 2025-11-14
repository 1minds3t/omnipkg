# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'My Awesome Project'
copyright = '2025, Your Name'
author = 'Your Name'
release = '0.1.0'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    # Add common extensions here, for example:
    # 'sphinx.ext.autodoc',  # To automatically document Python code
    # 'sphinx.ext.napoleon', # To support NumPy and Google style docstrings
]

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'alabaster' # Or change to 'furo', 'pydata_sphinx_theme', etc.
html_static_path = ['_static']

# Note: If you want to use autodoc, you may need to adjust your system path
# import os
# import sys
# sys.path.insert(0, os.path.abspath('../..'))

