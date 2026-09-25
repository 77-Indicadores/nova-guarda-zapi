# Nova Guarda - Webhook Z-API com Flask e ngrok

Projeto Python que inicia um servidor Flask na porta `3000`, abre automaticamente um tunel HTTPS com ngrok e atualiza o webhook da Z-API sempre que o sistema iniciar.

## O que o sistema faz

1. Carrega as variaveis de ambiente do arquivo `.env`.
2. Abre um tunel ngrok apontando para `http://localhost:3000`.
3. Captura a URL publica HTTPS gerada pelo ngrok.
4. Atualiza o webhook da Z-API para `https://URL_DO_NGROK/webhook`.
5. Mantem o Flask escutando eventos recebidos em `POST /webhook`.

## Arquitetura atual

O projeto agora usa um ponto de entrada fino em `app.py` e mantém o código da aplicação dentro do pacote `nova_guarda`.

O fluxo completo da solução está documentado em:

- `docs/arquitetura/fluxo-completo-nova-guarda-standalone.html`
- `docs/arquitetura/fluxo-completo-nova-guarda.html`

```text
app.py                         # entrada local/Gunicorn
nova_guarda/
  config.py                    # constantes, paths e envs
  state.py                     # estado em memória dos fluxos atuais
  routes.py                    # rotas HTTP e simulador local preservado
  flows.py                     # regras puras de agenda/check-in/termos
  messages.py                  # parsing e montagem de mensagens
  services.py                  # orquestra Z-API, estado e futuros syncs
  simulator.py                 # HTML do simulador usado em desenvolvimento
  clients/
    zapi.py                    # cliente HTTP da Z-API
    gestao77.py                # cliente HTTP preparado para 77gestão
    geocoding.py               # reverse geocode para localização
```

A integração com 77gestão fica isolada no client e no serviço de sincronização; as rotas e o webhook só orquestram os fluxos.

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
GESTAO77_BASE_URL=https://app.77gestao.com.br/api/v1
GESTAO77_EMAIL=seu_email_aqui
GESTAO77_PASSWORD=sua_senha_aqui
GESTAO77_TOKEN=
DEV_FAKE_ZAPI=true
```

Também é possível preencher `GESTAO77_TOKEN` diretamente. Se `GESTAO77_TOKEN` estiver vazio, o sistema faz login em `/api/v1/login` usando `GESTAO77_EMAIL` e `GESTAO77_PASSWORD`.

O provedor de WhatsApp é escolhido por `WHATSAPP_PROVIDER`:

```text
WHATSAPP_PROVIDER=zapi
WHATSAPP_PROVIDER=official
```

Com `zapi`, usa os tokens `ZAPI_*`. Com `official`, usa a Cloud API da Meta com `WHATSAPP_OFFICIAL_PHONE_NUMBER_ID`, `WHATSAPP_OFFICIAL_TOKEN` e `WHATSAPP_VERIFY_TOKEN`.

## Como executar

Com o ambiente virtual ativado:

```powershell
python app.py
```

## Chat dev local

Para testar os fluxos sem WhatsApp/Z-API real, rode o app, entre no painel com `ADMIN_USER`/`ADMIN_PASSWORD` e abra:

```text
http://127.0.0.1:3000/dev/chat
```

Essa tela simula mensagens recebidas do cooperado e processa pelo mesmo caminho do webhook real. Os envios automáticos da Z-API são forçados como fake dentro dessa rota dev, então ela não dispara mensagem real.

## 77Gestão

### Onboarding e gate local

O cooperado só entra no fluxo operacional depois de iniciar a conversa pelo WhatsApp/Chat Dev com `ativar`, `iniciar` ou `começar`, ser validado na 77Gestão por telefone em `phones[]` e aceitar o termo.

Estados locais do onboarding:

```text
not_started -> terms_sent -> accepted
                         -> rejected
```

`rejected` fica bloqueado por enquanto, sem reaceite automático.

### Escala

Estados locais da escala:

```text
pending -> sent -> confirmed
              -> declined
