import logging
import sys
import time

class PrettyFormatter(logging.Formatter):
    """
    Formatter que adiciona cores ANSI e emojis aos logs do Backend.
    """
    # Cores ANSI
    grey = "\x1b[38;20m"
    green = "\x1b[32;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    cyan = "\x1b[36;20m"
    reset = "\x1b[0m"

    emoji_map = {
        logging.DEBUG: "🐛",
        logging.INFO: "ℹ️ ",
        logging.WARNING: "⚠️ ",
        logging.ERROR: "🚨",
        logging.CRITICAL: "🔥"
    }

    color_map = {
        logging.DEBUG: grey,
        logging.INFO: green,
        logging.WARNING: yellow,
        logging.ERROR: red,
        logging.CRITICAL: bold_red
    }

    def format(self, record):
        color = self.color_map.get(record.levelno, self.reset)
        emoji = self.emoji_map.get(record.levelno, "")
        
        # Formata Time (apenas hora para dev)
        asctime = self.formatTime(record, "%H:%M:%S")
        
        # Mensagem e Nome do Módulo
        message = record.getMessage()
        name = record.name.replace("sgm_marketfy.", "").replace("infra.web.", "")

        # Layout: [Hora] EMOJI LEVEL [Modulo] Mensagem
        log_fmt = (
            f"{self.grey}[{asctime}]{self.reset} "
            f"{emoji} {color}{record.levelname:<8}{self.reset} "
            f"{self.cyan}[{name}]{self.reset} {message}"
        )

        if record.exc_info:
            text = super().formatException(record.exc_info)
            log_fmt += f"\n{self.red}{text}{self.reset}"

        return log_fmt

def get_logger(name: str):
    """Retorna uma instância de logger configurada."""
    logger = logging.getLogger(f"sgm_marketfy.{name}")
    logger.setLevel(logging.DEBUG)
    
    # Evita adicionar múltiplos handlers se já existir
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(PrettyFormatter())
        logger.addHandler(handler)
    
    # Não propaga para o root logger do Uvicorn para evitar duplicidade feia
    logger.propagate = False
    
    return logger