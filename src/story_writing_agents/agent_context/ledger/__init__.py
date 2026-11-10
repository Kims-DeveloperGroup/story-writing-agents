"""Public ledger owners, loaded only when requested."""

__all__ = ['CONTEXT_LEDGER_SCHEMA_VERSION', 'MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES', 'MAX_CONTEXT_LEDGER_SOURCES', 'MAX_CONTEXT_LEDGER_PROVIDER_CALLS', 'CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS', 'ContextLedger', 'ContextLedgerKey', 'ContextLedgerRecordState', 'ContextLedgerAttemptOutcome', 'ContextLedgerAttemptPlan', 'ContextLedgerProviderCall', 'ContextLedgerAttempt', 'ContextLedgerReadyCheckpoint', 'ContextLedgerRecord', 'ContextLedgerInspection', 'ContextLedgerCommitAuthorization', 'ContextLedgerError', 'ContextLedgerErrorCode', 'context_checkpoint_proposal_digest', 'context_ledger_scope_key']


def __getattr__(name: str) -> object:
    """Resolve one declared owner without loading unrelated state."""
    if name in ('CONTEXT_LEDGER_SCHEMA_VERSION', 'MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES', 'MAX_CONTEXT_LEDGER_SOURCES', 'MAX_CONTEXT_LEDGER_PROVIDER_CALLS', 'CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS', 'ContextLedgerError', 'ContextLedgerErrorCode'):
        from .record_validation import (
            CONTEXT_LEDGER_SCHEMA_VERSION,
            MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES,
            MAX_CONTEXT_LEDGER_SOURCES,
            MAX_CONTEXT_LEDGER_PROVIDER_CALLS,
            CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS,
            ContextLedgerError,
            ContextLedgerErrorCode,
        )
        names = ('CONTEXT_LEDGER_SCHEMA_VERSION', 'MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES', 'MAX_CONTEXT_LEDGER_SOURCES', 'MAX_CONTEXT_LEDGER_PROVIDER_CALLS', 'CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS', 'ContextLedgerError', 'ContextLedgerErrorCode')
        values = (CONTEXT_LEDGER_SCHEMA_VERSION, MAX_CONTEXT_LEDGER_RECORD_UTF8_BYTES, MAX_CONTEXT_LEDGER_SOURCES, MAX_CONTEXT_LEDGER_PROVIDER_CALLS, CONTEXT_LEDGER_LOCK_TIMEOUT_SECONDS, ContextLedgerError, ContextLedgerErrorCode)
    elif name in ('ContextLedger',):
        from .checkpoint_ledger import ContextLedger
        names = ('ContextLedger',)
        values = (ContextLedger,)
    elif name in ('ContextLedgerKey', 'ContextLedgerRecordState', 'ContextLedgerAttemptOutcome', 'ContextLedgerAttemptPlan', 'ContextLedgerProviderCall', 'ContextLedgerAttempt', 'ContextLedgerReadyCheckpoint', 'ContextLedgerRecord', 'ContextLedgerInspection', 'ContextLedgerCommitAuthorization', 'context_ledger_scope_key'):
        from .record_values import (
            ContextLedgerKey,
            ContextLedgerRecordState,
            ContextLedgerAttemptOutcome,
            ContextLedgerAttemptPlan,
            ContextLedgerProviderCall,
            ContextLedgerAttempt,
            ContextLedgerReadyCheckpoint,
            ContextLedgerRecord,
            ContextLedgerInspection,
            ContextLedgerCommitAuthorization,
            context_ledger_scope_key,
        )
        names = ('ContextLedgerKey', 'ContextLedgerRecordState', 'ContextLedgerAttemptOutcome', 'ContextLedgerAttemptPlan', 'ContextLedgerProviderCall', 'ContextLedgerAttempt', 'ContextLedgerReadyCheckpoint', 'ContextLedgerRecord', 'ContextLedgerInspection', 'ContextLedgerCommitAuthorization', 'context_ledger_scope_key')
        values = (ContextLedgerKey, ContextLedgerRecordState, ContextLedgerAttemptOutcome, ContextLedgerAttemptPlan, ContextLedgerProviderCall, ContextLedgerAttempt, ContextLedgerReadyCheckpoint, ContextLedgerRecord, ContextLedgerInspection, ContextLedgerCommitAuthorization, context_ledger_scope_key)
    elif name in ('context_checkpoint_proposal_digest',):
        from .checkpoint_wire import context_checkpoint_proposal_digest
        names = ('context_checkpoint_proposal_digest',)
        values = (context_checkpoint_proposal_digest,)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals().update(zip(names, values))
    return globals()[name]


def __dir__() -> list[str]:
    """Include every declared public name before lazy resolution."""
    return sorted(set(globals()) | set(__all__))
