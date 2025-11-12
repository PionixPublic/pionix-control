#
# SPDX-License-Identifier: Apache-2.0
# Copyright Pionix GmbH and Contributors to EVerest
#
"""Pionix control module."""
from pionix_control import pionix_control
__version__ = "0.1.0"


def get_parser():
    """Return the command line parser."""
    return pionix_control.get_parser(__version__)


def main():
    """Main entrypoint of PionixControl."""
    pionix_control.main(get_parser())
