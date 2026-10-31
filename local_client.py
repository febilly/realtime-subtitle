"""Credential shim for the credential-free local provider."""


def get_api_key() -> str:
    # ProviderManager uses truthiness to decide whether a session can start.
    # The local session ignores this sentinel and never sends it over the network.
    return "local-on-device"
