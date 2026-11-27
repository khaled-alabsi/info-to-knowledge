"""The models a model host has, asked of the host itself with the standard `GET /v1/models`: what the setup wizard reports
as installed, and what the Settings page offers as a model's spec. Its own module, since both read it and the settings
code is read by the wizard's.
"""

from __future__ import annotations

import config
from models.endpoint_errors import EndpointError
from models.endpoint_spec import EndpointSpec
from services import endpoint_client

__all__ = ["list_host_models", "HOST_TIMEOUT_S"]

HOST_TIMEOUT_S = 3  # seconds a host may take to list its models before it counts as not answering


def list_host_models(provider: str,
                     base_url: str) -> list[str] | None:
    """The model ids the server at `base_url` lists, None when it does not answer. `provider` names the kind of host the
    caller shows it under; every host is asked the same way."""

    host = config.settings.find_host_by_url(base_url)
    endpoint = EndpointSpec(base_url=base_url, api_key_env=host.api_key_env if host else "", timeout_s=HOST_TIMEOUT_S)
    try:
        return endpoint_client.list_served_models(endpoint=endpoint)
    except EndpointError:
        # a host that is off, or answers something else: reported as not answering, which is what its readers show
        return None
