"""Gunicorn hooks for the production TurBot process.

Gunicorn automatically reads ./gunicorn.conf.py from the working directory.
The systemd unit can therefore keep its long-standing ``bot:app`` entrypoint
while this hook registers the website lead routes after the Flask app has been
loaded and before the worker starts serving requests.
"""


def post_worker_init(worker):
    # MDT's live add-lead response can report success via a top-level `result`
    # without returning a numeric lead id. Keep the compatibility layer for the
    # legacy Website lead path and for safe interpretation of live responses.
    from shared import mdt_live_compat

    mdt_live_compat.install()

    # Import for the registration side effect. website_app reuses bot.app,
    # creates its local-first table, and starts one retry worker per gunicorn
    # worker. Production intentionally runs exactly one worker.
    import website_app

    # The Website path now writes structured MDT preorders so country, travel
    # dates and (when unambiguous) manager assignment populate CRM columns. The
    # installer adds durable tourist/preorder checkpoints before replacing the
    # delivery function used by the retry worker.
    from shared import website_mdt_preorder

    website_mdt_preorder.install(website_app)
