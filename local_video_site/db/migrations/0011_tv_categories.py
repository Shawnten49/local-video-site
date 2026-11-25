from __future__ import annotations

import importlib

from .runner import BASELINE_VERSION, Migration


_data_dictionary_module = importlib.import_module(
    "{}.0005_data_dictionary".format(__package__)
)


tv_categories_migration = Migration(
    BASELINE_VERSION + 10,
    "refresh schema dictionary for TV series categories and covers",
    _data_dictionary_module.data_dictionary_statements(),
    compatible_checksums=(
        "cfb8bef928063bd572c014b1da9dddec193d5d225f7fc3650eefcc998e88ba22",
    ),
)
