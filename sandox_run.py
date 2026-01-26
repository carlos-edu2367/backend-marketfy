import uvicorn
import os
import sys

if __name__ == "__main__":
    # Adiciona a pasta 'app' ao PYTHONPATH
    sys.path.append(os.path.join(os.path.dirname(__file__), "app"))

    # Define explicitamente o ambiente
    os.environ["ENV"] = "test"

    port = int(os.environ.get("TEST_PORT", 8000))

    print(f"🧪 Iniciando SGM Marketfy Backend (TESTE) na porta {port}...")

    uvicorn.run(
        "infra.web.main:app",
        host="127.0.0.1",
        port=port,
        reload=True,
        log_level="debug"
    )
