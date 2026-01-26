import uvicorn
import os
import sys


if __name__ == "__main__":
    # Adiciona a pasta 'app' ao PYTHONPATH
    sys.path.append(os.path.join(os.path.dirname(__file__), "app"))
    from app.infra.security.auth_handler import pwd_context

    port = int(os.environ.get("PORT", 8000))

    print(f"🚀 Iniciando SGM Marketfy Backend na porta {port}...")

    print("PASSLIB SCHEMES:", pwd_context.schemes())
    print("PASSLIB VERSION CHECK")


    uvicorn.run(
        "infra.web.main:app",
        host="0.0.0.0",
        port=port,
        reload=False
    )
