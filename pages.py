"""The site's pages (static/*.html), sent so a browser checks for a newer copy each time: after a
deploy, everyone gets the new page at once."""
from flask import current_app, send_from_directory


def page(name):
    """The page `name` (e.g. "sets.html")."""
    response = send_from_directory(current_app.static_folder, name)
    response.headers["Cache-Control"] = "no-cache"
    return response