```

Ordem de envio:

```text
buscar/importar escala -> enviar WhatsApp com sucesso -> persistir local_status=sent -> POST /api/v1/bookings/{id}/schedule-response {"status":"sent"}
```

Ordem de resposta:

```text
resposta do cooperado -> validar local_status=sent -> persistir confirmed/declined -> POST /api/v1/bookings/{id}/schedule-response {"status":"confirmed"|"declined"}
```

Eventos duplicados são idempotentes. Eventos fora de ordem, como tentar transformar `declined` em `confirmed`, são bloqueados até existir uma regra explícita.

### Check-in e check-out

Estados locais de presença:

```text
sent/confirmed -> checkin_pending -> checked_in -> checkout_pending -> checked_out
```

O check-in só é enviado para cooperado com termo `accepted`, booking local existente e appointment pertencente ao booking informado. O botão carrega o `appointment_id` no payload (`checkin_arrived:{appointment_id}`) para impedir que clique antigo altere outro appointment.

Ordem de check-in:

```text
validar cooperado/booking/appointment -> enviar WhatsApp com sucesso -> persistir checkin_pending -> resposta válida -> persistir checked_in -> POST /api/v1/appointments/{id}/status {"status":"checked_in","address":""}
```

Ordem de check-out:

```text
validar checked_in -> enviar botão de check-out -> persistir checkout_pending -> resposta válida -> persistir checked_out -> POST /api/v1/appointments/{id}/status {"status":"checked_out","address":""}
```

`checked_out` antes de `checked_in` é bloqueado. Novo `checked_in` depois de `checked_out` também é bloqueado. Falha da 77Gestão preserva o estado local para retry; falha no provider não avança o estado.

### Atraso e não comparecimento

Atraso e não vou são eventos somente locais. Eles não chamam a 77Gestão e não enviam `declined`, `checked_in`, `checked_out` ou qualquer outro status operacional externo.

Estados locais:

```text
checkin_pending -> late_reported
checkin_pending -> no_show_reported
```

O botão carrega o `appointment_id` (`late_15:{appointment_id}`, `late_30:{appointment_id}`, `late_60:{appointment_id}`, `reason_personal:{appointment_id}`, etc.) para impedir evento antigo de outro appointment. Atraso persiste `late_minutes`; não comparecimento persiste `no_show_reason`.

### Retry de integrações

### Automação operacional

O painel possui um poller configurável em `/configuracoes`. Ele é a rotina normal de operação:

```text
consultar 77Gestão -> importar bookings pendentes -> validar cooperado accepted -> enviar escala -> sincronizar sent -> avaliar check-in/check-out -> retry de pendências
```

Parâmetros controlados pela interface:

- `Poller ativo`: liga/desliga o ciclo automático.
- `Intervalo em minutos`: frequência do ciclo.
- `Limite por ciclo`: limite de escalas enviadas por execução.
- `Disparar check-in/check-out automático`: liga/desliga presença automática.
- `Check-in antes`: quantos minutos antes do horário do appointment o sistema pode enviar o check-in.
- `Check-out depois`: quantos minutos depois do horário de referência o sistema pode enviar o check-out.

O ciclo respeita `America/Sao_Paulo`. Check-in só é enviado quando o booking local está `sent` ou `confirmed`, tem `appointment_id`, telefone, cooperado com termo `accepted` e está dentro da janela configurada. Check-out só é enviado para appointment local `checked_in`; a confirmação do cooperado continua vindo pelo botão do WhatsApp e só então sincroniza `checked_out` com a 77Gestão.

Também existe o botão administrativo `Executar ciclo agora` no painel. Ele não substitui a operação automática; serve para apresentação, homologação e troubleshooting.

### Roteiro de apresentação

1. Iniciar o app e abrir `http://127.0.0.1:3000`.
2. Entrar em `/configuracoes`.
3. Selecionar provider WhatsApp (`Z-API` ou `Meta Cloud oficial`), modo 77Gestão e deixar `Poller ativo`.
4. Definir janelas curtas para apresentação, se necessário, por exemplo check-in 1440 minutos antes e check-out 1 minuto depois.
5. O cooperado envia `ativar` pelo WhatsApp real ou pelo Chat Dev.
6. O sistema valida telefone em `phones[]` na 77Gestão, pede para salvar o contato, envia o PDF do termo e exibe botões de aceite/recusa.
7. Com aceite registrado, o cooperado fica `accepted`.
8. A 77Gestão deve ter uma escala/booking pendente para esse cooperado e telefone.
9. Rodar `Executar ciclo agora` ou aguardar o poller.
10. O sistema envia a escala pelo WhatsApp e sincroniza `sent`.
11. O cooperado confirma ou recusa; o webhook sincroniza `confirmed` ou `declined`.
12. Dentro da janela configurada, o poller envia o check-in.
13. O cooperado responde `Sim, cheguei`, `Vou atrasar` ou `Não vou`.
14. `Sim, cheguei` sincroniza `checked_in`; atraso e não vou ficam somente locais.
15. Após a janela de check-out, o poller envia o botão de check-out.
16. O cooperado finaliza e o sistema sincroniza `checked_out`.
17. Acompanhar tudo em `/cooperados`, `/escalas` e `/registros`.

