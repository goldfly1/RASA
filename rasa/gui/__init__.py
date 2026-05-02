from rasa import __version__ as rasa_version
from rasa.gui.server import app

__all__ = ["app", "rasa_version"]


def run_gui(host: str = "127.0.0.1", port: int = 8400) -> None:
    import uvicorn
    uvicorn.run(
        "rasa.gui.server:app",
        host=host,
        port=port,
        log_level="info",
    )
