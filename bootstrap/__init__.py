"""Turns a fresh Unicon stack into one the backend can use.

Run it from the deploy directory with `uv run bootstrap`. Every step checks for
the thing it would create before creating it, so a second run changes nothing.
"""
