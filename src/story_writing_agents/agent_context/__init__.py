"""Internal provider-neutral agent-context contracts."""

__all__ = [
    "ContextComponentBytes",
    "ContextComponentKind",
    "JsonPath",
    "ProviderCallAccounting",
    "ProviderCallKind",
    "ProviderCallState",
    "ProviderJsonEncodingError",
    "ProviderPayloadAccounting",
    "ProviderStreamEvent",
    "ProviderStreamEventKind",
    "ProviderStreamSink",
    "ProviderUsage",
    "account_provider_payload",
    "active_provider_stream_sink",
    "bind_provider_stream_sink",
    "canonical_provider_json",
    "reset_provider_stream_sink",
]



def __getattr__(name: str) -> object:
    """Resolve one declared public owner without importing unrelated runtime state."""
    if name in (
        'ContextComponentBytes',
        'ContextComponentKind',
        'JsonPath',
        'ProviderCallAccounting',
        'ProviderCallKind',
        'ProviderCallState',
        'ProviderJsonEncodingError',
        'ProviderPayloadAccounting',
        'ProviderUsage',
        'account_provider_payload',
        'canonical_provider_json',
    ):
        from .accounting import (
            ContextComponentBytes,
            ContextComponentKind,
            JsonPath,
            ProviderCallAccounting,
            ProviderCallKind,
            ProviderCallState,
            ProviderJsonEncodingError,
            ProviderPayloadAccounting,
            ProviderUsage,
            account_provider_payload,
            canonical_provider_json,
        )
        names = (
            'ContextComponentBytes',
            'ContextComponentKind',
            'JsonPath',
            'ProviderCallAccounting',
            'ProviderCallKind',
            'ProviderCallState',
            'ProviderJsonEncodingError',
            'ProviderPayloadAccounting',
            'ProviderUsage',
            'account_provider_payload',
            'canonical_provider_json',
        )
        values = (
            ContextComponentBytes,
            ContextComponentKind,
            JsonPath,
            ProviderCallAccounting,
            ProviderCallKind,
            ProviderCallState,
            ProviderJsonEncodingError,
            ProviderPayloadAccounting,
            ProviderUsage,
            account_provider_payload,
            canonical_provider_json,
        )
    elif name in (
        'ProviderStreamEvent',
        'ProviderStreamEventKind',
        'ProviderStreamSink',
        'active_provider_stream_sink',
        'bind_provider_stream_sink',
        'reset_provider_stream_sink',
    ):
        from .progress import (
            ProviderStreamEvent,
            ProviderStreamEventKind,
            ProviderStreamSink,
            active_provider_stream_sink,
            bind_provider_stream_sink,
            reset_provider_stream_sink,
        )
        names = (
            'ProviderStreamEvent',
            'ProviderStreamEventKind',
            'ProviderStreamSink',
            'active_provider_stream_sink',
            'bind_provider_stream_sink',
            'reset_provider_stream_sink',
        )
        values = (
            ProviderStreamEvent,
            ProviderStreamEventKind,
            ProviderStreamSink,
            active_provider_stream_sink,
            bind_provider_stream_sink,
            reset_provider_stream_sink,
        )
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals().update(zip(names, values))
    return globals()[name]


def __dir__() -> list[str]:
    """Include unresolved public names in ordinary module introspection."""
    return sorted(set(globals()) | set(__all__))
