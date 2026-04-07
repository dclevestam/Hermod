"""GNOME Online Accounts source adapter."""

import gi
gi.require_version('Goa', '1.0')
from gi.repository import Goa

try:
    from ..descriptors import AccountDescriptor
except ImportError:
    from accounts.descriptors import AccountDescriptor


_GOA_PROVIDER_KIND_MAP = {
    'google': 'gmail',
    'ms_graph': 'microsoft-graph',
}


def descriptor_from_goa_object(goa_obj):
    if goa_obj is None:
        return None
    account = goa_obj.get_account()
    if account is None or account.props.mail_disabled:
        return None
    provider_type = str(account.props.provider_type or '').strip()
    provider_kind = _GOA_PROVIDER_KIND_MAP.get(provider_type)
    if not provider_kind:
        return None
    if not goa_obj.get_oauth2_based():
        return None
    identity = str(account.props.presentation_identity or '').strip()
    if not identity:
        return None
    return AccountDescriptor(
        source='goa',
        provider_kind=provider_kind,
        identity=identity,
        presentation_name=identity,
        auth_kind='goa-oauth2',
        metadata={
            'goa_provider_type': provider_type,
        },
        source_obj=goa_obj,
    )


def get_goa_account_descriptors(client=None):
    client = client or Goa.Client.new_sync(None)
    descriptors = []
    for goa_obj in client.get_accounts():
        descriptor = descriptor_from_goa_object(goa_obj)
        if descriptor is not None:
            descriptors.append(descriptor)
    return descriptors
