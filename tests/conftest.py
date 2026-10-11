"""Shared API-test isolation defaults.

Resource APIs are secure-by-default at runtime. Existing unit tests that focus on
domain behavior explicitly use the trusted in-process test mode; security tests
enable JWT enforcement and exercise the real request boundary.
"""
import os

os.environ["OFI_RESOURCE_AUTH_ENABLED"] = "false"
