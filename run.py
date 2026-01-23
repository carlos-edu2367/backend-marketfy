import uvicorn
import os
import sys

if __name__ == "__main__":
    # Adiciona a pasta 'app' ao caminho do Python
    sys.path.append(os.path.join(os.path.dirname(__file__), "app"))
    
    # Inicia o servidor
    print("🚀 Iniciando SGM Marketfy Backend...")
    uvicorn.run("infra.web.main:app", host="127.0.0.1", port=8000, reload=True)