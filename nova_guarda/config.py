import os
from pathlib import Path


PORT = 3000
WEBHOOK_PATH = "/webhook"

ZAPI_BASE_URL = "https://api.z-api.io"
WHATSAPP_PROVIDER = os.getenv("WHATSAPP_PROVIDER", "zapi").strip().lower()
WHATSAPP_OFFICIAL_BASE_URL = os.getenv("WHATSAPP_OFFICIAL_BASE_URL", "https://graph.facebook.com/v20.0").strip().rstrip("/")
WHATSAPP_OFFICIAL_PHONE_NUMBER_ID = os.getenv("WHATSAPP_OFFICIAL_PHONE_NUMBER_ID", "").strip()
WHATSAPP_OFFICIAL_TOKEN = os.getenv("WHATSAPP_OFFICIAL_TOKEN", "").strip()
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "").strip()
TERMS_DOCUMENT_URL = os.getenv("TERMS_DOCUMENT_URL", "").strip()
# Templates aprovados na Meta, usados para iniciar conversa fora da janela de 24h.
WHATSAPP_TEMPLATE_LANGUAGE = os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "pt_BR").strip()
WHATSAPP_TEMPLATE_TERMS = os.getenv("WHATSAPP_TEMPLATE_TERMS", "nova_guarda_ativacao_termo").strip()
# Template da escala do período: cabeçalho DOCUMENTO (PDF da escala), corpo com
# {{1}} nome, {{2}} período, {{3}} dias de trabalho, e botões Confirmar / Recusar.
WHATSAPP_TEMPLATE_BOOKING = os.getenv("WHATSAPP_TEMPLATE_BOOKING", "nova_guarda_escala_periodo_v1").strip()
WHATSAPP_TEMPLATE_CHECKIN = os.getenv("WHATSAPP_TEMPLATE_CHECKIN", "nova_guarda_checkin_atendimento_v2").strip()
WHATSAPP_TEMPLATE_CHECKOUT = os.getenv("WHATSAPP_TEMPLATE_CHECKOUT", "nova_guarda_checkout_atendimento_v3").strip()
DEFAULT_PUBLIC_BASE_URL = "https://testezapi.77indicadores.com.br/webhook"

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
LOCAL_NGROK_PATH = PROJECT_DIR / "ngrok.exe"
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE_URL).strip().rstrip("/")
TERMS_PDF_PATH = Path(os.getenv("TERMS_PDF_PATH", PROJECT_DIR / "termo_uso_consentimento_nova_guarda.pdf"))
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", PROJECT_DIR / "nova_guarda.sqlite3"))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DEV_FAKE_ZAPI = os.getenv("DEV_FAKE_ZAPI", "false").strip().lower() in {"1", "true", "yes", "sim"}
DEV_FAKE_GESTAO77 = os.getenv("DEV_FAKE_GESTAO77", "false").strip().lower() in {"1", "true", "yes", "sim"}

TERMS_ACCEPTANCE_TEXT = "li e aceito os termos"
TERMS_REJECTION_TEXT = "não aceito os termos"

# Fixtures de teste já confirmadas como válidas na 77Gestão real (cliente e
# serviço de teste), usadas pelo modo teste assistido para criar cooperado +
# appointment de verdade lá quando gestao77_mode != fake.
GESTAO77_TEST_CUSTOMER_ID = os.getenv("GESTAO77_TEST_CUSTOMER_ID", "7957").strip()
GESTAO77_TEST_SERVICE_ID = os.getenv("GESTAO77_TEST_SERVICE_ID", "367").strip()
