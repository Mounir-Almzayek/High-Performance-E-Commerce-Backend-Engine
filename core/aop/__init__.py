"""
core.aop - Aspect-Oriented Programming primitives.

Cross-cutting concerns are kept out of business logic:
 - decorators.py : @timed, @audit_log, @count_calls
 - middleware.py : PerformanceMiddleware (per-request timing + instance tag)
 - signals.py    : pre_save / post_save audit hooks

Why split them out: business code stays clean, and toggling instrumentation
becomes a one-line change (remove a decorator or unregister a middleware).
"""
