import os
import sys

import uvicorn


if __name__ == "__main__":
    sys.path.append(os.path.join(os.path.dirname(__file__), "app"))

    port = int(os.environ.get("PORT", 8000))

    uvicorn.run(
        "infra.web.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
