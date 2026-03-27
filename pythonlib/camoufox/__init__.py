from .addons import DefaultAddons
from .async_api import AsyncCamoufox, AsyncNewBrowser, AsyncNewContext
from .rdp_api import RDPBrowser, RDPPage
from .sync_api import Camoufox, NewBrowser, NewContext
from .utils import launch_options

__all__ = [
    "Camoufox",
    "NewBrowser",
    "NewContext",
    "AsyncCamoufox",
    "AsyncNewBrowser",
    "AsyncNewContext",
    "RDPBrowser",
    "RDPPage",
    "DefaultAddons",
    "launch_options",
]