Retry manual de pendências:

```http
POST /api/gestao77/retry-pending-syncs
```

A rotina tenta sincronizar novamente somente registros com divergência entre estado local e `gestao77_status`:

```text
bookings: sent, confirmed, declined
appointments: checked_in, checked_out
```

Registros já sincronizados não são reenviados.

Consulta de escalas pendentes do parceiro:

```http
POST /api/gestao77/pending-bookings
Content-Type: application/json
```

Body:

```json
{
  "month": 8,
  "year": 2026
}
```

O sistema chama a 77Gestão com `GET`:

```http
GET https://app.77gestao.com.br/api/v1/bookings/summary/partner?month=8&year=2026&skipLoader=1
```

E filtra `cooperative_members` com `schedule_status` em:

```text
awaiting_send
awaiting_approval
```

Depois da importação, o sistema consulta `GET /partners/{id}` para buscar telefone e `GET /appointments?booking_id={booking_id}` para buscar os agendamentos do mês. O check-in usa o appointment do dia em `America/Sao_Paulo`; se não houver appointment hoje, o envio manual pode informar o `appointment_id`. Os registros ficam no SQLite local (`DATABASE_PATH`, por padrão `nova_guarda.sqlite3`) para guardar o que a 77Gestão não expõe diretamente no fluxo do WhatsApp: enviado, confirmado, recusado e eventos de sincronização.

Enviar uma escala importada para o cooperado:

```http
POST /api/bookings/{booking_id}/send
Content-Type: application/json
```

```json
{
  "phone": "5513999999999"
}
```

Ao enviar, o sistema dispara a mensagem com botões pela Z-API e chama:

```http
POST /api/v1/bookings/{id}/schedule-response
```

com `status=sent`. Quando o cooperado confirmar ou recusar pelo WhatsApp, o webhook atualiza a 77Gestão com `confirmed` ou `declined`.

Importar pendentes e enviar em lote:

```http
POST /api/gestao77/import-and-send-pending
Content-Type: application/json
```

```json
{
  "month": 8,
  "year": 2026,
  "phone_by_booking": {
    "1": "5513999999999"
  }
}
```

Disparo técnico de check-in de um agendamento:

```http
POST /api/appointments/{appointment_id}/send-checkin
Content-Type: application/json
```

```json
{
  "phone": "5513999999999",
  "mode": "checkin2",
  "client_name": "Cooperado Teste",
  "client_address": "Rua Exemplo, 123 - Santos/SP",
  "schedule_date": "20/05/2026",
  "schedule_time": "15:00",
  "service": "Atendimento da cooperativa"
}
```

Quando o cooperado confirma chegada ou envia localização, o webhook chama:

```http
POST /api/v1/appointments/{id}/status
```

com `status=checked_in`. Para check-out manual:

```http
POST /api/appointments/{appointment_id}/checkout
Content-Type: application/json
```

```json
{
  "address": "opcional"
}
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
