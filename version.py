#!/usr/bin/env python3
"""
Version information for TCI CW Controller
"""

__version__ = "0.2.0"
__build_date__ = "2026-01-22"
__author__ = "SM0ONR"
__description__ = "TCI CW Controller - Morse code keyer for ExpertSDR3"

def get_version_string():
    """Return formatted version string"""
    return f"v{__version__}"

def get_full_version_info():
    """Return detailed version information"""
    return f"TCI CW Controller v{__version__} ({__build_date__})"
