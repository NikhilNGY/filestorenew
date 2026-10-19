from aiohttp import web
from .route import routes


async def web_server() -> web.Application:
    """
    Initializes the aiohttp web server for health checks
    and README status rendering.
    """
    web_app = web.Application(client_max_size=30 * 1024 * 1024)
    web_app.add_routes(routes)
    return web_app
