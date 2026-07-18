"""Service layer — business logic shared by routes, the scheduler, and the
orchestrator.

Routes stay thin HTTP adapters; anything a cron tick or another module also
needs lives here, so the dependency arrows point routes→services instead of
scheduler→routes."""
