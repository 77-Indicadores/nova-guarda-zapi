# Nova Guarda - Webhook Z-API com Flask e ngrok

Projeto Python que inicia um servidor Flask na porta `3000`, abre automaticamente um tunel HTTPS com ngrok e atualiza o webhook da Z-API sempre que o sistema iniciar.

## O que o sistema faz

1. Carrega as variaveis de ambiente do arquivo `.env`.
2. Abre um tunel ngrok apontando para `http://localhost:3000`.
3. Captura a URL publica HTTPS gerada pelo ngrok.
4. Atualiza o webhook da Z-API para `https://URL_DO_NGROK/webhook`.
5. Mantem o Flask escutando eventos recebidos em `POST /webhook`.

## Requisitos

- Python 3.10 ou superior
- Conta no ngrok com `NGROK_AUTHTOKEN`
- Credenciais da Z-API:
  - `ZAPI_INSTANCE_ID`
  - `ZAPI_INSTANCE_TOKEN`
  - `ZAPI_CLIENT_TOKEN`

## Instalação

No terminal, dentro desta pasta:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Crie o arquivo `.env` a partir do exemplo:

```powershell
Copy-Item .env.example .env
```

Depois edite o arquivo `.env` com suas credenciais reais:

```env
ZAPI_INSTANCE_ID=sua_instancia_aqui
ZAPI_INSTANCE_TOKEN=seu_token_da_instancia_aqui
ZAPI_CLIENT_TOKEN=seu_client_token_aqui
NGROK_AUTHTOKEN=seu_authtoken_do_ngrok_aqui
```

## Como executar

Com o ambiente virtual ativado:

```powershell
python app.py
```

Ao iniciar, o terminal exibira:

- URL publica HTTPS do ngrok
- Status da atualizacao do webhook na Z-API
- Payloads recebidos em `POST /webhook`
- Erros de inicializacao ou comunicacao com a API

## Endpoint local

```http
POST http://localhost:3000/webhook
```

O corpo recebido sera registrado nos logs e o servidor respondera:

```json
{
  "received": true
}
```

## Observacoes importantes

- O arquivo `.env` fica fora do versionamento por seguranca.
- O Flask roda com `use_reloader=False` para evitar abrir dois tuneis ngrok durante o desenvolvimento.
- Toda vez que o sistema iniciar, uma nova URL ngrok pode ser criada e enviada automaticamente para a Z-API.
