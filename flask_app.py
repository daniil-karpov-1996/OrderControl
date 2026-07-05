import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.join(_ROOT, "app")
if _APP not in sys.path:
    sys.path.insert(0, _APP)

from wsgi_app import create_app
from settings import APP_DEBUG, APP_PORT

application = create_app()

if __name__ == "__main__":
    application.run(host="127.0.0.1", port=APP_PORT, debug=APP_DEBUG)
