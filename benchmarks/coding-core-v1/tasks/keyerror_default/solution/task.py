"""Reference fix for t3_keyerror_default — overlay this directory with
`minder-op benchmark run --overlay` to produce a verified run. The
missing-key lookup falls back to None instead of raising.
"""
SETTINGS = {"retries": 2}


def get_setting(key, settings=None):
    settings = settings or SETTINGS
    return settings.get(key)
