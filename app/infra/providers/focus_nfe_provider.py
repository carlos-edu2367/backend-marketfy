import httpx
import json
import base64
from domain.interfaces import FiscalProviderInterface
from domain.fiscal import FiscalConfig, FiscalEnvironment
from infra.config.logger import get_logger

logger = get_logger("fiscal_provider")

class FocusNFeProvider(FiscalProviderInterface):
    """
    Implementação da integração com a API V2 da Focus NFe.
    Documentação: https://focusnfe.com.br/doc/
    """
    
    def __init__(self, api_token: str = None):
        # Em prod, usar variável de ambiente FOCUS_API_TOKEN
        self.api_token = api_token or "TOKEN_DA_PLATAFORMA_SGM" 
        self.base_url_homolog = "https://homologacao.focusnfe.com.br/v2"
        self.base_url_prod = "https://api.focusnfe.com.br/v2"

    def _get_url(self, env: FiscalEnvironment) -> str:
        return self.base_url_prod if env == FiscalEnvironment.PRODUCTION else self.base_url_homolog

    def _get_auth(self, config: FiscalConfig):
        # A Focus usa Basic Auth com o Token no username
        return (self.api_token, "")

    async def emit(self, sale_data: dict, config: FiscalConfig) -> dict:
        """
        Envia a requisição de forma ASSÍNCRONA para a API.
        """
        url = f"{self._get_url(config.environment)}/nfce"
        
        # Mapeia referência para evitar duplicidade
        reference = sale_data.get("ref")
        
        logger.info(f"Emitindo NFC-e ref={reference} para FocusNFe...")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    url + f"?ref={reference}", 
                    json=sale_data, 
                    auth=self._get_auth(config)
                )

            # Focus retorna 202 Accepted para processamento assíncrono ou 200/201
            if response.status_code in [200, 201, 202]:
                data = response.json()
                
                # Se for 202, a nota está "processando". 
                # Se for Síncrono (dependendo da config da Focus), já vem autorizada.
                # O ideal é checar o campo "status" no JSON.
                
                return {
                    "success": True,
                    "access_key": data.get("chave_nfe"), 
                    "protocol": data.get("protocolo"),
                    "number": data.get("numero"),
                    "xml_url": data.get("caminho_xml_nota_fiscal"),
                    "pdf_url": data.get("caminho_danfe"),
                    "status_api": data.get("status"),
                    "mensagem": data.get("mensagem_sefaz") or data.get("mensagem")
                }
            elif response.status_code in [400, 422]:
                # Erro de Validação
                err_data = response.json()
                msg = err_data.get("mensagem") or err_data.get("erros", [{}])[0].get("mensagem", "Erro de validação")
                logger.warning(f"Erro validação FocusNFe: {msg}")
                return {
                    "success": False,
                    "error": msg
                }
            else:
                logger.error(f"Erro FocusNFe HTTP {response.status_code}: {response.text}")
                return {
                    "success": False,
                    "error": f"Erro de comunicação: HTTP {response.status_code}"
                }

        except Exception as e:
            logger.error(f"Exceção na emissão fiscal: {str(e)}")
            return {"success": False, "error": str(e)}