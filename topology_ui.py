from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.staticfiles import StaticFiles
from starlette.routing import Mount


_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def register_topology_ui(app: FastAPI, admin_dependency):
    """Register topology UI route and static assets without changing API contracts."""
    has_static_mount = any(isinstance(route, Mount) and route.path == "/static" for route in app.routes)
    if not has_static_mount:
        app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")

    @app.get("/ui/topology", response_class=HTMLResponse)
    async def ui_topology(request: Request, _=Depends(admin_dependency)):
        return _TEMPLATES.TemplateResponse("topology.html", {"request": request})
